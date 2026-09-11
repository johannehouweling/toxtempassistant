import difflib
import json
import logging
import random
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product

import requests
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordResetView as DjangoPasswordResetView
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.db import models, transaction
from django.db.models import QuerySet, Sum
from django.http import (
    FileResponse,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
)
from django.http.response import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.safestring import mark_safe
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django_q.tasks import async_task
from django_tables2 import SingleTableView
from guardian.shortcuts import get_objects_for_user
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from openai import BadRequestError, RateLimitError
from tqdm.auto import tqdm

from myocyte import settings
from toxtempass import config
from toxtempass import utilities as beta_util
from toxtempass.azure_registry import get_model as get_azure_model
from toxtempass.export import export_assay_to_file
from toxtempass.filehandling import (
    collect_source_documents,
    get_text_or_imagebytes_from_django_uploaded_file,
    split_doc_dict_by_type,
    store_files_to_storage,
    stringyfy_text_dict,
    summarize_image_entries,
    truncate_context_to_token_limit,
)
from toxtempass.forms import (
    AssayAnswerForm,
    AssayForm,
    InvestigationForm,
    LoginForm,
    SignupForm,
    SignupFormOrcid,
    StartingForm,
    StudyForm,
)
from toxtempass.llm import (
    current_llm_key,
    get_llm,
    get_llm_for_endpoint,
    resolve_user_llm,
)
from toxtempass.models import (
    Answer,
    AnswerFile,
    Assay,
    AssayCost,
    AssayTimeLog,
    Feedback,
    Investigation,
    LLMStatus,
    Person,
    Question,
    QuestionSet,
    RiskHunt3rLabel,
    Section,
    Study,
    Subsection,
)
from toxtempass.tables import AssayTable
from toxtempass.utilities import (
    add_user_alert,
    get_password_reset_wait_seconds,
    log_processing_event,
    provenance_label_for_item,
    record_password_reset_attempt,
    update_prefs_atomic,
)

logger = logging.getLogger("views")

# Rate-limit handling must cover every provider, not just OpenAI. The OpenAI SDK
# (used by ChatOpenAI / AzureChatOpenAI — so gpt-4o-mini, gpt-5.4*, Llama, and the
# OpenAI-compatible Mistral/Kimi/DeepSeek) raises openai.RateLimitError on a 429.
# Anthropic raises its OWN RateLimitError (not a subclass), so it must be added
# explicitly or Claude 429s slip through to a generic handler and return empty.
_RATE_LIMIT_ERRORS: tuple[type[Exception], ...] = (RateLimitError,)
try:
    from anthropic import RateLimitError as _AnthropicRateLimitError

    _RATE_LIMIT_ERRORS = (RateLimitError, _AnthropicRateLimitError)
except ImportError:  # pragma: no cover - anthropic is an optional provider
    pass

# TRANSIENT errors (timeouts, dropped connections, 5xx) are worth retrying with a
# short exponential backoff rather than being swallowed as empty answers — under
# load Claude/OpenAI requests can read-timeout even when they'd succeed on retry.
_TRANSIENT_ERRORS: tuple[type[Exception], ...] = ()
try:
    import httpx

    _TRANSIENT_ERRORS += (httpx.TimeoutException, httpx.TransportError)
except ImportError:  # pragma: no cover
    pass
try:
    from openai import APIConnectionError, APITimeoutError, InternalServerError

    _TRANSIENT_ERRORS += (APITimeoutError, APIConnectionError, InternalServerError)
except ImportError:  # pragma: no cover
    pass
try:
    from anthropic import (
        APIConnectionError as _AnthConnErr,
    )
    from anthropic import (
        APITimeoutError as _AnthTimeoutErr,
    )
    from anthropic import (
        InternalServerError as _AnthISErr,
    )

    _TRANSIENT_ERRORS += (_AnthTimeoutErr, _AnthConnErr, _AnthISErr)
except ImportError:  # pragma: no cover
    pass

# BadRequest (HTTP 400) is NOT retryable — context-length overflow, malformed
# payload, or a billing/credit-balance error. Like the 429 case, Anthropic raises
# its OWN BadRequestError (not a subclass of openai's), so it must be added
# explicitly or Claude 400s slip past the dedicated handler into the generic
# `except Exception` and get logged as an opaque flood instead of a clear message.
_BAD_REQUEST_ERRORS: tuple[type[Exception], ...] = (BadRequestError,)
try:
    from anthropic import BadRequestError as _AnthropicBadRequestError

    _BAD_REQUEST_ERRORS = (BadRequestError, _AnthropicBadRequestError)
except ImportError:  # pragma: no cover - anthropic is an optional provider
    pass

MAX_TRANSIENT_RETRIES = 4  # cap so a permanently-failing request can't loop forever


# Login stuff
orcid_id_baseurl = "https://sandbox.orcid.org" if settings.DEBUG else "https://orcid.org"


# Custom test function to check if the user is a logged-in
def is_admin(user: User) -> bool:
    """Check is user is admin. Return True if that's the case."""
    return user.is_superuser


# Custom test function to check if the user is a superuser (admin)
def is_logged_in(user: Person | None) -> bool:
    """Check if user is logged in as a Person instance."""
    return bool(user and user.is_authenticated)


def is_beta_admitted(user: Person | None) -> bool:
    """Check if user is admitted to beta.

    If the user is not logged in, or not admitted, return False.
    Admins (superusers) always bypass this check.
    """
    if not isinstance(user, Person):
        return False
    # Allow superusers to bypass beta admission
    if user.is_superuser:
        return True
    prefs = getattr(user, "preferences", {}) or {}
    return prefs.get("beta_admitted", False)


def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    """Log out the current user and redirect to the login page."""
    logout(request)
    return redirect(reverse("login"))


class LoginView(View):
    """View to handle user login via GET and POST methods."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the login page with the login form if user is not authenticated."""
        if request.user.is_authenticated:
            return redirect(reverse("overview"))
        form = LoginForm()
        return render(request, "login.html", {"form": form})

    def post(self, request: HttpRequest) -> JsonResponse:
        """Process login form submission and authenticate user."""
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data.get("username")
            # Check if username looks like an ORCID id and get the email associated
            if "@" not in username:
                orcid_pattern = r"^\d{4}-\d{4}-\d{4}-\d{4}$"
                if re.match(orcid_pattern, username):
                    try:
                        username = Person.objects.get(orcid_id=username).email
                    except Person.DoesNotExist:
                        form.add_error(None, "Invalid credentials.")
                        return JsonResponse({"success": False, "errors": form.errors})
                else:
                    form.add_error(
                        None, "ORCID iD must be in the format '0000-0000-0000-0000'."
                    )
                    return JsonResponse({"success": False, "errors": form.errors})
            password = form.cleaned_data.get("password")
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                return JsonResponse(
                    {
                        "success": True,
                        "errors": form.errors,
                        "redirect_url": reverse("overview"),
                    }
                )
            else:
                form.add_error(None, "Invalid credentials.")
                return JsonResponse({"success": False, "errors": form.errors})
        else:
            return JsonResponse({"success": False, "errors": form.errors})


