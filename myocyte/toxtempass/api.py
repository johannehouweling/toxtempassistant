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
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_q.tasks import async_task

from toxtempass import config, notifications, utilities, versions
from toxtempass.export import export_assay_to_file, generate_json_from_assay
from toxtempass.models import (
    ApiPdfJob,
    Assay,
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

NIL_UUID = UUID(int=0)  # stands in for a job id when building route templates
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
    active = sum(1 for t in workspace.api_tokens.all() if t.is_active)
    if active >= config._api_tokens_max_active:
        return JsonResponse(
            {
                "success": False,
                "error": (
                    f"A workspace can have {config._api_tokens_max_active} active "
                    "tokens; revoke one first"
                ),
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

    Annotated with ``last_modified`` and the newest version (see
    ``toxtempass.versions``), because ``Assay`` has no modified timestamp.
    """
    return versions.with_latest_versions(
        Assay.objects.filter(
            study__investigation__shared_in_workspaces__workspace=workspace
        ).distinct()
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _assay_summary(assay: Assay) -> dict:
    newest = versions.latest_version(assay)
    return {
        "id": assay.pk,
        "title": assay.title,
        "status": assay.status,
        "submission_date": _iso(assay.submission_date),
        "last_modified": _iso(assay.last_modified),
        "version": str(newest.id) if newest else None,
        "study": {"id": assay.study_id, "title": assay.study.title},
        "investigation": {
            "id": assay.study.investigation_id,
            "title": assay.study.investigation.title,
        },
    }


def _credited_ids(workspace: Workspace) -> frozenset[int]:
    """Return who may be named: members of the workspace who have not opted out.

    The agreement is the person's own (Privacy tab) and covers every workspace.
    Leaving the workspace, or withdrawing the agreement, takes effect on the very
    next request, because nothing is remembered about who was named before.
    """
    return frozenset(
        WorkspaceMember.objects.filter(
            workspace=workspace, user__credit_by_name=True
        ).values_list("user_id", flat=True)
    )


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
                    "pdf_job": reverse("api_pdf_job", args=[NIL_UUID]).replace(
                        str(NIL_UUID), "{job_id}"
                    ),
                },
                "limits": {
                    "pdf_cooldown_seconds": config._api_pdf_cooldown_seconds,
                    "pdf_retention_minutes": config._api_pdf_retention_minutes,
                },
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
    return _api_response(
        JsonResponse(generate_json_from_assay(assay, _credited_ids(workspace)))
    )


def _pdf_job_payload(job: ApiPdfJob) -> dict:
    """Describe a job. A finished PDF that has been cleaned away reads "expired"."""
    expired = job.is_expired
    ready = job.status == ApiPdfJob.Status.DONE and not expired
    return {
        "id": str(job.pk),
        "status": "expired" if expired else job.status,
        "assay_id": job.assay_id,
        "created_at": _iso(job.created_at),
        "finished_at": _iso(job.finished_at),
        "expires_at": _iso(job.expires_at),
        "error": job.error or None,
        "file_url": reverse("api_pdf_job_file", args=[job.pk]) if ready else None,
    }


def _pdf_job_response(job: ApiPdfJob, status: int) -> HttpResponse:
    response = _api_response(JsonResponse(_pdf_job_payload(job), status=status))
    response["Location"] = reverse("api_pdf_job", args=[job.pk])
    return response


def _busy(message: str, status: int, retry_after: int) -> HttpResponse:
    response = _error(message, status)
    response["Retry-After"] = str(retry_after)
    return response


@require_GET
@token_required
def api_assay_version(
    request: HttpRequest, workspace: Workspace, assay_id: int, version_id: UUID
) -> HttpResponse:
    """Return a ToxTemp as it was at one of the versions in its ``history``."""
    assay = (
        _workspace_assays(workspace)
        .select_related("study__investigation", "question_set")
        .filter(pk=assay_id)
        .first()
    )
    version = versions.find(assay, version_id) if assay is not None else None
    if version is None:
        return _error("Not found", 404)
    document = generate_json_from_assay(assay, _credited_ids(workspace), version)
    return _api_response(JsonResponse(document))


@csrf_exempt
@require_POST
@token_required
def api_assay_pdf(
    request: HttpRequest, workspace: Workspace, assay_id: int
) -> HttpResponse:
    """Ask for a ToxTemp as a PDF. It is built by the task queue, not in this request.

    Answers ``202`` with a job to poll (``Location``), then download from its
    ``file_url``. A PDF costs a pandoc run, so each token may ask for one per
    ``Config._api_pdf_cooldown_seconds`` and has one unfinished job at a time;
    asking sooner gets a 429 with ``Retry-After``. Beyond
    ``Config._api_pdf_max_active_jobs`` jobs across all tokens the API says 503.
    Requests that fail, or name an unknown assay, do not use up the cool-down.
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
    unfinished = ApiPdfJob.objects.filter(
        token=token, status__in=ApiPdfJob.ACTIVE
    ).first()
    if unfinished is not None:
        if unfinished.assay_id == assay.pk:
            return _pdf_job_response(unfinished, 202)  # asking again is harmless
        return _busy("A PDF for this token is still being built", 429, 10)
    if (
        ApiPdfJob.objects.filter(status__in=ApiPdfJob.ACTIVE).count()
        >= config._api_pdf_max_active_jobs
    ):
        return _busy("PDF builds are busy; retry shortly", 503, 30)

    now = timezone.now()
    cooldown = timedelta(seconds=config._api_pdf_cooldown_seconds)
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
        return _busy(f"PDF export is limited; retry in {wait} s", 429, wait)

    job = ApiPdfJob.objects.create(
        workspace=workspace, token=token, assay=assay, created_at=now
    )
    try:
        async_task("toxtempass.api.build_pdf_job", str(job.pk), group="api-pdf")
    except Exception:
        logger.exception("Could not queue PDF job %s", job.pk)
        _fail_pdf_job(job, "The PDF could not be queued; try again later.")
        return _busy("PDF builds are unavailable; retry shortly", 503, 30)
    job.refresh_from_db()  # already finished when the queue runs inline
    return _pdf_job_response(job, 202)


@require_GET
@token_required
def api_pdf_job(request: HttpRequest, workspace: Workspace, job_id: UUID) -> HttpResponse:
    """Say how a PDF job is doing; poll this until it is done."""
    job = ApiPdfJob.objects.filter(pk=job_id, workspace=workspace).first()
    if job is None:
        return _error("Not found", 404)
    return _api_response(JsonResponse(_pdf_job_payload(job)))


@require_GET
@token_required
def api_pdf_job_file(
    request: HttpRequest, workspace: Workspace, job_id: UUID
) -> HttpResponse:
    """Download the finished PDF, for a limited time."""
    job = ApiPdfJob.objects.filter(pk=job_id, workspace=workspace).first()
    if job is None:
        return _error("Not found", 404)
    if job.is_active:
        return _error("The PDF is not ready yet", 409)
    if job.status == ApiPdfJob.Status.FAILED:
        return _error("The PDF could not be built; request it again", 410)
    if job.is_expired:
        return _error("The PDF has expired; request it again", 410)
    try:
        handle = job.file_path.open("rb")
    except OSError:
        return _error("The PDF has expired; request it again", 410)
    return _api_response(
        FileResponse(
            handle,
            as_attachment=True,
            filename=job.file_name or "toxtemp.pdf",
            content_type="application/pdf",
        )
    )


def _fail_pdf_job(job: ApiPdfJob, message: str) -> None:
    """Mark a job failed and hand the token its cool-down back."""
    ApiPdfJob.objects.filter(pk=job.pk).update(
        status=ApiPdfJob.Status.FAILED, error=message, finished_at=timezone.now()
    )
    WorkspaceApiToken.objects.filter(pk=job.token_id, last_pdf_at=job.created_at).update(
        last_pdf_at=None
    )


def build_pdf_job(job_id: str) -> None:
    """Build one job's PDF (runs in the task queue).

    Never raises: the queue would retry a failed task, and a failed build is
    reported on the job instead.
    """
    claimed = ApiPdfJob.objects.filter(
        pk=job_id, status=ApiPdfJob.Status.QUEUED
    ).update(status=ApiPdfJob.Status.RUNNING)
    if not claimed:
        return  # gone, or already taken by an earlier delivery
    job = (
        ApiPdfJob.objects.select_related(
            "workspace", "assay__study__investigation", "assay__question_set"
        )
        .filter(pk=job_id)
        .first()
    )
    if job is None:
        return
    try:
        # Only members who have not opted out are named, and never by email.
        response = export_assay_to_file(
            None, job.assay, "pdf", credited_ids=_credited_ids(job.workspace)
        )
        if response.status_code != 200:
            raise RuntimeError(f"export returned {response.status_code}")
        content = b"".join(response.streaming_content)
        directory = ApiPdfJob.directory()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = job.file_path.with_suffix(".pdf.part")
        partial.write_bytes(content)
        partial.replace(job.file_path)  # a reader never sees half a file
        finished = timezone.now()
        ApiPdfJob.objects.filter(pk=job.pk).update(
            status=ApiPdfJob.Status.DONE,
            finished_at=finished,
            expires_at=finished
            + timedelta(minutes=config._api_pdf_retention_minutes),
            file_name=f"toxtemp_{job.assay_id}.pdf",
        )
    except Exception:
        logger.exception("PDF job %s failed", job_id)
        _fail_pdf_job(job, "The PDF could not be built; request it again.")


def cleanup_pdf_jobs(now: datetime | None = None) -> None:
    """Delete expired PDFs and old job records, and fail jobs that lost their worker.

    Run by the periodic job. Safe to repeat; the files are disposable.
    """
    now = now or timezone.now()
    directory = ApiPdfJob.directory()

    stale = ApiPdfJob.objects.filter(
        status__in=ApiPdfJob.ACTIVE,
        created_at__lt=now - timedelta(minutes=config._api_pdf_stale_minutes),
    )
    for job in stale:
        _fail_pdf_job(job, "The PDF build was interrupted; request it again.")

    for job in ApiPdfJob.objects.filter(
        status=ApiPdfJob.Status.DONE, expires_at__lte=now
    ):
        job.file_path.unlink(missing_ok=True)

    old = ApiPdfJob.objects.filter(
        created_at__lt=now - timedelta(hours=config._api_pdf_record_hours)
    )
    for job in old:
        job.file_path.unlink(missing_ok=True)
    old.delete()

    # Anything left in the directory that no live job owns, e.g. after a crash.
    if directory.is_dir():
        keep = {
            f"{pk}.pdf"
            for pk in ApiPdfJob.objects.filter(
                status=ApiPdfJob.Status.DONE, expires_at__gt=now
            ).values_list("pk", flat=True)
        }
        recent = now.timestamp() - 600  # leave a file that is being written
        for path in directory.iterdir():
            if path.name not in keep and path.stat().st_mtime < recent:
                path.unlink(missing_ok=True)


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
