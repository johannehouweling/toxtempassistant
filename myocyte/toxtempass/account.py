"""User menu endpoints: profile, email and password, ORCID, shared documents.

They answer the menu's fetch calls with JSON, except the confirmation link for a
new email address (a page) and the shared-documents list (an HTML fragment the
Privacy tab swaps in after each action).
"""

import logging

from django import forms
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from toxtempass import config, notifications, privacy, utilities
from toxtempass.forms import AccountDeletionForm, EmailChangeForm, ProfileForm
from toxtempass.models import Person

logger = logging.getLogger(__name__)


def _rate_limited() -> JsonResponse:
    """Return the error shown when a client made too many attempts."""
    return JsonResponse(
        {"success": False, "error": config._rate_limited_message}, status=429
    )


def _form_errors(form: forms.BaseForm) -> JsonResponse:
    """Return a form's validation errors for the menu to show."""
    return JsonResponse({"success": False, "errors": form.errors}, status=400)


@login_required(login_url="/login/")
@require_POST
def update_profile(request: HttpRequest) -> JsonResponse:
    """Save the name and organization the user edited inline in the Account tab.

    A changed organization is checked against ROR first, as at signup (see
    forms.OrganizationRorCheckMixin). The response carries the saved values, so the
    menu can show them, including the ROR match, without reloading.
    """
    form = ProfileForm(request.POST, instance=request.user)
    if not form.is_valid():
        return _form_errors(form)
    user = form.save()
    return JsonResponse(
        {
            "success": True,
            "name": user.get_full_name(),
            "organization": user.organization,
            "ror_name": user.ror_name,
        }
    )


@login_required(login_url="/login/")
@require_POST
def request_email_change(request: HttpRequest) -> JsonResponse:
    """Start changing the email address; the new one must be confirmed first.

    The account keeps its current address until the link sent to the new one is
    followed, and the current address is told about the request.
    """
    if utilities.is_rate_limited(request, "email_change"):
        return _rate_limited()
    form = EmailChangeForm(request.user, request.POST)
    if not form.is_valid():
        return _form_errors(form)
    user = request.user
    user.pending_email = form.cleaned_data["new_email"]
    user.save(update_fields=["pending_email"])
    notifications.request_email_change(user)
    return JsonResponse(
        {
            "success": True,
            "reload": True,
            "message": f"We sent a confirmation link to {user.pending_email}.",
        }
    )


@login_required(login_url="/login/")
@require_POST
def cancel_email_change(request: HttpRequest) -> JsonResponse:
    """Drop a pending email change; its confirmation link stops working."""
    user = request.user
    if user.pending_email:
        user.pending_email = ""
        user.save(update_fields=["pending_email"])
    return JsonResponse(
        {"success": True, "reload": True, "message": "The change is cancelled."}
    )


@require_GET
def confirm_email_change(request: HttpRequest, token: str) -> HttpResponse:
    """Switch the account to the new address, from the link sent to that address."""
    template = "toxtempass/email_change.html"
    verified = utilities.verify_email_change_token(token)
    if verified is None:
        return render(request, template, {"status": "invalid"}, status=400)
    person, new_email = verified
    taken = Person.objects.filter(email__iexact=new_email).exclude(pk=person.pk)
    if not taken.exists():
        person.email = new_email
        person.pending_email = ""
        person.email_confirmed_at = timezone.now()
        try:
            person.save(update_fields=["email", "pending_email", "email_confirmed_at"])
        except IntegrityError:
            logger.info("Email change for %s lost a race for the address", person.pk)
        else:
            return render(request, template, {"status": "confirmed", "email": new_email})
    Person.objects.filter(pk=person.pk).update(pending_email="")
    return render(request, template, {"status": "taken", "email": new_email}, status=400)


@login_required(login_url="/login/")
@require_POST
def change_password(request: HttpRequest) -> JsonResponse:
    """Change the password, keep this session signed in and send a security notice."""
    if utilities.is_rate_limited(request, "password_change"):
        return _rate_limited()
    form = PasswordChangeForm(request.user, request.POST)
    if not form.is_valid():
        return _form_errors(form)
    user = form.save()
    update_session_auth_hash(request, user)
    notifications.queue_email(notifications.PASSWORD_CHANGED, user=user)
    return JsonResponse({"success": True, "message": "Your password is changed."})