def signup(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Show normal signup view.

    If the user is not logged in, they can sign up.
    """
    if request.user.is_authenticated:
        return redirect(reverse("overview"))

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            # Create the user but ensure the ORCID id is set from the session.
            user = form.save(commit=False)
            user.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            # Mark the user as having requested beta and enqueue notification to mntnr.
            try:
                async_task("toxtempass.tasks.send_beta_signup_notification", user.id)
                beta_util.set_beta_requested(user)
            except Exception:
                logger.exception(
                    "Failed to record/queue beta signup notification for user %s",
                    getattr(user, "id", None),
                )
            return JsonResponse(
                dict(
                    success=True,
                    errors=form.errors,
                    redirect_url=reverse("overview"),
                )
            )
        else:
            return JsonResponse(
                {
                    "success": False,
                    "errors": form.errors,
                }
            )
    else:
        # Prefill the form with the ORCID id.
        form = SignupForm()

    return render(
        request,
        "signup.html",
        {
            "form": form,
            "ror_domain_lookup_min_query_length": (
                config.ror_domain_lookup_min_query_length
            ),
            "ror_general_lookup_min_query_length": (
                config.ror_general_lookup_min_query_length
            ),
        },
    )


@require_GET
def ror_organization_lookup(request: HttpRequest) -> JsonResponse:
    """Return organization suggestions from the public ROR API."""
    def _extract_email_domain(raw_email: str) -> str | None:
        value = (raw_email or "").strip().lower()
        if "@" not in value:
            return None
        _, _, domain = value.rpartition("@")
        domain = domain.strip(".")
        if not domain or "." not in domain:
            return None
        if ".." in domain or len(domain) > 253:
            return None
        # RFC 1035-style hostname check: labels start/end alphanumeric, may
        # contain internal hyphens, max 63 chars per label, and include at
        # least one dot-separated suffix label (e.g., example.org).
        if not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+",
            domain,
        ):
            return None
        return domain

    def _fetch_ror_payload(advanced_query: str) -> dict:
        response = requests.get(
            config.ror_organization_api_url,
            params={"query.advanced": advanced_query},
            timeout=config.ror_lookup_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _escape_ror_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    query = " ".join((request.GET.get("q") or "").split())
    if len(query) < config.ror_domain_lookup_min_query_length:
        return JsonResponse({"items": []})
    if len(query) > config.ror_max_query_length:
        return JsonResponse({"items": []})
    if not re.fullmatch(r"[A-Za-z0-9 .,-]+", query):
        return JsonResponse({"items": []})
    can_run_general_lookup = len(query) >= config.ror_general_lookup_min_query_length
    email_domain = _extract_email_domain(request.GET.get("email", ""))
    # Proceed when either the general text lookup is allowed or a valid email
    # domain enables the domain-first lookup path.
    if not can_run_general_lookup and email_domain is None:
        return JsonResponse({"items": []})
    quoted_query = _escape_ror_query_value(query)
    name_or_acronym_query = f'(names.value:"{quoted_query}" OR acronyms:"{quoted_query}")'

    domain_queries = []
    if email_domain:
        quoted_email_domain = _escape_ror_query_value(email_domain)
        if can_run_general_lookup:
            domain_queries.append(
                f'links.value:"{quoted_email_domain}" AND {name_or_acronym_query}'
            )
        domain_queries.append(f'links.value:"{quoted_email_domain}"')
    query_batches = [domain_queries]
    if can_run_general_lookup:
        query_batches.append([name_or_acronym_query])

    seen_organizations: set[str] = set()
    suggestions = []
    for batch_idx, advanced_queries in enumerate(query_batches):
        is_domain_batch = batch_idx == 0 and bool(email_domain)
        for advanced_query in advanced_queries:
            try:
                payload = _fetch_ror_payload(advanced_query)
            except requests.RequestException:
                logger.exception(
                    "ROR lookup failed for query '%s' (advanced query: %s)",
                    query,
                    advanced_query,
                )
                continue

            for item in payload.get("items", []):
                # ROR API now returns v2 schema: names live in a `names[]` array tagged
                # with `types` (preferred display = "ror_display"), country lives under
                # `locations[].geonames_details.country_name`. Fall back to the legacy
                # v1 flat shape for resilience.
                organization = item.get("organization", item)
                organization_name = organization.get("name")
                country_name = (organization.get("country") or {}).get("country_name")
                if not organization_name:
                    names = organization.get("names") or []
                    display_entry = next(
                        (n for n in names if "ror_display" in (n.get("types") or [])),
                        None,
                    )
                    label_entry = next(
                        (n for n in names if "label" in (n.get("types") or [])),
                        None,
                    )
                    entry = display_entry or label_entry or (names[0] if names else None)
                    organization_name = (entry or {}).get("value")
                if not country_name:
                    locations = organization.get("locations") or []
                    if locations:
                        country_name = (
                            locations[0].get("geonames_details") or {}
                        ).get("country_name")
                if not organization_name:
                    continue

                organization_id = organization.get("id")
                dedupe_key = organization_id if organization_id is not None else organization_name
                if dedupe_key in seen_organizations:
                    continue
                seen_organizations.add(dedupe_key)

                display_label = (
                    f"{organization_name} ({country_name})"
                    if country_name
                    else organization_name
                )
                suggestions.append(
                    {
                        "name": organization_name,
                        "label": display_label,
                        "id": organization.get("id"),
                    }
                )
                if len(suggestions) >= config.ror_max_suggestions:
                    return JsonResponse({"items": suggestions})

        if email_domain and is_domain_batch and suggestions:
            return JsonResponse({"items": suggestions})
    return JsonResponse({"items": suggestions})


def approve_beta(request: HttpRequest, token: str) -> HttpResponse:
    """One-click approval endpoint reached from the emailed link.

    Verifies the signed token and, on success, admits the associated Person to
    the beta program. Sends an approval email to the person and returns a
    confirmation page. On failure returns an appropriate HTTP error.
    """
    from toxtempass import utilities as beta_util  # local import to avoid cycles

    payload = beta_util.verify_beta_token(token)
    if not payload or "person_id" not in payload:
        return HttpResponse("Invalid or expired approval token.", status=400)

    person_id = payload["person_id"]
    try:
        person = Person.objects.get(pk=person_id)
    except Person.DoesNotExist:
        return HttpResponse("Person not found for provided token.", status=404)

    try:
        beta_util.set_beta_admitted(person, True, comment="Approved via email link")
    except Exception:
        logger.exception("Failed to set beta admitted flag for person %s", person_id)
        return HttpResponse("Failed to admit user; contact maintainer.", status=500)

    # Send approval email to the user (best-effort; do not fail the request if email fails).
    # Use django-q async_task directly to ensure the job is scheduled consistently.
    try:
        recipient = getattr(person, "email", None)
        if recipient:
            task_id = async_task(
                "toxtempass.tasks.send_email_task",
                to=[recipient],
                subject="[ToxTempAssistant] Beta access approved",
                template_text="toxtempass/email/beta_approved_email.txt",
                template_html="toxtempass/email/beta_approved_email.html",
                context={
                    "person": person,
                    "login_url": request.build_absolute_uri(reverse("login")),
                },
                group="emails",
            )
            logger.info("Queued approval email task %s for person %s", task_id, person_id)
    except Exception:
        logger.exception("Failed to queue approval email for person %s", person_id)

    return render(
        request,
        "toxtempass/email/beta_approved.html",
        {
            "person": person,
            "toggle_beta_users_url": request.build_absolute_uri(
                reverse("admin_beta_user_list")
            ),
        },
    )


def beta_wait(request: HttpRequest) -> HttpResponse:
    """Page shown to users who have requested beta access but are not yet admitted.

    If the user has already been admitted, redirect them to the main application.
    """
    if is_beta_admitted(request.user):
        return redirect(reverse("overview"))
    return render(request, "toxtempass/beta_wait.html")


# --- Password reset ----------------------------------------------------------


def _format_wait_duration(seconds: float) -> str:
    """Return a human-readable wait duration string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = (seconds + 59) // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = (minutes + 59) // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = (hours + 23) // 24
    return f"{days} day{'s' if days != 1 else ''}"


class PasswordResetRequestView(DjangoPasswordResetView):
    """Password reset view with rate-limiting spam protection.

    Extends Django's built-in PasswordResetView. Before dispatching to the
    built-in form processing, checks whether the requesting user has exceeded
    the allowed frequency of reset requests. The rate-limit schedule is:
      1st → 2nd attempt: 1 minute wait
      2nd → 3rd attempt: 5 minutes wait
      3rd → 4th attempt: 1 hour wait
      4th+ attempt:      1 day wait
    """

    template_name = "toxtempass/password_reset.html"
    email_template_name = "toxtempass/email/password_reset_email.txt"
    html_email_template_name = "toxtempass/email/password_reset_email.html"
    subject_template_name = "toxtempass/email/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def form_valid(self, form):
        """Check rate-limiting before sending the reset email."""
        email = form.cleaned_data.get("email", "").strip().lower()
        try:
            user = Person.objects.get(email__iexact=email)
        except Person.DoesNotExist:
            # Do not reveal whether the account exists; proceed normally.
            return super().form_valid(form)

        wait_seconds = get_password_reset_wait_seconds(user)
        if wait_seconds > 0:
            wait_str = _format_wait_duration(wait_seconds)
            form.add_error(
                None,
                f"Too many password reset requests. "
                f"Please wait {wait_str} before trying again.",
            )
            return self.form_invalid(form)

        record_password_reset_attempt(user)
        return super().form_valid(form)


@method_decorator(user_passes_test(is_admin, login_url="/login/"), name="dispatch")
class AdminBetaUserListView(SingleTableView):
    """Admin-only SingleTableView listing persons who requested beta access.

    Uses a table class defined in toxtempass.tables.BetaUserTable.
    """

    model = Person
    template_name = "toxtempass/admin/beta_user_list.html"

    def get_table_class(self) -> type:
        """Return the table class used for displaying beta users."""
        # local import to avoid circular imports at module import time
        from toxtempass.tables import BetaUserTable

        return BetaUserTable

    def get_table_data(self) -> list[Person]:
        """Return an iterable of Person objects who requested beta access."""
        qs = Person.objects.all()
        # Filter in Python level to be resilient against JSONField DB differences
        return [p for p in qs if (p.preferences or {}).get("beta_signup")]

    def get_context_data(self, **kwargs) -> dict:
        """Add extra context variables for the template."""
        ctx = super().get_context_data(**kwargs)
        return ctx


@staff_member_required(login_url="/login/")
def toggle_beta_admitted(request: HttpRequest) -> HttpResponse:
    """Toggle admit/revoke beta status for a Person.

    Expects POST with:
      - person_id: int
      - admit: "1" or "0" (or "true"/"false")

    Returns JSON: {"success": True, "admitted": bool} or {"success": False, "error": "..."}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    person_id = request.POST.get("person_id")
    admit_raw = request.POST.get("admit", "0")
    if not person_id:
        return JsonResponse({"success": False, "error": "person_id required"}, status=400)

    try:
        person = Person.objects.get(pk=int(person_id))
    except (Person.DoesNotExist, ValueError):
        return JsonResponse({"success": False, "error": "Person not found"}, status=404)

    admit = admit_raw.lower() in ("1", "true", "yes", "on")
    try:
        beta_util.set_beta_admitted(
            person, admitted=admit, comment=f"Set by admin {request.user.get_full_name()}"
        )
    except Exception as exc:
        logger.exception("Error toggling beta admitted for person %s: %s", person_id, exc)
        return JsonResponse(
            {"success": False, "error": "Failed to update person"}, status=500
        )

    return JsonResponse({"success": True, "admitted": admit, "person_id": person.id})


# Redirects the user to ORCID’s OAuth authorization endpoint.
def orcid_login(request: HttpRequest) -> HttpResponse:
    """Redirect the user to ORCIDs OAuth authorization endpoint."""
    # Use your provided ORCID credentials:
    client_id = config._orcid_client_id
    # Build the redirect URI dynamically (ensure it matches the one registered with ORCID)
    redirect_uri = request.build_absolute_uri("/orcid/callback/")

    # The ORCID OAuth URL.
    # According to the docs, for authentication the scope is `/authenticate`
    orcid_authorize_url = (
        f"{orcid_id_baseurl}/oauth/authorize?"  ### NEED TO CHANGE FOR PRODUCTION
        f"client_id={client_id}"
        "&response_type=code"
        "&scope=/authenticate"
        f"&redirect_uri={redirect_uri}"
    )
    return redirect(orcid_authorize_url)


# Handles the callback from ORCID and logs the user in.
def orcid_callback(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Digest the callback from ORCID.

    Either log the user in or link their ORCID to their account.
    """
    code = request.GET.get("code")
    if not code:
        return HttpResponse("No code provided.", status=400)

    # Construct the token URL and prepare the token request data.
    token_url = f"{orcid_id_baseurl}/oauth/token"
    client_id = config._orcid_client_id
    client_secret = config._orcid_client_secret
    redirect_uri = request.build_absolute_uri("/orcid/callback/")

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }

    response = requests.post(token_url, data=data, timeout=15)
    if response.status_code != 200:
        return HttpResponse("Failed to authenticate with ORCID.", status=400)

    token_data = response.json()
    orcid_id = token_data.get("orcid")
    if not orcid_id:
        return HttpResponse("No ORCID id found in response.", status=400)

    # If the user is already authenticated, link the ORCID id to the logged-in account.
    if request.user.is_authenticated:
        try:
            # Assume your Person model is linked to the User model.
            # This example retrieves the Person instance via the user.
            person = Person.objects.get(email=request.user.email)
        except Person.DoesNotExist:
            return HttpResponse(
                "Authenticated user does not have a corresponding profile.", status=400
            )

        # Optional: Check if another account already has this ORCID linked.
        if Person.objects.filter(orcid_id=orcid_id).exclude(pk=person.pk).exists():
            return HttpResponse(
                (
                    "This ORCID id is already linked to another account."
                    " Please go orcid.com and logout to be prompted with"
                    " anohter authentication prompt."
                ),
                status=400,
            )

        # Save the ORCID id and token data to the user’s profile.
        person.orcid_id = orcid_id
        # Ensure your model field supports storing this data (e.g., JSONField)
        person.orcid_token_data = token_data
        person.save()

        # Optionally, you can add a message to confirm successful linking.
        return redirect("overview")  # Or wherever you wish to redirect the user.

    # If the user is not authenticated, handle the login/signup flow.
    try:
        # Attempt to log in if an account with this ORCID exists.
        user_profile = Person.objects.get(orcid_id=orcid_id)
        login(
            request, user_profile, backend="django.contrib.auth.backends.ModelBackend"
        )  # Assuming Person has a related user object.
        return redirect("overview")
    except Person.DoesNotExist:
        # Save the ORCID id and token data in the session for use in the signup process.
        request.session["orcid_id"] = orcid_id
        request.session["orcid_token_data"] = token_data
        return redirect("orcid_signup")


