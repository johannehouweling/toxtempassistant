"""Read-only API for external servers, authenticated by workspace tokens.

A token belongs to a workspace and reads the investigations shared into it. Owners
and admins of the workspace issue and revoke tokens; nobody else, including site
admins, ever sees a token's secret, because only its hash is stored.
"""

import hashlib
import logging
import math
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import lru_cache, wraps
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce, Greatest
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from toxtempass import config, notifications, utilities
from toxtempass.export import export_assay_to_file, get_assay_api_authors
from toxtempass.models import (
    Answer,
    Assay,
    Section,
    Workspace,
    WorkspaceApiToken,
    WorkspaceMember,
    WorkspaceRole,
)

logger = logging.getLogger(__name__)

# The data API is versioned in the path. "preview" promises nothing: fields and
# routes may change until the contract is agreed. Once frozen it becomes /api/v1/,
# which may only grow; a breaking change is /api/v2/, with v1 kept and marked by a
# Sunset header.
API_VERSION = "preview"
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 200

TOKEN_PREFIX = "ttw_"  # noqa: S105 - a public label, not a secret


def hash_token(token: str) -> str:
    """Return the stored form of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _token_info(token: WorkspaceApiToken) -> dict:
    return {
        "id": token.pk,
        "name": token.name,
        "prefix": token.prefix,
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat(),
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
    }


def _managed_workspace(request: HttpRequest, pk: int) -> Workspace | None:
    """Return the workspace if the user is its owner or an admin, else None."""
    workspace = get_object_or_404(Workspace, pk=pk)
    is_manager = WorkspaceMember.objects.filter(
        workspace=workspace,
        user=request.user,
        role__in=[WorkspaceRole.OWNER, WorkspaceRole.ADMIN],
    ).exists()
    return workspace if is_manager else None


_FORBIDDEN = JsonResponse(
    {"success": False, "error": "You do not have permission"}, status=404
)


@login_required(login_url="/login/")
@require_GET
def list_tokens(request: HttpRequest, pk: int) -> JsonResponse:
    """List a workspace's active tokens (never their secrets)."""
    workspace = _managed_workspace(request, pk)
    if workspace is None:
        return _FORBIDDEN
    tokens = [t for t in workspace.api_tokens.order_by("-created_at") if t.is_active]
    return JsonResponse({"success": True, "tokens": [_token_info(t) for t in tokens]})


@login_required(login_url="/login/")
@require_POST
def create_token(request: HttpRequest, pk: int) -> JsonResponse:
    """Issue a token. The plaintext is in this response and nowhere else."""
    workspace = _managed_workspace(request, pk)
    if workspace is None:
        return _FORBIDDEN
    name = request.POST.get("name", "").strip()[:100]
    if not name:
        return JsonResponse(
            {"success": False, "error": "Give the token a name"}, status=400
        )
    try:
        days = int(request.POST.get("expires_days") or config._api_token_default_days)
    except ValueError:
        days = 0
    if not 1 <= days <= config._api_token_max_days:
        return JsonResponse(
            {
                "success": False,
                "error": f"Lifetime must be 1 to {config._api_token_max_days} days",
            },
            status=400,
        )
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with transaction.atomic():
        token = WorkspaceApiToken.objects.create(
            workspace=workspace,
            name=name,
            token_hash=hash_token(secret),
            prefix=secret[:8],
            created_by=request.user,
            expires_at=timezone.now() + timedelta(days=days),
        )
        notifications.notify_api_token_created(token, request.user)
    logger.info(
        "API token %s issued for workspace %s by user %s",
        token.pk,
        workspace.pk,
        request.user.pk,
    )
    return JsonResponse({"success": True, "token": secret, "info": _token_info(token)})


@login_required(login_url="/login/")
@require_POST
def revoke_token(request: HttpRequest, pk: int, token_id: int) -> JsonResponse:
    """Revoke a token at once."""
    workspace = _managed_workspace(request, pk)
    if workspace is None:
        return _FORBIDDEN
    token = get_object_or_404(WorkspaceApiToken, pk=token_id, workspace=workspace)
    if token.revoked_at is None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at"])
        notifications.cancel_api_token_notices(token)
        logger.info("API token %s revoked by user %s", token.pk, request.user.pk)
    return JsonResponse({"success": True})


def _unauthorized() -> HttpResponse:
    response = JsonResponse({"error": "Invalid or missing token"}, status=401)
    response["WWW-Authenticate"] = "Bearer"
    return _api_response(response)


