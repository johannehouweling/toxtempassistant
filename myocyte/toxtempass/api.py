"""Read-only API for external servers, authenticated by workspace tokens.

A token belongs to a workspace and reads the investigations shared into it. Owners
and admins of the workspace issue and revoke tokens; nobody else, including site
admins, ever sees a token's secret, because only its hash is stored.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable
from datetime import timedelta
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from toxtempass import config, utilities
from toxtempass.export import generate_json_from_assay
from toxtempass.models import (
    Assay,
    Workspace,
    WorkspaceApiToken,
    WorkspaceMember,
    WorkspaceRole,
)

logger = logging.getLogger(__name__)

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
    token = WorkspaceApiToken.objects.create(
        workspace=workspace,
        name=name,
        token_hash=hash_token(secret),
        prefix=secret[:8],
        created_by=request.user,
        expires_at=timezone.now() + timedelta(days=days),
    )
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
        logger.info("API token %s revoked by user %s", token.pk, request.user.pk)
    return JsonResponse({"success": True})


def _unauthorized() -> JsonResponse:
    response = JsonResponse({"error": "Invalid or missing token"}, status=401)
    response["WWW-Authenticate"] = "Bearer"
    return response


def token_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Authenticate a request by its bearer token and pass the workspace on."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if utilities.is_rate_limited(request, "api"):
            return JsonResponse({"error": config._rate_limited_message}, status=429)
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
        return view(request, token.workspace, *args, **kwargs)

    return wrapper


def _workspace_assays(workspace: Workspace) -> QuerySet[Assay]:
    return Assay.objects.filter(
        study__investigation__shared_in_workspaces__workspace=workspace
    ).distinct()


@require_GET
@token_required
def api_assay_list(request: HttpRequest, workspace: Workspace) -> JsonResponse:
    """List the ToxTemps in the investigations shared into the token's workspace."""
    assays = _workspace_assays(workspace).select_related("study__investigation")
    return JsonResponse(
        {
            "assays": [
                {
                    "id": a.pk,
                    "title": a.title,
                    "study": a.study.title,
                    "investigation": a.study.investigation.title,
                }
                for a in assays.order_by("pk")
            ]
        }
    )


@require_GET
@token_required
def api_assay_detail(
    request: HttpRequest, workspace: Workspace, assay_id: int
) -> JsonResponse:
    """Return one ToxTemp with its answers, in the same shape as the JSON export."""
    assay = get_object_or_404(_workspace_assays(workspace), pk=assay_id)
    data = generate_json_from_assay(assay)
    if data is None:
        return JsonResponse({"error": "Could not build this ToxTemp"}, status=500)
    return JsonResponse(data)