def orcid_signup(request: HttpRequest) -> HttpResponse | JsonResponse:
    """Handle the signup process after the user has authenticated with ORCID."""
    orcid_id = request.session.get("orcid_id")
    if not orcid_id:
        return JsonResponse(
            dict(
                success=False,
                errors={"__all__": ["No ORCID id found in session."]},
                redirect_url=reverse("login"),
            )
        )

    if request.method == "POST":
        form = SignupFormOrcid(request.POST)
        if form.is_valid():
            # Create the user but ensure the ORCID id is set from the session.
            user = form.save(commit=False)
            user.orcid_id = orcid_id
            user.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            # Record beta request and enqueue notification to maintainer.
            try:
                from toxtempass import utilities as beta_util  # type: ignore

                async_task("toxtempass.tasks.send_beta_signup_notification", user.id)
                beta_util.set_beta_requested(user)
            except Exception:
                logger.exception(
                    "Failed to record/queue beta signup notification for ORCID user %s",
                    getattr(user, "id", None),
                )
            # Optionally, clear the ORCID data from the session.
            request.session.pop("orcid_id", None)
            request.session.pop("orcid_token_data", None)
            return JsonResponse(
                dict(
                    success=True,
                    errors=form.errors,
                    redirect_url=reverse("overview"),
                )
            )
        else:
            return JsonResponse(
                {
                    "success": False,
                    "errors": form.errors,
                }
            )
    else:
        # Prefill the form with the ORCID id.
        names = request.session.get("orcid_token_data", {}).get("name").split(" ")
        if len(names) == 2:
            first_name, last_name = names
        elif len(names) < 2:
            first_name, last_name = names[0], ""
        elif len(names) > 2:
            first_name, last_name = names[0], " ".join(names[1:])
        form = SignupFormOrcid(
            initial={
                "orcid_id": orcid_id,
                "first_name": first_name,
                "last_name": last_name,
            }
        )

    return render(
        request,
        "signup.html",
        {
            "form": form,
            "ror_domain_lookup_min_query_length": (
                config.ror_domain_lookup_min_query_length
            ),
            "ror_general_lookup_min_query_length": (
                config.ror_general_lookup_min_query_length
            ),
        },
    )


def _coerce_riskhunt3r_label(raw: object) -> str:
    """Return ``raw`` if it is a known RiskHunt3rLabel value, else ``""``.

    Question-set JSON may carry a per-question ``"riskhunt3r_db_label"`` (the
    RISK-HUNT3R readiness colour). Unknown/missing values degrade to blank, so a
    typo never raises during seeding and simply renders as uncategorised.
    """
    return raw if raw in RiskHunt3rLabel.values else ""


def create_questionset_from_json(label: str, created_by: Person) -> QuestionSet:
    """Create a QuestionSet from a ToxTemp_<label>.json file."""
    if not label:
        raise ValueError("Version label is required.")

    existing_qs = QuestionSet.objects.filter(label=label).first()
    if existing_qs:
        has_content = (
            Section.objects.filter(question_set=existing_qs).exists()
            or Question.objects.filter(
                subsection__section__question_set=existing_qs
            ).exists()
        )
        if has_content:
            raise ValueError(f"Version '{label}' already exists")
        # reuse the existing (empty) QuestionSet
        qs = existing_qs
        if qs.created_by is None:
            qs.created_by = created_by
            qs.save(update_fields=["created_by"])
    else:
        qs = QuestionSet.objects.create(label=label, created_by=created_by)

    path = settings.BASE_DIR / f"ToxTemp_{label}.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(f"Could not find {path.name}")

    pending_ctx = []  # will hold (Question, [context_title, ...])

    with transaction.atomic():
        # Phase 1: create everything and collect context titles
        for sec in data.get("sections", []):
            section = Section.objects.create(
                question_set=qs,
                title=sec.get("title", "").strip(),
            )

            for subsec in sec.get("subsections", []):
                subsection = Subsection.objects.create(
                    section=section,
                    title=subsec.get("title", "").strip(),
                )

                # MAIN question
                q = Question.objects.create(
                    subsection=subsection,
                    question_text=subsec.get("question", "").strip(),
                    answer=subsec.get("answer", "").strip(),
                    answering_round=subsec.get("answering_round", 1),
                    additional_llm_instruction=subsec.get(
                        "additional_llm_instruction", ""
                    ).strip(),
                    only_additional_llm_instruction=subsec.get(
                        "only_additional_llm_instruction", False
                    ),
                    only_subsections_for_context=subsec.get(
                        "only_subsections_for_context", False
                    ),
                    riskhunt3r_db_label=_coerce_riskhunt3r_label(
                        subsec.get("riskhunt3r_db_label", "")
                    ),
                )

                # collect context‑titles for later resolution
                raw_ctx = subsec.get("subsections_for_context_title", [])
                ctx_list = [raw_ctx] if isinstance(raw_ctx, str) else list(raw_ctx)
                if ctx_list:
                    pending_ctx.append((q, ctx_list))

                # SUBquestions
                for sq in subsec.get("subquestions", []):
                    subq = Question.objects.create(
                        subsection=subsection,
                        parent_question=q,
                        question_text=sq.get("question", "").strip(),
                        answer=sq.get("answer", "").strip(),
                        answering_round=sq.get("answering_round", q.answering_round),
                        additional_llm_instruction=sq.get(
                            "additional_llm_instruction", ""
                        ).strip(),
                        only_subsections_for_context=sq.get(
                            "only_subsections_for_context", False
                        ),
                        riskhunt3r_db_label=_coerce_riskhunt3r_label(
                            sq.get("riskhunt3r_db_label", "")
                        ),
                    )

                    raw_ctx_sq = sq.get("subsections_for_context_title", [])
                    ctx_sq = (
                        [raw_ctx_sq] if isinstance(raw_ctx_sq, str) else list(raw_ctx_sq)
                    )
                    if ctx_sq:
                        pending_ctx.append((subq, ctx_sq))

        # Phase 2: resolve all contexts
        for question_obj, titles in pending_ctx:
            # find matching subsections in this set
            subs = Subsection.objects.filter(title__in=titles, section__question_set=qs)
            question_obj.subsections_for_context.set(subs)

    return qs


@staff_member_required()
def init_db(request: HttpRequest, label: str) -> JsonResponse:
    """Create a brand-new QuestionSet each time you upload."""
    try:
        qs = create_questionset_from_json(label=label, created_by=request.user)
    except ValueError as exc:
        return JsonResponse({"message": str(exc)}, status=400)
    except FileNotFoundError as exc:
        return JsonResponse({"message": str(exc)}, status=404)
    except json.JSONDecodeError as exc:
        return JsonResponse({"message": f"JSON parse error: {exc}"}, status=400)

    return JsonResponse(
        {
            "message": f"QuestionSet '{qs.label}' successfully created.",
            "version": qs.label,
        }
    )


def user_has_seen_tour_page(page: str, user: Person) -> bool:
    """Register onboarding in user preferences per page.

    Returns:
        bool: False, if onboarding has not been seen already (first time)
        bool: True, if onboarding has been shown already

    """
    # Fast path: if the in-memory snapshot already records this page as seen,
    # skip the lock + save entirely. Page renders are frequent and writing on
    # every render widens the window for lost-update races on preferences.
    cached = getattr(user, "preferences", None) or {}
    cached_seen = cached.get("has_seen_tour")
    if isinstance(cached_seen, dict) and cached_seen.get(page, False):
        return True

    result = {"already_seen": False}

    def mutate(prefs: dict) -> bool:
        seen = prefs.get("has_seen_tour")
        if not isinstance(seen, dict):
            # Repair malformed entry and record this page as seen in one write.
            prefs["has_seen_tour"] = {page: True}
            return True
        if seen.get(page, False):
            result["already_seen"] = True
            return False
        seen[page] = True
        return True

    update_prefs_atomic(user, mutate)
    return result["already_seen"]


llm = get_llm()