def token_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Authenticate a request by its bearer token and pass the workspace on."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if utilities.is_rate_limited(request, "api"):
            return _error(config._rate_limited_message, 429)
        scheme, _, secret = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secret.strip():
            return _unauthorized()
        token = (
            WorkspaceApiToken.objects.select_related("workspace")
            .filter(token_hash=hash_token(secret.strip()))
            .first()
        )
        if token is None or not token.is_active:
            return _unauthorized()
        WorkspaceApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        request.api_token = token
        return view(request, token.workspace, *args, **kwargs)

    return wrapper


def _api_response(response: HttpResponse) -> HttpResponse:
    """Stamp the API version and keep private data out of shared caches."""
    response["X-API-Version"] = API_VERSION
    response["Cache-Control"] = "private, no-store"
    return response


def _error(message: str, status: int) -> HttpResponse:
    return _api_response(JsonResponse({"error": message}, status=status))


def _workspace_assays(workspace: Workspace) -> QuerySet[Assay]:
    """Assays in the investigations shared into ``workspace``.

    ``last_modified`` is the later of creation and the latest answer edit, taken
    from the answers' history, because ``Assay`` has no modified timestamp.
    """
    latest_answer_edit = (
        Answer.history.model.objects.filter(assay_id=OuterRef("pk"))
        .order_by("-history_date")
        .values("history_date")[:1]
    )
    return (
        Assay.objects.filter(
            study__investigation__shared_in_workspaces__workspace=workspace
        )
        .distinct()
        .annotate(
            last_modified=Greatest(
                F("submission_date"),
                Coalesce(Subquery(latest_answer_edit), F("submission_date")),
            )
        )
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _assay_summary(assay: Assay) -> dict:
    return {
        "id": assay.pk,
        "title": assay.title,
        "status": assay.status,
        "submission_date": _iso(assay.submission_date),
        "last_modified": _iso(assay.last_modified),
        "study": {"id": assay.study_id, "title": assay.study.title},
        "investigation": {
            "id": assay.study.investigation_id,
            "title": assay.study.investigation.title,
        },
    }


def _answer_payload(answer: Answer | None) -> dict:
    return {
        "text": answer.answer_text if answer else "",
        "accepted": answer.accepted if answer else None,
        "llm_abstained": answer.llm_abstained if answer else None,
        "source_documents": (answer.answer_documents or []) if answer else [],
    }


def _credited_ids(workspace: Workspace) -> frozenset[int]:
    """Return who may be named: members of the workspace who agreed to be credited.

    Leaving the workspace, or withdrawing the agreement, takes effect on the very
    next request, because nothing is remembered about who was named before.
    """
    return frozenset(
        WorkspaceMember.objects.filter(
            workspace=workspace, credit_consent_at__isnull=False
        ).values_list("user_id", flat=True)
    )


def _assay_detail(assay: Assay, credited_ids: frozenset[int]) -> dict:
    """Return one ToxTemp as an explicit allowlist of fields.

    Built field by field on purpose: serialising the models would also expose
    internal ones (``processing_log``, ``user_alerts``, user ids).
    """
    answers = {a.question_id: a for a in assay.answers.all()}
    question_set_id = assay.question_set_id
    if question_set_id is None and answers:
        # Assays from before question sets: derive it from the answered questions.
        question_set_id = (
            Section.objects.filter(subsections__questions__answers__assay=assay)
            .values_list("question_set_id", flat=True)
            .first()
        )
    sections = (
        Section.objects.filter(question_set_id=question_set_id)
        .prefetch_related("subsections__questions")
        .order_by("pk")
    )
    return {
        **_assay_summary(assay),
        "description": assay.description,
        "question_set": assay.question_set.label if assay.question_set else None,
        "authors": get_assay_api_authors(assay, credited_ids),
        "sections": [
            {
                "id": section.pk,
                "title": section.title,
                "subsections": [
                    {
                        "id": sub.pk,
                        "title": sub.title,
                        "questions": [
                            {
                                "id": q.pk,
                                "parent_question_id": q.parent_question_id,
                                "text": q.question_text,
                                "answer": _answer_payload(answers.get(q.pk)),
                            }
                            for q in sub.questions.all()
                        ],
                    }
                    for sub in section.subsections.all()
                ],
            }
            for section in sections
        ],
    }


@require_GET
@token_required
def api_root(request: HttpRequest, workspace: Workspace) -> HttpResponse:
    """Say which API this is and which workspace the token reads."""
    return _api_response(
        JsonResponse(
            {
                "api_version": API_VERSION,
                "stable": False,
                "workspace": {"id": workspace.pk, "name": workspace.name},
                "endpoints": {
                    "assays": reverse("api_assay_list"),
                    "assay": reverse("api_assay_detail", args=[0]).replace("0", "{id}"),
                    "assay_pdf": reverse("api_assay_pdf", args=[0]).replace("0", "{id}"),
                },
                "limits": {"pdf_cooldown_seconds": config._api_pdf_cooldown_seconds},
            }
        )
    )


@require_GET
@token_required
def api_assay_list(request: HttpRequest, workspace: Workspace) -> HttpResponse:
    """List ToxTemps in the shared investigations, newest change first.

    Query: ``limit`` (default 50, max 200), ``offset``, and ``updated_since``
    (ISO 8601) to fetch only what changed since the last sync.
    """
    try:
        limit = min(
            max(int(request.GET.get("limit", PAGE_SIZE_DEFAULT)), 1), PAGE_SIZE_MAX
        )
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        return _error("limit and offset must be integers", 400)

    assays = _workspace_assays(workspace).select_related("study__investigation")
    since = request.GET.get("updated_since")
    if since:
        # A "+" in a URL query decodes to a space; restore it for the UTC offset.
        parsed = parse_datetime(since.strip().replace(" ", "+"))
        if parsed is None:
            return _error("updated_since must be an ISO 8601 date-time", 400)
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        assays = assays.filter(last_modified__gt=parsed)

    ordered = assays.order_by("-last_modified", "pk")
    total = ordered.count()
    page = list(ordered[offset : offset + limit])
    next_offset = offset + limit if offset + limit < total else None
    return _api_response(
        JsonResponse(
            {
                "count": total,
                "next_offset": next_offset,
                "results": [_assay_summary(a) for a in page],
            }
        )
    )


@require_GET
@token_required
def api_assay_detail(
    request: HttpRequest, workspace: Workspace, assay_id: int
) -> HttpResponse:
    """Return one ToxTemp with its questionnaire and answers."""
    assay = (
        _workspace_assays(workspace)
        .select_related("study__investigation", "question_set")
        .filter(pk=assay_id)
        .first()
    )
    if assay is None:
        return _error("Not found", 404)
    return _api_response(JsonResponse(_assay_detail(assay, _credited_ids(workspace))))


@require_GET
@token_required
def api_assay_pdf(
    request: HttpRequest, workspace: Workspace, assay_id: int
) -> HttpResponse:
    """Return a ToxTemp as a PDF, built on the spot and not stored.

    A PDF costs a pandoc run, so each token may ask for one per cool-down period
    (``Config._api_pdf_cooldown_seconds``); asking sooner gets a 429 with
    ``Retry-After``. Requests that fail, or name an unknown assay, do not use up
    the cool-down.
    """
    assay = (
        _workspace_assays(workspace)
        .select_related("study__investigation", "question_set")
        .filter(pk=assay_id)
        .first()
    )
    if assay is None:
        return _error("Not found", 404)

    token: WorkspaceApiToken = request.api_token
    now = timezone.now()
    cooldown = timedelta(seconds=config._api_pdf_cooldown_seconds)
    previous = token.last_pdf_at
    # One conditional UPDATE decides the winner, so concurrent requests with the
    # same token cannot both start a build.
    claimed = (
        WorkspaceApiToken.objects.filter(pk=token.pk)
        .filter(Q(last_pdf_at__isnull=True) | Q(last_pdf_at__lte=now - cooldown))
        .update(last_pdf_at=now)
    )
    if not claimed:
        last = WorkspaceApiToken.objects.values_list("last_pdf_at", flat=True).get(
            pk=token.pk
        )
        wait = max(1, math.ceil((last + cooldown - now).total_seconds())) if last else 1
        response = _error(f"PDF export is limited; retry in {wait} s", 429)
        response["Retry-After"] = str(wait)
        return response

    # Only members who agreed to be credited are named, and never by email address.
    response = export_assay_to_file(
        request, assay, "pdf", credited_ids=_credited_ids(workspace)
    )
    if response.status_code >= 400:
        # Give the cool-down back, unless another request has taken it since.
        WorkspaceApiToken.objects.filter(pk=token.pk, last_pdf_at=now).update(
            last_pdf_at=previous
        )
    return _api_response(response)


OPENAPI_DIR = Path(__file__).parent / "openapi"


@lru_cache(maxsize=1)
def _openapi_spec() -> dict:
    """Load the contract. It is a file in the repo, not generated from the views."""
    import yaml  # noqa: PLC0415

    with (OPENAPI_DIR / f"{API_VERSION}.yaml").open(encoding="utf-8") as spec_file:
        return yaml.safe_load(spec_file)


@require_GET
def api_openapi(request: HttpRequest) -> HttpResponse:
    """Serve the OpenAPI document. Public: partners need it before they have a token."""
    return JsonResponse(_openapi_spec())


@require_GET
def api_docs(request: HttpRequest) -> HttpResponse:
    """Serve the interactive docs (Swagger UI) for the API."""
    return render(
        request, "toxtempass/api_docs.html", {"spec_url": reverse("api_openapi")}
    )