@login_required(login_url="/login/")
@require_POST
def unlink_orcid(request: HttpRequest) -> JsonResponse:
    """Remove the ORCID iD from the account; signing in then needs the password."""
    user = request.user
    if user.orcid_id:
        if not user.has_usable_password():
            return JsonResponse(
                {
                    "success": False,
                    "error": "Set a password first, or you could not sign in any more.",
                },
                status=400,
            )
        user.orcid_id = None
        user.save(update_fields=["orcid_id"])
    return JsonResponse({"success": True, "reload": True, "message": "ORCID unlinked."})


def _shared_files_html(request: HttpRequest) -> str:
    """Render the Privacy tab's list of shared documents."""
    return render_to_string(
        "toxtempass/base_extras/account/shared_files.html",
        {"groups": privacy.shared_files_by_assay(request.user)},
        request=request,
    )


def _file_ids(request: HttpRequest) -> list[str]:
    """Read the comma-separated file ids a Stop sharing or Undo button sends."""
    return [part for part in request.POST.get("file_ids", "").split(",") if part]


@login_required(login_url="/login/")
@require_GET
def shared_files(request: HttpRequest) -> HttpResponse:
    """Return the list of documents the user shared, as an HTML fragment."""
    return HttpResponse(_shared_files_html(request))


@login_required(login_url="/login/")
@require_POST
def stop_sharing(request: HttpRequest) -> JsonResponse:
    """Withdraw documents the user uploaded, and return the updated list."""
    privacy.withdraw_files(request.user, _file_ids(request))
    return JsonResponse({"success": True, "html": _shared_files_html(request)})


@login_required(login_url="/login/")
@require_POST
def undo_stop_sharing(request: HttpRequest) -> JsonResponse:
    """Share withdrawn documents again within the waiting period."""
    privacy.restore_files(request.user, _file_ids(request))
    return JsonResponse({"success": True, "html": _shared_files_html(request)})


@login_required(login_url="/login/")
@require_GET
def delete_account_panel(request: HttpRequest) -> HttpResponse:
    """Return the Account tab's deletion section: export, blockers, confirmation."""
    user = request.user
    blockers = privacy.deletion_blockers(user)
    context = {
        "blockers": blockers,
        "summary": None if blockers else privacy.deletion_summary(user),
        "assay_count": privacy.exportable_assays(user).count(),
    }
    return HttpResponse(
        render_to_string(
            "toxtempass/base_extras/account/delete_account.html", context, request=request
        )
    )


@login_required(login_url="/login/")
@require_GET
def export_toxtemps(request: HttpRequest) -> HttpResponse:
    """Download every ToxTemp the user can open, as a ZIP of JSON and Markdown files."""
    if utilities.is_rate_limited(request, "account_export"):
        return HttpResponse(
            config._rate_limited_message, status=429, content_type="text/plain"
        )
    archive = privacy.export_toxtemps_zip(request.user)
    response = HttpResponse(archive, content_type="application/zip")
    filename = f"toxtempassistant-export-{timezone.now():%Y-%m-%d}.zip"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required(login_url="/login/")
@require_POST
def delete_account(request: HttpRequest) -> JsonResponse:
    """Delete the signed-in user's account, after the password and a confirmation.

    Refused while something blocks it (see privacy.deletion_blockers). The former
    address gets a receipt, so a deletion from a stolen session gets noticed.
    """
    if utilities.is_rate_limited(request, "account_delete"):
        return _rate_limited()
    user = request.user
    if privacy.deletion_blockers(user):
        return JsonResponse(
            {
                "success": False,
                "error": "Your account cannot be deleted yet; see the notes above.",
            },
            status=400,
        )
    form = AccountDeletionForm(user, request.POST)
    if not form.is_valid():
        return _form_errors(form)
    email, name = user.email, user.get_full_name()
    logout(request)
    privacy.delete_account(user)
    notifications.send_account_deleted_receipt(email, name)
    logger.info("An account was deleted at its owner's request")
    return JsonResponse({"success": True, "redirect_url": reverse("account_deleted")})


@require_GET
def account_deleted(request: HttpRequest) -> HttpResponse:
    """Confirm, after the redirect, that the account is gone."""
    return render(
        request,
        "toxtempass/account_deleted.html",
        {"backup_retention_days": config._backup_retention_days},
    )