def _supports_prompt_caching(llm: object) -> bool:
    """Return True if ``llm`` is an Anthropic chat model accepting ``cache_control``.

    The document bundle is byte-identical across every question of an assay, so
    marking it as an Anthropic ephemeral cache breakpoint lets the other ~76
    questions reuse it at ~90% discount (5-min TTL). OpenAI/Azure do automatic
    server-side prefix caching and would reject the Anthropic-specific
    ``cache_control`` field, so we only emit it for Anthropic.
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:  # pragma: no cover - anthropic is an optional provider
        return False
    return isinstance(llm, ChatAnthropic)


def generate_answer(
    ans: Answer,
    full_pdf_context: str,
    assay: Assay,
    chatopenai: ChatOpenAI,
    base_prompt: str | None = None,
) -> tuple[int, str, int, int]:
    """Generate an answer for a single Answer instance.

    Returns a 4-tuple of ``(answer_id, answer_text, input_tokens, output_tokens)``.
    ``input_tokens`` and ``output_tokens`` are 0 when the LLM response does not
    include usage metadata.
    """
    ## some variables for logging and deadline handling
    # compute a soft deadline based on Django‑Q timeout (90% of it)
    q_timeout = settings.Q_CLUSTER.get("timeout", None)
    if q_timeout:
        deadline = time.time() + q_timeout * 0.9
    else:
        deadline = None

    all_answers = list(
        assay.answers.select_related("question__subsection__section__question_set")
    )
    max_ans_id = max(a.id for a in all_answers) if all_answers else None
    min_ans_id = min(a.id for a in all_answers) if all_answers else None
    delta_ans = max_ans_id - min_ans_id if max_ans_id and min_ans_id else 0

    q = ans.question

    # pick system messages
    if q.only_additional_llm_instruction and q.additional_llm_instruction:
        sys_msgs = [SystemMessage(content=q.additional_llm_instruction)]
    else:
        # base + question‐specific appended.
        # base_prompt override (e.g. an evaluation experiment's prompt strategy)
        # takes precedence over the production Config.base_prompt when supplied.
        sys_msgs = [
            SystemMessage(content=base_prompt or config.base_prompt),
            SystemMessage(content=f"ASSAY NAME: {assay.title}"),
            SystemMessage(content=f"ASSAY DESCRIPTION: {assay.description}"),
        ]
        if q.additional_llm_instruction:
            sys_msgs.append(SystemMessage(content=q.additional_llm_instruction))

    # Build context, separating the large *stable* document bundle (identical
    # across every question of an assay → the cache target) from any per-question
    # subsection answers (variable → must follow the cache breakpoint).
    if q.only_subsections_for_context and q.subsections_for_context.exists():
        # gather answers to *all* questions in those subsections
        ctx_answers = Answer.objects.filter(
            assay=assay,
            question__subsection__in=q.subsections_for_context.all(),
            answer_text__isnull=False,
        )
        stable_bundle = ""
        variable_ctx = "\n\n".join(
            f"--- Q: {ca.question.question_text}\nA: {ca.answer_text}"
            for ca in ctx_answers
        )
    else:
        # use full PDF + *optional* subsection‑scoped answers
        stable_bundle = full_pdf_context
        variable_ctx = ""
        if q.subsections_for_context.exists():
            ctx_answers = Answer.objects.filter(
                assay=assay,
                question__subsection__in=q.subsections_for_context.all(),
                answer_text__isnull=False,
            )
            variable_ctx = "\n\n".join(
                f"--- Q: {ca.question.question_text}\nA: {ca.answer_text}"
                for ca in ctx_answers
            )

    # build messages
    messages = []
    messages.extend(sys_msgs)
    if _supports_prompt_caching(chatopenai) and stable_bundle:
        # Anthropic prompt caching: mark the stable document bundle as an
        # ephemeral cache breakpoint so the other ~76 questions of this assay
        # reuse the prefix at ~90% discount. Variable subsection context follows
        # the breakpoint so the cached prefix stays byte-identical per question.
        messages.append(
            SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Context for this question:\n" + stable_bundle,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            )
        )
        if variable_ctx:
            messages.append(
                SystemMessage(content="Additional context:\n" + variable_ctx)
            )
    else:
        # Non-Anthropic (OpenAI/Azure/Mistral/etc.): automatic server-side prefix
        # caching — keep the original single combined context message unchanged so
        # the prompt is byte-identical to prior runs (preserves cross-model parity).
        context_str = stable_bundle
        if variable_ctx:
            context_str = (
                stable_bundle + "\n\n" + variable_ctx if stable_bundle else variable_ctx
            )
        if context_str:
            messages.append(
                SystemMessage(content="Context for this question:\n" + context_str)
            )
    messages.append(HumanMessage(content=q.question_text))

    # retry loop with dynamic waits and soft deadline
    transient_attempts = 0
    rate_limit_attempts = 0
    while True:
        if deadline is not None and time.time() > deadline:
            logger.error(
                f"Timed out retrying answer {ans.id} [{max_ans_id - ans.id}"
                " of {delta_ans}] after {q_timeout}s total"
            )
            raise TimeoutError(
                f"Answer {ans.id} [{max_ans_id - ans.id} of {delta_ans}] timed out"
            )

        try:
            resp = chatopenai.invoke(messages)
            usage = getattr(resp, "usage_metadata", None) or {}
            # `or 0` guards against providers that explicitly return None for these keys.
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            return ans.id, (resp.content or ""), input_tokens, output_tokens

        except _RATE_LIMIT_ERRORS as e:
            # Determine how long to back off. Prefer the standard Retry-After
            # header; otherwise parse the message — OpenAI says "try again in Xs",
            # Anthropic/Azure say "wait N seconds". Default + cap as a safety net.
            rate_limit_attempts += 1
            wait = 5.0
            try:
                retry_after = e.response.headers.get("retry-after")
                if retry_after:
                    wait = float(retry_after) + 0.5
                else:
                    # OpenAI: "try again in Xs"; Anthropic/Azure: "wait N seconds".
                    msg = str(getattr(e, "message", "") or e)
                    m = re.search(r"try again in ([\d\.]+)\s*s", msg) or re.search(
                        r"wait ([\d\.]+)\s*seconds?", msg
                    )
                    if m:
                        wait = float(m.group(1)) + 0.5
            except Exception:  # noqa: S110 - best-effort parse; the default applies
                pass
            # Escalate on CONSECUTIVE 429s and add jitter. A low-TPM endpoint
            # (e.g. Mistral) returns a short "retry in 1.5s" hint; with several
            # worker threads honouring it verbatim they retry in lockstep — a
            # thundering herd that re-saturates the limit every cycle and never
            # clears it (observed: 4 workers stuck on the first 4 questions for
            # 11 min). Growing an exponential floor and desynchronising the
            # workers with random jitter lets the rate-limit window actually drain.
            backoff_floor = min(2.0 * (2 ** (rate_limit_attempts - 1)), 60.0)
            wait = min(max(wait, backoff_floor), 90.0)
            wait += random.uniform(0, min(wait * 0.5, 15.0))  # desync workers

            logger.warning(
                f"RateLimit hit for answer {ans.id} [{max_ans_id - ans.id} "
                f"of {delta_ans}] (attempt {rate_limit_attempts}), "
                f"retrying in {wait:.1f}s"
            )
            time.sleep(wait)

        except _TRANSIENT_ERRORS as exc:
            # Timeout / dropped connection / 5xx — retry with exponential backoff
            # up to a cap, then give up rather than loop forever.
            transient_attempts += 1
            if transient_attempts > MAX_TRANSIENT_RETRIES:
                logger.warning(
                    "Giving up on answer %s after %d transient errors: %s",
                    ans.id, transient_attempts, exc,
                )
                return ans.id, "", 0, 0
            backoff = min(2 ** transient_attempts, 30)
            logger.warning(
                "Transient error for answer %s (attempt %d/%d), retrying in %ds: %s",
                ans.id, transient_attempts, MAX_TRANSIENT_RETRIES, backoff, exc,
            )
            time.sleep(backoff)

        except _BAD_REQUEST_ERRORS as exc:
            # Surface context-length, billing/credit-balance, and other 400-level
            # errors explicitly so they are never silently swallowed as empty
            # answers. Covers BOTH openai.BadRequestError and anthropic's own
            # variant. logger.exception includes the full traceback for diagnostics.
            logger.exception(
                "BadRequest from LLM for answer %s [%s of %s]: %s",
                ans.id,
                max_ans_id - ans.id,
                delta_ans,
                exc,
            )
            return ans.id, "", 0, 0

        except Exception as exc:
            logger.exception(
                "LLM error for answer %s [%s of %s]: %s",
                ans.id,
                max_ans_id - ans.id,
                delta_ans,
                exc,
            )
            return ans.id, "", 0, 0


def _save_assay_cost(
    assay_id: int,
    model_key: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Persist (or update) an ``AssayCost`` row for a completed LLM run.

    Looks up cost-per-million-token rates from the Azure registry and
    calculates the estimated costs.  Cost fields are left ``None``
    when the model has no pricing tags configured.  The ``cost_unit``
    field stores the currency code from the ``cost-unit`` tag (e.g. ``Eur``).
    """
    from decimal import Decimal

    from toxtempass.azure_registry import get_model as get_azure_model_entry

    cost_input_per_1m = None
    cost_output_per_1m = None
    cost_unit = ""
    model_id = ""

    try:
        idx_s, tag = model_key.split(":", 1)
        result = get_azure_model_entry(int(idx_s), tag)
        if result is not None:
            _ep, _m = result
            model_id = _m.model_id
            cost_unit = _m.cost_unit
            cip = _m.cost_input_per_1m_tokens
            cop = _m.cost_output_per_1m_tokens
            if cip is not None:
                cost_input_per_1m = Decimal(str(cip))
            if cop is not None:
                cost_output_per_1m = Decimal(str(cop))
    except Exception as exc:
        logger.warning("Could not resolve cost rates for model %r: %s", model_key, exc)

    cost_input = None
    cost_output = None
    if cost_input_per_1m is not None:
        cost_input = cost_input_per_1m * Decimal(input_tokens) / Decimal("1000000")
    if cost_output_per_1m is not None:
        cost_output = cost_output_per_1m * Decimal(output_tokens) / Decimal("1000000")

    AssayCost.objects.update_or_create(
        assay_id=assay_id,
        model_key=model_key,
        defaults=dict(
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_input_per_1m=cost_input_per_1m,
            cost_output_per_1m=cost_output_per_1m,
            cost_input=cost_input,
            cost_output=cost_output,
            cost_unit=cost_unit,
        ),
    )
    logger.info(
        "AssayCost saved: assay=%s model=%s input_tok=%d output_tok=%d "
        "cost_input=%s cost_output=%s cost_unit=%r",
        assay_id,
        model_key,
        input_tokens,
        output_tokens,
        cost_input,
        cost_output,
        cost_unit,
    )


def process_llm_async(
    assay_id: int,
    doc_dict: dict[str, dict[str, str]] | None = None,
    extract_images: bool = False,
    answer_ids: list[int] | None = None,
    chatopenai: ChatOpenAI | None = None,
    verbose: bool = False,
    user_id: int | None = None,
    llm_model: str | None = None,
    base_prompt: str | None = None,
    max_workers: int | None = None,
) -> None:
    """Process llm answer async.

    1) Seed answers in round 1,
    2) save them, then round 2, etc.
    Each question can override or replace instructions,
    and can scope context to specific subsections or use the PDFs.

    ``max_workers`` overrides the answering thread-pool size for this run (falls
    back to ``config.max_workers_threading``). Used to serialise requests against
    low-throughput endpoints — a shared low-TPM deployment livelocks on 429s when
    several large-context requests fire at once.
    """
    pool_workers = max_workers or config.max_workers_threading
    try:
        try:
            assay = Assay.objects.get(pk=assay_id)
        except Assay.DoesNotExist:
            logger.info(f"Assay with id {assay_id} does not exist. Exiting task early.")
            return

        if assay.demo_lock:
            logger.info("Assay %s is demo locked; skipping processing.", assay_id)
            return

        # Clear stale user alerts at the start of each run so the banner
        # reflects only what happened in the current run. Without persistent
        # dismissal this is how alerts get cleaned up — uploading new files
        # (or retrying) starts fresh, and any new issues will re-populate.
        assay.status = LLMStatus.BUSY
        assay.user_alerts = []
        assay.save()

        if chatopenai is None:
            # Prefer the snapshotted deployment captured at queue time — ensures
            # the worker uses the model the user had selected *then*, not what
            # they may have switched to while the task was waiting.
            if llm_model and ":" in llm_model:
                try:
                    idx_s, tag = llm_model.split(":", 1)
                    chatopenai = get_llm_for_endpoint(int(idx_s), tag, temperature=0)
                except Exception as exc:
                    logger.warning(
                        "Queued llm_model=%r unusable (%s); falling back to live resolution.",
                        llm_model,
                        exc,
                    )
                    chatopenai = None
            if chatopenai is None:
                user = None
                if user_id is not None:
                    try:
                        user = Person.objects.get(pk=user_id)
                    except Person.DoesNotExist:
                        user = None
                chatopenai, _source, _replaced = resolve_user_llm(user)

        payload = dict(doc_dict or {})
        if extract_images and payload:
            summarize_image_entries(payload)
        else:
            for key in list(payload.keys()):
                if "encodedbytes" in payload[key]:
                    payload.pop(key)

        source_documents = collect_source_documents(payload)
        text_dict, _ = split_doc_dict_by_type(payload, decode=False)
        full_pdf_context = stringyfy_text_dict(text_dict)

        # --- Context-window guard -----------------------------------------
        # Proactively truncate the document context so it stays within the
        # token budget available to the active model.  Without this guard, an
        # oversized context would either cause the LLM API to raise a
        # BadRequestError (silently turned into empty answers) or, for APIs
        # that do their own truncation, silently crop the input.
        #
        # Budget = model's context_window tag - headroom (prompts/output).
        # When the model has no context-window tag we use a conservative
        # fallback so the guard is always active.
        _context_budget: int = (
            config.context_window_fallback_tokens
            - config.context_window_headroom_tokens
        )
        if llm_model and ":" in llm_model:
            try:
                _idx_s, _mtag = llm_model.split(":", 1)
                _result = get_azure_model(int(_idx_s), _mtag)
                if _result is not None:
                    _ep, _model_entry = _result
                    if _model_entry.context_window is not None:
                        _context_budget = (
                            _model_entry.context_window
                            - config.context_window_headroom_tokens
                        )
                        logger.debug(
                            "Context budget for assay %s: %d tokens "
                            "(model=%s context_window=%d, headroom=%d)",
                            assay_id,
                            _context_budget,
                            _model_entry.model_id,
                            _model_entry.context_window,
                            config.context_window_headroom_tokens,
                        )
            except Exception as exc:
                logger.warning(
                    "Could not resolve context-window for model %r; "
                    "using fallback budget of %d tokens. Error: %s",
                    llm_model,
                    _context_budget,
                    exc,
                )

        # Guard against misconfiguration: if headroom >= context_window the
        # budget can be 0 or negative. truncate_context_to_token_limit returns
        # an empty context and marks it truncated when max_tokens <= 0, so
        # continuing would call the LLM with no document context. Running the
        # LLM with no document context produces near-useless answers, so abort
        # the run rather than press on.
        if _context_budget <= 0:
            logger.error(
                "Context budget non-positive (%d tokens) for assay %s; "
                "check context_window_headroom_tokens (%d) vs the active "
                "model's context_window. Aborting run.",
                _context_budget,
                assay_id,
                config.context_window_headroom_tokens,
            )
            log_processing_event(
                assay,
                (
                    f"Context budget non-positive ({_context_budget} tokens); "
                    f"headroom={config.context_window_headroom_tokens}. "
                    "Aborted before LLM call."
                ),
            )
            add_user_alert(
                assay,
                (
                    "The selected model's available context window is too small "
                    "to include the uploaded documents, so no answers were "
                    "generated. Switch to a model with a larger context window "
                    "(see model details in the side panel) and re-run."
                ),
                level="danger",
            )
            assay.status = LLMStatus.ERROR
            assay.save()
            return

        full_pdf_context, context_was_truncated = truncate_context_to_token_limit(
            full_pdf_context, _context_budget
        )
        if context_was_truncated:
            logger.warning(
                "Context for assay %s was truncated to fit within the "
                "%d-token context budget. "
                "Consider uploading fewer or shorter documents.",
                assay_id,
                _context_budget,
            )
            add_user_alert(
                assay,
                (
                    "Uploaded documents exceeded the available context-window budget "
                    f"({_context_budget:,} tokens). "
                    "The context was automatically truncated; some document "
                    "content may not have been used when generating answers. "
                    "Consider uploading fewer or shorter files."
                ),
                level="warning",
            )
            assay.save()
        # ------------------------------------------------------------------

        all_answers = list(
            assay.answers.select_related("question__subsection__section__question_set")
        )
        requested_ids = set(answer_ids or [])
        if requested_ids:
            all_answers = [a for a in all_answers if a.id in requested_ids]
            if not all_answers:
                logger.info(
                    "No matching answers to regenerate for assay %s; marking DONE.",
                    assay_id,
                )
                assay.status = LLMStatus.DONE
                assay.save()
                return
        rounds = sorted({a.question.answering_round for a in all_answers})
        answers_by_round = defaultdict(list)
        for ans in all_answers:
            answers_by_round[ans.question.answering_round].append(ans)

        def _assay_still_exists() -> bool:
            """Fast existence check used to short-circuit deleted assays."""
            return Assay.objects.filter(pk=assay_id).exists()

        # Accumulate token usage across all rounds.
        total_input_tokens = 0
        total_output_tokens = 0

        for rnd in rounds:
            # Gate every round on the assay still existing. Prevents round N+1
            # from firing LLM calls after the user deleted mid-run.
            if not _assay_still_exists():
                logger.info(
                    "Assay %s deleted before round %s; stopping.",
                    assay_id,
                    rnd,
                )
                return

            round_answers = answers_by_round[rnd]
            if requested_ids:
                round_answers = [a for a in round_answers if a.id in requested_ids]
                if not round_answers:
                    continue

            logger.info(
                f"Starting answering_round={rnd} with {len(round_answers)} questions"
            )

            # fire off the round in parallel
            with ThreadPoolExecutor(max_workers=pool_workers) as pool:
                futures = {
                    pool.submit(
                        generate_answer, a, full_pdf_context, assay, chatopenai,
                        base_prompt,
                    ): a
                    for a in round_answers
                }
                assay_gone = False

                with tqdm(
                    total=len(futures), disable=not verbose, desc="Answers"
                ) as pbar:
                    for future in as_completed(futures):
                        try:
                            aid, text, in_tok, out_tok = (
                                future.result()
                            )  # optionally: future.result(timeout=...)
                            total_input_tokens += in_tok
                            total_output_tokens += out_tok
                        except TimeoutError as te:
                            logger.error(str(te))
                            continue
                        except Exception as exc:
                            logger.exception(
                                f"Fatal error for answer {futures[future].id}: {exc}"
                            )
                            continue
                        finally:
                            pbar.update(1)

                        # Detect mid-round deletion; cancel anything not yet started.
                        if not _assay_still_exists():
                            if not assay_gone:
                                logger.info(
                                    "Assay %s deleted during round %s; "
                                    "cancelling %d pending future(s).",
                                    assay_id,
                                    rnd,
                                    sum(1 for f in futures if not f.done()),
                                )
                                for f in futures:
                                    if not f.done():
                                        f.cancel()
                                assay_gone = True
                            continue  # discard this completed future's result

                        try:
                            Answer.objects.filter(pk=aid).update(
                                answer_text=text,
                                answer_documents=source_documents,
                            )
                        except Exception as e:
                            log_processing_event(assay, str(e))
                            assay.status = LLMStatus.ERROR
                            assay.save()
                            continue

            # If the assay went away mid-round, stop the whole task.
            if assay_gone:
                return

        assay.status = LLMStatus.DONE
        assay.save()

        # ── Persist token usage & cost ─────────────────────────────────────────
        # Only persist when we actually received token counts from the LLM API.
        # Zero-token runs (e.g. test fakes with no usage_metadata) are skipped
        # to avoid creating spurious cost rows with no data.
        if llm_model and ":" in llm_model and (total_input_tokens or total_output_tokens):
            try:
                _save_assay_cost(
                    assay_id=assay_id,
                    model_key=llm_model,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist AssayCost for assay %s model %s: %s",
                    assay_id,
                    llm_model,
                    exc,
                )

    except Exception as e:
        logger.exception(f"Fatal error in process_llm_async: {e}")
        # Check if assay exists before updating status and context
        try:
            assay.status = LLMStatus.ERROR
            log_processing_event(assay, str(e))
            assay.save()
        except (UnboundLocalError, Assay.DoesNotExist):
            logger.info(
                f"Assay with id {assay_id} does not exist; skipping error status update."
            )


@method_decorator(user_passes_test(is_logged_in, login_url="/login/"), name="dispatch")
class AssayListView(SingleTableView):
    model = Assay
    table_class = AssayTable
    template_name = "toxtempass/overview.html"
    paginate_by = 7

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Gate users who have requested beta access but are not yet admitted.

        Redirect them to the beta waiting page.
        """
        prefs = getattr(request.user, "preferences", {}) or {}
        if prefs.get("beta_signup") and not prefs.get("beta_admitted"):
            return redirect(reverse("beta_wait"))
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[Assay]:
        """Return a queryset of Assays accessible by the user.

        Filtered to only those with a question_set.
        """
        user = self.request.user
        accessible_investigations = get_objects_for_user(
            user,
            "toxtempass.view_investigation",
            klass=Investigation,
            use_groups=False,
            any_perm=False,
        )
        accessible_assays = get_objects_for_user(
            user,
            "toxtempass.view_assay",
            klass=Assay,
            use_groups=False,
            any_perm=False,
        )
        # Assays accessible because the user can view the parent Investigation
        via_investigation_qs = Assay.objects.filter(
            study__investigation__in=accessible_investigations
        )

        # Combine assays the user has object-level view permission on (e.g., shared via workspace)
        combined_qs = (via_investigation_qs | accessible_assays).distinct()

        # Show every accessible assay that has a questionnaire. The demo copy is
        # NOT auto-hidden when real work exists — it stays in the list (sorting to
        # the bottom as the oldest entry) and disappears only if the user deletes
        # it themselves. Admins can opt out per-request via ?hide_demo, which is
        # a view filter only: nothing is deleted and other users are unaffected.
        qs = combined_qs.filter(question_set__isnull=False)
        if self.request.GET.get("hide_demo") and is_admin(user):
            qs = qs.filter(
                demo_lock=False, demo_template=False, demo_source__isnull=True
            )
        return qs.order_by("-submission_date")

    def get_context_data(self, **kwargs) -> dict:
        """Inject context."""
        context = super().get_context_data(**kwargs)
        context["show_tour"] = not user_has_seen_tour_page("overview", self.request.user)
        context["reload_busy_interval"] = config.reload_busy_interval_seconds
        context["reload_busy_max_retries"] = config.reload_busy_max_retries
        context["LLMStatus"] = LLMStatus
        context.update(get_workspace_list(self.request))
        # Tour management is now handled by JavaScript localStorage
        # No backend flags needed
        return context


@login_required(login_url="/login/")
@user_passes_test(is_beta_admitted, login_url="/beta/wait/")
def new_form_view(request: HttpRequest) -> HttpResponse | JsonResponse:
    """View to handle the starting form for new ToxTemp."""
    # -------------------------------
    # GET: render the StartingForm, possibly using ?investigation=?, ?study=?, ?assay=?
    # -------------------------------
    if request.method == "GET":
        # 2) Check for any query-parameters to prefill the form
        inv_id = request.GET.get("investigation")
        st_id = request.GET.get("study")
        assay_id = request.GET.get("assay")

        initial: dict[str, str] = {}
        if inv_id:
            initial["investigation"] = inv_id
        if st_id:
            initial["study"] = st_id
        if assay_id:
            initial["assay"] = assay_id

        # 3) Instantiate StartingForm with initial=… so those
        # <select> fields stay pre-selected
        form = StartingForm(initial=initial, user=request.user)

        # Compute the set of assay PKs the user is allowed to delete.
        # An assay is deletable if the user owns its parent investigation OR created the assay.
        deletable_assay_ids = list(
            form.fields["assay"]
            .queryset.filter(
                models.Q(created_by=request.user)
                | models.Q(study__investigation__owner=request.user)
            )
            .values_list("pk", flat=True)
        )

        # Check if user has seen the add_new tour before
        show_tour = not user_has_seen_tour_page("add_new", request.user)

        return render(
            request,
            "new.html",
            {
                "form": form,
                "action": reverse("add_new"),
                "show_tour": show_tour,
                "deletable_assay_ids": deletable_assay_ids,
            },
        )

    # --------------------------------
    # POST: process the StartingForm and kick off the async task
    # --------------------------------
    if request.method == "POST":
        form = StartingForm(request.POST, user=request.user)

        if form.is_valid():
            assay = form.cleaned_data["assay"]
            extract_images = form.cleaned_data.get("extract_images", False)
            overwrite = form.cleaned_data.get("overwrite", False)
            qs = form.cleaned_data["question_set"]
            assay.question_set = qs
            assay.save()
            # Security check: does the user still have view-permission on this Assay?
            if not assay.is_accessible_by(request.user, perm_prefix="view"):
                from django.core.exceptions import PermissionDenied

                raise PermissionDenied("You do not have permission to access this assay.")

            if assay.demo_lock:
                return JsonResponse(
                    {
                        "success": False,
                        "errors": {
                            "__all__": [
                                "Demo assays cannot be regenerated.",
                            ]
                        },
                    },
                    status=400,
                )

            files = request.FILES.getlist("files")
            consent_file_storage = form.cleaned_data.get("consent_file_storage", False)
            answers_exist = assay.answers.exists()

            # Store files to S3/MinIO if user consented
            stored_file_assets = []
            if files and consent_file_storage:
                try:
                    stored_file_assets = store_files_to_storage(
                        files=files,
                        user=request.user,
                        assay=assay,
                        consent=True,
                    )
                    logger.info(
                        "Stored %d files for user %s on assay %s",
                        len(stored_file_assets),
                        request.user.email,
                        assay.id,
                    )
                except Exception as e:
                    corr_id = uuid.uuid4().hex[:8]
                    logger.exception(
                        "File storage failed [corr=%s] for assay %s", corr_id, assay.id
                    )
                    log_processing_event(
                        assay, f"[{corr_id}] {type(e).__name__}: {e}"
                    )
                    assay.save()
                    return JsonResponse(
                        {
                            "success": False,
                            "errors": {
                                "__all__": [
                                    f"File storage failed (ref {corr_id}). "
                                    "Please contact support if the issue persists."
                                ],
                            },
                        },
                        status=500,
                    )

            # If files were uploaded and there are no existing answers,
            # seed empty answers.
            if files and not answers_exist:
                form_empty_answers = AssayAnswerForm({}, assay=assay, user=request.user)
                if form_empty_answers.is_valid():
                    form_empty_answers.save()
                    if consent_file_storage and stored_file_assets:
                        # Link stored FileAssets to the newly created Answers
                        pairs = [
                            AnswerFile(answer=answer, file=file)
                            for answer, file in product(
                                assay.answers.all(), stored_file_assets
                            )
                        ]
                        with transaction.atomic():
                            AnswerFile.objects.bulk_create(pairs, ignore_conflicts=True)
            # If files were uploaded and either overwrite is True or no existing
            if files and (overwrite or not answers_exist):
                doc_dict, unreadable = get_text_or_imagebytes_from_django_uploaded_file(
                    files, extract_images=False
                )
                if unreadable:
                    for name in unreadable:
                        add_user_alert(
                            assay,
                            f"'{name}' could not be read and was not used to generate answers.",
                            level="warning",
                        )
                try:
                    # Set assay status to busy and hand it off to the async worker
                    assay.status = LLMStatus.SCHEDULED
                    assay.save()
                    # Fire off the asynchronous worker
                    async_task(
                        process_llm_async,
                        assay.id,
                        doc_dict,
                        extract_images,
                        user_id=request.user.pk,
                        # Snapshot the user's current model choice so a later
                        # preference change doesn't affect this already-queued job.
                        llm_model=current_llm_key(request.user),
                    )

                except Exception as e:
                    corr_id = uuid.uuid4().hex[:8]
                    logger.exception(
                        "LLM processing failed [corr=%s] for assay %s", corr_id, assay.id
                    )
                    # Cleanup orphaned files if LLM fails
                    if stored_file_assets:
                        for asset in stored_file_assets:
                            try:
                                asset.status = "deleted"
                                asset.save()
                                logger.info(
                                    "Marked FileAsset as deleted due to LLM failure: %s",
                                    asset.id,
                                )
                            except Exception as cleanup_error:
                                logger.exception(
                                    "Failed to cleanup file asset: %s", cleanup_error
                                )
                    log_processing_event(
                        assay, f"[{corr_id}] {type(e).__name__}: {e}"
                    )
                    assay.save()
                    return JsonResponse(
                        {
                            "success": False,
                            "errors": {
                                "__all__": [
                                    f"Processing failed (ref {corr_id}). "
                                    "Please contact support if the issue persists."
                                ]
                            },
                        },
                        status=500,
                    )

            # On success, return JSON with a redirect to 'answer_assay_questions'
            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": reverse("overview"),
                }
            )

        else:
            # Form invalid → return errors back to the AJAX handler
            return JsonResponse({"success": False, "errors": form.errors})


@login_required(login_url="/login/")
def get_filtered_studies(request: HttpRequest, investigation_id: int) -> JsonResponse:
    """Get filtered Studies based on the Investigation ID."""
    if investigation_id:
        # include owner + creator provenance when appropriate
        studies = Study.objects.filter(investigation_id=investigation_id).select_related(
            "investigation__owner", "created_by"
        )
        out = []
        for s in studies:
            display = provenance_label_for_item(s, request.user)
            out.append({"id": s.id, "title": s.title, "display": display})
        return JsonResponse(out, safe=False)
    return JsonResponse([], safe=False)


@login_required(login_url="/login/")
def get_filtered_assays(request: HttpRequest, study_id: int) -> JsonResponse:
    """Get filtered Assays based on the Study ID."""
    if study_id:
        assays = Assay.objects.filter(study_id=study_id).select_related(
            "study__investigation__owner", "created_by"
        )
        assays_list = []
        for assay in assays:
            display = provenance_label_for_item(assay, request.user)
            assays_list.append({"id": assay.id, "title": assay.title, "display": display})
        return JsonResponse(assays_list, safe=False)
    return JsonResponse([], safe=False)


@login_required(login_url="/login/")
def get_assay_is_busy_or_scheduled(request: HttpRequest, pk: int) -> JsonResponse:
    """Check if the Assay is busy or scheduled."""
    if request.method == "POST":
        assay = get_object_or_404(Assay, pk=pk)
        if not assay.is_accessible_by(request.user, perm_prefix="view"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to access this assay.")
        is_busy_or_scheduled = assay.status in {LLMStatus.BUSY, LLMStatus.SCHEDULED}
        return JsonResponse({"is_busy_or_scheduled": is_busy_or_scheduled})


@login_required(login_url="/login/")
def initial_gpt_allowed_for_assay(request: HttpRequest, pk: int) -> JsonResponse:
    """Check if GPT is allowed for the given Assay."""
    if request.method == "POST":
        assay = get_object_or_404(Assay, pk=pk)
        if not assay.is_accessible_by(request.user, perm_prefix="view"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to access this assay.")
        if not assay.answers.all():
            return JsonResponse({"gpt_allowed": True})
        else:
            return JsonResponse({"gpt_allowed": False})


@login_required(login_url="/login/")
def create_or_update_investigation(
    request: HttpRequest, pk: int | None = None
) -> JsonResponse | HttpResponse:
    """Create or update an Investigation."""
    if pk:
        investigation = get_object_or_404(Investigation, pk=pk)
        if not investigation.is_accessible_by(request.user, perm_prefix="change"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied(
                "You do not have permission to modify this investigation."
            )
    else:
        investigation = None
    if request.method == "POST":
        # Bind posted data to the form
        form = InvestigationForm(request.POST, user=request.user, instance=investigation)
        if form.is_valid():
            inv = form.save(commit=False)
            # Only set the owner when creating a new Investigation; do not change owner on updates
            if investigation is None:
                inv.owner = request.user
            inv.save()
            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": reverse("add_new"),
                }
            )
        else:
            return JsonResponse({"success": False, "errors": form.errors})
    else:
        # GET: show form; pass instance but do not change owner here
        form = InvestigationForm(user=request.user, instance=investigation)
    return render(
        request,
        "create.html",
        {
            "form": form,
            "title": "Create Investigation",
            "back_url": mark_safe(reverse("add_new")),  # noqa: S308
        },
    )


@login_required(login_url="/login/")
@require_POST
def delete_investigation(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Delete an investigation if the user has permission.

    POST-only and CSRF-protected: GET would be triggerable by browser
    prefetchers, link scanners, or cross-site image/link tags.
    """
    investigation = get_object_or_404(Investigation, pk=pk)
    if not investigation.is_accessible_by(request.user, perm_prefix="delete"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied(
            "You do not have permission to delete this investigation."
        )
    investigation.delete()
    return redirect("add_new")


@login_required(login_url="/login/")
def create_or_update_study(
    request: HttpRequest, pk: int | None = None
) -> JsonResponse | HttpResponse:
    """Create or update a Study."""
    # If pk is provided, we’re editing an existing Study.
    if pk:
        study = get_object_or_404(Study, pk=pk)
        if not study.is_accessible_by(request.user, perm_prefix="change"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to modify this study.")
    else:
        study = None

    if request.method == "POST":
        form = StudyForm(request.POST, instance=study, user=request.user)
        if form.is_valid():
            investigation = form.cleaned_data.get("investigation")
            # Ensure the user actually has view access to the parent investigation
            if not investigation.is_accessible_by(request.user, perm_prefix="view"):
                from django.core.exceptions import PermissionDenied

                raise PermissionDenied(
                    "You do not have permission to add a study to this investigation."
                )

            saved_study = form.save(commit=False)
            # record who created it when creating a new Study
            if study is None:
                saved_study.created_by = request.user
            saved_study.save()
            inv_id = saved_study.investigation_id
            st_id = saved_study.id

            # Redirect back to /start/?investigation=<inv_id>&study=<st_id>
            redirect_url = reverse("add_new")
            redirect_url += f"?investigation={inv_id}&study={st_id}"

            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": redirect_url,
                }
            )
        else:
            return JsonResponse({"success": False, "errors": form.errors})

    else:
        # GET: possibly prefill “investigation” if present in querystring
        inv_id = request.GET.get("investigation")
        initial = {}
        if inv_id:
            initial["investigation"] = inv_id

        form = StudyForm(instance=study, initial=initial, user=request.user)

        back_url = reverse("add_new")
        if inv_id:
            back_url += f"?investigation={inv_id}"

        # If creating inside a workspace-shared investigation and the current user
        # is not the investigation owner, present a warning banner in the UI.
        warning = None
        if study is None:
            try:
                inv = Investigation.objects.get(pk=int(inv_id)) if inv_id else None
            except Exception:
                inv = None
            if (
                inv
                and inv.owner != request.user
                and inv.is_accessible_by(request.user, perm_prefix="view")
            ):
                warning = (
                    f"You are creating a Study inside an Investigation owned by {inv.owner.email}. "
                    "If you leave the workspace or lose access, you lose editing access to this Study and all its associated Assays."
                )

        return render(
            request,
            "create.html",
            {
                "form": form,
                "title": pk and "Modify Study" or "Create Study",
                "back_url": mark_safe(back_url),  # noqa: S308
                "workspace_creation_warning": warning,
            },
        )


@login_required(login_url="/login/")
@require_POST
def delete_study(request: HttpRequest, pk: int) -> HttpResponseRedirect:
    """Delete a study if the user has permission.

    POST-only and CSRF-protected: GET would be triggerable by browser
    prefetchers, link scanners, or cross-site image/link tags.
    """
    study = get_object_or_404(Study, pk=pk)
    if not study.is_accessible_by(request.user, perm_prefix="delete"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to delete this study.")
    study.delete()
    return redirect("add_new")


@login_required(login_url="/login/")
def create_or_update_assay(
    request: HttpRequest, pk: int | None = None
) -> JsonResponse | HttpResponse:
    """Create or update an Assay."""
    # If pk is provided, we’re editing an existing Assay.
    if pk:
        assay = get_object_or_404(Assay, pk=pk)
        if not assay.is_accessible_by(request.user, perm_prefix="change"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("You do not have permission to modify this assay.")
    else:
        assay = None

    if request.method == "POST":
        form = AssayForm(request.POST, instance=assay, user=request.user)
        if form.is_valid():
            saved_assay = form.save(commit=False)
            # record creator for new assays when created by workspace members
            if assay is None:
                saved_assay.created_by = request.user
            saved_assay.save()
            st_id = saved_assay.study_id
            inv_id = saved_assay.study.investigation_id
            assay_id = saved_assay.id

            # Redirect back to
            redirect_url = reverse("add_new")
            redirect_url += f"?investigation={inv_id}&study={st_id}&assay={assay_id}"

            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": redirect_url,
                }
            )
        else:
            return JsonResponse({"success": False, "errors": form.errors})

    else:
        # GET: possibly prefill “study” (and indirectly Investigation) from querystring
        inv_id = request.GET.get("investigation")
        st_id = request.GET.get("study")
        initial = {}
        if st_id:
            initial["study"] = st_id
        # (AssayForm only needs the “study” field; the Investigation is inferred.)

        form = AssayForm(instance=assay, initial=initial, user=request.user)

        back_url = reverse("add_new")
        params = []
        if inv_id:
            params.append(f"investigation={inv_id}")
        if st_id:
            params.append(f"study={st_id}")
        if params:
            back_url += "?" + "&".join(params)

        return render(
            request,
            "create.html",
            {
                "form": form,
                "title": pk and "Modify Assay" or "Create Assay",
                "back_url": mark_safe(back_url),  # noqa: S308
                "show_tour": not user_has_seen_tour_page("create_assay", request.user),
            },
        )


@login_required(login_url="/login/")
@require_POST
def delete_assay(
    request: HttpRequest, pk: int
) -> JsonResponse | HttpResponseRedirect:
    """Delete an assay if the user has permission.

    POST-only and CSRF-protected: GET would be triggerable by browser
    prefetchers, link scanners, or cross-site image/link tags. The
    `?from=overview` query parameter is still accepted (querystrings work
    on POST too) so the redirect target depends on the originating page.
    """
    # allows to distinguish between deleting from overview tables
    source_page = request.GET.get("from")
    assay = get_object_or_404(Assay, pk=pk)
    if not assay.is_accessible_by(request.user, perm_prefix="delete"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to delete this assay.")
    # The demo *template* (master) must never be deleted through the UI. Demo
    # *copies* belong to the user, so they may delete their own — the access
    # check above already restricts deletion to the owner.
    if assay.demo_template:
        return JsonResponse(
            {
                "status": "error",
                "message": "The demo template cannot be deleted here.",
            },
            status=400,
        )
    assay.delete()
    if source_page == "overview":
        return redirect("overview")
    return redirect("add_new")


@login_required(login_url="/login/")
def answer_assay_questions(
    request: HttpRequest, assay_id: int
) -> JsonResponse | HttpResponse:
    """Render the form to answer questions for a specific assay."""
    from toxtempass.models import AssayView

    assay = get_object_or_404(Assay, pk=assay_id)
    if not assay.is_accessible_by(request.user, perm_prefix="view"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to access this assay.")
    if assay.demo_lock and request.method == "POST":
        return JsonResponse(
            {
                "success": False,
                "errors": {
                    "__all__": [
                        "This assay is locked for demo purposes and cannot be edited.",
                    ]
                },
            },
            status=400,
        )
    # Record that the user has viewed this assay, update or create the AssayView record
    AssayView.objects.update_or_create(
        user=request.user,
        assay=assay,
        defaults={"last_viewed": timezone.now()},
    )
    # only the sections belonging to this assay's QuestionSet
    sections = Section.objects.filter(question_set=assay.question_set).prefetch_related(
        "subsections__questions"
    )
    if request.method == "POST":
        form = AssayAnswerForm(
            request.POST, request.FILES, assay=assay, user=request.user
        )
        if form.is_valid():
            queued = form.save()
            if form.errors:
                return JsonResponse({"success": False, "errors": form.errors})
            redirect_target = (
                reverse("overview")
                if getattr(form, "async_enqueued", False)
                else reverse("answer_assay_questions", kwargs=dict(assay_id=assay.pk))
            )
            return JsonResponse(
                {
                    "success": True,
                    "errors": form.errors,
                    "redirect_url": redirect_target,
                }
            )
        else:
            return JsonResponse({"success": False, "errors": form.errors})
    else:
        form = AssayAnswerForm(assay=assay, user=request.user)
    return render(
        request,
        "answer.html",
        {
            "form": form,
            "assay": assay,
            "sections": sections,
            "show_tour": not user_has_seen_tour_page(
                "answer_assay_questions", request.user
            ),
            # config is injected via the template context processor
            "back_url": reverse("overview"),
            "export_json_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="json")
            ),
            "export_md_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="md")
            ),
            "export_pdf_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="pdf")
            ),
            "export_xml_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="xml")
            ),
            "export_html_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="html")
            ),
            "export_docx_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="docx")
            ),
            "export_tex_url": reverse(
                "export_assay", kwargs=dict(assay_id=assay.id, export_type="tex")
            ),
        },
    )


@login_required(login_url="/login/")
def get_version_history(
    request: HttpRequest, assay_id: int, question_id: int
) -> HttpResponse:
    """Fetch the version history of an answer to a question in an assay."""
    answer = get_object_or_404(Answer, assay=assay_id, question=question_id)
    if not answer.is_accessible_by(request.user, perm_prefix="view"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied(
            "You do not have permission to access this answer's version history."
        )
    history = answer.history.all().order_by("-history_date")
    version_changes = []

    class DotDict(dict):
        def __getattr__(self, key: str):
            try:
                return self[key]
            except KeyError:
                raise AttributeError(f"'DotDict' object has no attribute '{key}'")

        def __setattr__(self, key: str, value: object) -> None:
            self[key] = value

    def _split_into_words(text: str) -> list:
        return re.findall(r"\S+|\n", text)

    def _get_diff_html(diff: Iterator) -> str:
        html_diff = ""
        for word in diff:
            if word.startswith("-"):
                html_diff += (
                    '<span style="color: red; text-decoration: line-through;">'
                    f"{word[2:]}</span> "
                )
            elif word.startswith("+"):
                html_diff += f'<span style="color: green;">{word[2:]}</span> '
            else:
                html_diff += f"{word[2:]} "
        return html_diff

    for version in history:
        if version.prev_record:
            changes = version.diff_against(version.prev_record).changes
        else:
            changes = version.diff_against(version).changes
        version_changes.append({"version": version, "changes": changes})
        version_changes[-1]["version"].history_date = naturaltime(
            version_changes[-1]["version"].history_date
        )
        last_changes_answer_text = [
            change
            for change in version_changes[-1]["changes"]
            if change.field == "answer_text"
        ]
        if last_changes_answer_text:
            version_changes[-1]["answer_text_changes_html"] = _get_diff_html(
                difflib.ndiff(
                    _split_into_words(last_changes_answer_text[0].old),
                    _split_into_words(last_changes_answer_text[0].new),
                )
            )
        if changes and version_changes[-1]["changes"][0].field == "accepted":
            version_changes.pop(-1)

    # ─── strip out any “versions” with no changes ───
    version_changes = [entry for entry in version_changes if entry["changes"]]

    # If no versions with changes found, add a synthetic "original" version entry to show initial data.
    if not version_changes:
        # Build a minimal synthetic version entry
        answer_text = answer.answer_text or ""

        # Create a DotDict-like object for synthetic version metadata
        class SyntheticVersion(DotDict):
            pass

        synthetic_version = SyntheticVersion()
        synthetic_version.history_date = (
            history.first().history_date if history else "Original"
        )
        synthetic_version.history_user = history.first().history_user if history else None
        synthetic_version.history_id = history.first().history_id if history else None

        # Build changes list for answer_text and answer_documents field
        changes = []
        # change dict format matches what is expected
        # Use attributes to mimic along with "field", "old", and "new"
        changes.append(DotDict(field="answer_text", old="", new=answer_text))
        changes.append(
            DotDict(
                field="answer_documents",
                old="",
                new=", ".join(answer.answer_documents or []),
            )
        )

        # Build HTML for answer_text no diffs because original
        answer_text_html = answer_text.replace("\n", "<br>")

        version_changes.append(
            {
                "version": synthetic_version,
                "changes": changes,
                "answer_text_changes_html": answer_text_html,
            }
        )

    question_set_display_name = str(answer.question.subsection.section.question_set)
    return render(
        request,
        "answer_extras/version_history_modal_body.html",
        {
            "version_changes": version_changes,
            "instance": answer,
            "question_set_display_name": question_set_display_name,
        },
    )


@login_required(login_url="/login/")
def export_assay(
    request: HttpRequest, assay_id: int, export_type: str
) -> FileResponse | JsonResponse:
    """Export an assay to a file in the specified format."""
    assay = get_object_or_404(Assay, id=assay_id)
    if not assay.is_accessible_by(request.user, perm_prefix="view"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You do not have permission to export this assay.")
    if assay.has_feedback:
        return export_assay_to_file(request, assay, export_type)
    else:
        return JsonResponse(
            {
                "success": False,
                "errors": {"__all__": ["No feedback has been provided yet."]},
            }
        )


@login_required(login_url="/login/")
@require_POST
def assay_time_sync(request: HttpRequest, assay_id: int) -> JsonResponse:
    """Sync a user's accumulated active time for an assay to the server.

    Upserts one ``AssayTimeLog`` row per (user, assay) pair, keeping the
    stored value monotonic — the server will never decrease the recorded
    seconds even if the client sends a smaller number (e.g. after storage
    was cleared).  Returns the aggregate total across all collaborators;
    this is used by ``AssayAnswerForm`` to capture ``completion_time_seconds``
    when all answers are accepted.

    POST body: ``seconds=<non-negative integer>``
    """
    assay = get_object_or_404(Assay, id=assay_id)
    if not assay.is_accessible_by(request.user):
        return JsonResponse({"success": False, "error": "Forbidden"}, status=403)

    raw = request.POST.get("seconds")
    try:
        seconds = int(raw)  # type: ignore[arg-type]
        if seconds < 0:
            raise ValueError("negative seconds")
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "error": "Invalid seconds value"}, status=400)

    # Keep server-side value monotonic: never regress the recorded total.
    existing = AssayTimeLog.objects.filter(user=request.user, assay=assay).first()
    effective_seconds = max(seconds, existing.seconds if existing else 0)
    if existing:
        if existing.seconds != effective_seconds:
            existing.seconds = effective_seconds
            existing.save(update_fields=["seconds"])
    else:
        AssayTimeLog.objects.create(user=request.user, assay=assay, seconds=effective_seconds)

    total: int = (
        AssayTimeLog.objects.filter(assay=assay).aggregate(total=Sum("seconds"))["total"]
        or 0
    )
    return JsonResponse({"success": True, "total_seconds": total})


@login_required(login_url="/login/")
def assay_hasfeedback(request: HttpRequest, assay_id: int) -> JsonResponse:
    """Check if an assay has feedback. Returns a JSON response."""
    assay = get_object_or_404(Assay, id=assay_id)
    return JsonResponse({"success": True, "has_feedback": assay.has_feedback})


@login_required(login_url="/login/")
@require_POST
def assay_feedback(request: HttpRequest, assay_id: int) -> JsonResponse:
    """Handle feedback submission for an assay."""
    from django.core.exceptions import PermissionDenied

    assay = get_object_or_404(Assay, id=assay_id)
    if not assay.is_accessible_by(request.user, perm_prefix="view"):
        raise PermissionDenied("You do not have permission to submit feedback for this assay.")
    feedback_text = request.POST.get("feedback")
    usefulness_rating = request.POST.get("usefulness_rating")
    if feedback_text and usefulness_rating:
        feedback = Feedback.objects.create(
            feedback_text=feedback_text,
            usefulness_rating=usefulness_rating,
            # Use the server-recorded completion time, not a client-submitted value.
            time_spent_seconds=assay.completion_time_seconds,
            assay=assay,
            user=request.user,
        )
        assay.feedback = feedback
        assay.save()
        return JsonResponse(
            {
                "success": True,
                "errors": {},
            }
        )
    else:
        errors = {}
        if not feedback_text:
            errors["feedbackText"] = ["Feedback cannot be empty."]
        if not usefulness_rating:
            errors["usefulnessRating"] = ["Usefulness rating cannot be empty."]
        return JsonResponse({"success": False, "errors": errors})


# ---------------------------------------------------------------------------
# Workspace views — extracted to toxtempass/workspace.py
# Re-exported here so that existing imports (for example, urls.py)
# that reference toxtempass.views continue to work.
# ---------------------------------------------------------------------------
from toxtempass.workspace import (  # noqa: E402
    add_workspace_assay,
    add_workspace_member,
    add_workspace_member_by_email,
    create_or_update_workspace,
    delete_workspace,
    get_workspace_list,
    remove_workspace_assay,
    remove_workspace_member,
    remove_workspace_member_by_email,
)


@login_required
def set_llm_preference(request: HttpRequest) -> JsonResponse:
    """Persist the user's preferred LLM deployment on ``Person.preferences``.

    POST body: ``llm_model=<idx:tag>`` or empty string to clear.
    Validates against the current registry, admin allowlist, and retirement status.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    from toxtempass.azure_registry import get_model
    from toxtempass.models import LLMConfig

    raw = (request.POST.get("llm_model") or "").strip()

    if not raw:
        sentinel = object()
        update_prefs_atomic(
            request.user,
            lambda prefs: prefs.pop("llm_model", sentinel) is not sentinel,
        )
        # Return the admin-default's signature so the badge updates in place.
        from toxtempass.azure_registry import badge_icon, badge_short, get_model
        from toxtempass.models import LLMConfig as _LLMConfig

        cfg = _LLMConfig.load()
        sig = None
        if cfg.default_model and ":" in cfg.default_model:
            try:
                idx_s, tag = cfg.default_model.split(":", 1)
                resolved = get_model(int(idx_s), tag)
            except (ValueError, TypeError):
                resolved = None
            if resolved is not None:
                _, m = resolved
                direct = (m.tags.get("direct-from-azure") or "").lower() == "true"
                model_by = (m.tags.get("provider") or "").title()
                sig = {
                    "model_id": m.model_id,
                    "model_by": model_by,
                    "hosted_on": "Azure (direct)"
                    if direct
                    else (f"Azure (MaaS via {model_by or 'third-party'})"),
                    "version": m.tags.get("version", ""),
                    "privacy_short": badge_short(m.badge),
                    "privacy_icon": badge_icon(m.badge),
                    "retirement_date": (
                        m.retirement_date.isoformat() if m.retirement_date else ""
                    ),
                    "context_window": m.context_window,
                }
        return JsonResponse({"success": True, "llm_model": None, "signature": sig})

    if ":" not in raw:
        return JsonResponse({"success": False, "error": "Invalid format"}, status=400)
    try:
        idx_s, tag = raw.split(":", 1)
        idx = int(idx_s)
    except ValueError:
        return JsonResponse({"success": False, "error": "Invalid format"}, status=400)

    resolved = get_model(idx, tag)
    if resolved is None:
        return JsonResponse({"success": False, "error": "Unknown deployment"}, status=400)

    ep, m = resolved
    if m.retirement_status == "retired":
        return JsonResponse({"success": False, "error": "Deployment retired"}, status=400)

    cfg = LLMConfig.load()
    if (
        cfg.allowed_models
        and raw not in cfg.allowed_models
        and not request.user.is_superuser
    ):
        return JsonResponse(
            {"success": False, "error": "Not in allowed list"}, status=403
        )

    def _set_llm_model(prefs: dict) -> bool:
        prefs["llm_model"] = raw
        return True

    update_prefs_atomic(request.user, _set_llm_model)

    from toxtempass.azure_registry import badge_icon, badge_short

    direct = (m.tags.get("direct-from-azure") or "").lower() == "true"
    model_by = (m.tags.get("provider") or "").title()
    if direct:
        hosted_on = "Azure (direct)"
    else:
        hosted_on = f"Azure (MaaS via {model_by or 'third-party'})"

    return JsonResponse(
        {
            "success": True,
            "llm_model": raw,
            "signature": {
                "model_id": m.model_id,
                "model_by": model_by,
                "hosted_on": hosted_on,
                "version": m.tags.get("version", ""),
                "privacy_short": badge_short(m.badge),
                "privacy_icon": badge_icon(m.badge),
                "retirement_date": (
                    m.retirement_date.isoformat() if m.retirement_date else ""
                ),
                "context_window": m.context_window,
            },
        }
    )
