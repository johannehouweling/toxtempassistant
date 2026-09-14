"""What a user shares with us, taking it back, and leaving altogether.

Shared documents: uploads are only kept when the user consents at upload (see
``filehandling.store_files_to_storage``). "Stop sharing" marks a file withdrawn:
from that moment nothing uses it, because admin downloads and benchmarking only
read ``available`` files. After ``Config._file_withdrawal_grace_hours`` the
periodic job deletes it, which also removes the object from storage
(``signals.delete_object_from_storage``). Until then the uploader can undo.
Answers keep their text; they never depend on the stored file.

Account deletion: blocked while the user owns workspaces, which are never
deleted on their behalf. Before deleting, users can download every ToxTemp they
can open with :func:`export_toxtemps_zip`.
"""

from __future__ import annotations

import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.text import slugify
from guardian.shortcuts import get_objects_for_user

from toxtempass import config
from toxtempass.models import (
    AnswerFile,
    Assay,
    EmailLog,
    FileAsset,
    FileWithdrawal,
    Investigation,
    Person,
    QuestionSet,
    Workspace,
    WorkspaceInvestigation,
)

logger = logging.getLogger(__name__)


def _valid_ids(file_ids: list[str]) -> list[uuid.UUID]:
    """Keep only the ids that are UUIDs; anything else cannot be a file."""
    valid = []
    for file_id in file_ids:
        try:
            valid.append(uuid.UUID(str(file_id).strip()))
        except ValueError:
            continue
    return valid


def _assay_ids(files: list[FileAsset]) -> dict[uuid.UUID, int]:
    """Map each file to the assay it was uploaded for.

    A file reaches its assay only through the answers it is linked to.
    """
    assay_ids: dict[uuid.UUID, int] = {}
    links = (
        AnswerFile.objects.filter(file__in=files)
        .values_list("file_id", "answer__assay_id")
        .distinct()
    )
    for file_id, assay_id in links:
        assay_ids.setdefault(file_id, assay_id)
    return assay_ids


def shared_files(user: Person) -> QuerySet[FileAsset]:
    """Return the documents ``user`` shared that are not deleted yet."""
    return FileAsset.objects.filter(
        uploaded_by=user,
        status__in=[FileAsset.Status.AVAILABLE, FileAsset.Status.WITHDRAWN],
    ).order_by("original_filename")


def shared_files_by_assay(user: Person) -> list[dict]:
    """Group ``user``'s shared documents by assay, for the Privacy tab.

    Files never linked to answers (for example from a draft that failed) form a
    group of their own, listed last.
    """
    files = list(shared_files(user))
    assay_ids = _assay_ids(files)
    assays = Assay.objects.in_bulk(set(assay_ids.values()))
    groups: dict[int | None, dict] = {}
    for file in files:
        assay = assays.get(assay_ids.get(file.pk))
        key = assay.pk if assay else None
        group = groups.setdefault(
            key,
            {
                "assay": assay,
                "files": [],
                "available_ids": [],
                "panel_id": f"sharedFiles-{key or 'unlinked'}",
            },
        )
        group["files"].append(file)
        if file.status == FileAsset.Status.AVAILABLE:
            group["available_ids"].append(str(file.pk))
    return sorted(
        groups.values(),
        key=lambda group: (
            group["assay"] is None,
            group["assay"].title.lower() if group["assay"] else "",
        ),
    )


def withdraw_files(
    user: Person, file_ids: list[str], now: datetime | None = None
) -> int:
    """Stop sharing some of ``user``'s documents. Returns how many changed.

    The files are not used from now on, and deleted once the waiting period ends.
    """
    now = now or timezone.now()
    return FileAsset.objects.filter(
        uploaded_by=user,
        pk__in=_valid_ids(file_ids),
        status=FileAsset.Status.AVAILABLE,
    ).update(
        status=FileAsset.Status.WITHDRAWN,
        withdrawn_at=now,
        delete_after=now + timedelta(hours=config._file_withdrawal_grace_hours),
    )


def restore_files(
    user: Person, file_ids: list[str], now: datetime | None = None
) -> int:
    """Undo "stop sharing" for documents whose waiting period has not ended."""
    now = now or timezone.now()
    return FileAsset.objects.filter(
        uploaded_by=user,
        pk__in=_valid_ids(file_ids),
        status=FileAsset.Status.WITHDRAWN,
        delete_after__gt=now,
    ).update(status=FileAsset.Status.AVAILABLE, withdrawn_at=None, delete_after=None)


def delete_withdrawn_files(now: datetime | None = None) -> int:
    """Delete withdrawn documents whose waiting period is over. Returns how many.

    Each deletion is recorded per user and assay in ``FileWithdrawal`` (counts
    only). Files marked ``deleted`` after a failed draft are removed as well, so
    their stored objects do not linger.
    """
    now = now or timezone.now()
    due = list(
        FileAsset.objects.filter(
            status=FileAsset.Status.WITHDRAWN, delete_after__lte=now
        )
    )
    if due:
        assay_ids = _assay_ids(due)
        records: dict[tuple, dict] = {}
        for file in due:
            withdrawn_at = file.withdrawn_at or now
            record = records.setdefault(
                (file.uploaded_by_id, assay_ids.get(file.pk)),
                {"count": 0, "withdrawn_at": withdrawn_at},
            )
            record["count"] += 1
            record["withdrawn_at"] = min(record["withdrawn_at"], withdrawn_at)
        with transaction.atomic():
            FileWithdrawal.objects.bulk_create(
                FileWithdrawal(
                    user_id=user_id,
                    assay_id=assay_id,
                    file_count=record["count"],
                    withdrawn_at=record["withdrawn_at"],
                )
                for (user_id, assay_id), record in records.items()
            )
            for file in due:
                file.delete()
        logger.info("Deleted %d withdrawn shared documents", len(due))

    for file in FileAsset.objects.filter(status=FileAsset.Status.DELETED):
        file.delete()
    return len(due)


# ── Export and account deletion ───────────────────────────────────────────────


def accessible_assays(user: Person) -> QuerySet[Assay]:
    """Return every ToxTemp ``user`` can open, by the same rules as the overview."""
    investigations = get_objects_for_user(
        user,
        "toxtempass.view_investigation",
        klass=Investigation,
        use_groups=False,
        any_perm=False,
    )
    assays = get_objects_for_user(
        user, "toxtempass.view_assay", klass=Assay, use_groups=False, any_perm=False
    )
    return (
        (Assay.objects.filter(study__investigation__in=investigations) | assays)
        .distinct()
        .filter(question_set__isnull=False)
        .order_by("pk")
    )


def exportable_assays(user: Person) -> QuerySet[Assay]:
    """Return the ToxTemps ``user`` can take away: those they can open, minus demos.

    The read-only demo is seeded for every account from a template; it is not the
    user's own work.
    """
    demo = Q(demo_lock=True) | Q(demo_template=True) | Q(demo_source__isnull=False)
    return accessible_assays(user).exclude(demo)


def export_toxtemps_zip(user: Person) -> bytes:
    """Return a ZIP with every ToxTemp ``user`` can open, as JSON and Markdown.

    Meant for taking one's data before deleting the account: unlike the export on
    the assay page it does not ask for feedback first, and it leaves out the slow
    Pandoc formats. One folder per ToxTemp; one that fails to export is skipped.
    """
    from toxtempass.export import generate_json_from_assay, generate_markdown_from_assay

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, assay in enumerate(exportable_assays(user), start=1):
            folder = f"{number:03d}-{slugify(assay.title)[:60] or 'toxtemp'}"
            try:
                data = generate_json_from_assay(assay)
                markdown = generate_markdown_from_assay(assay)
            except Exception:
                logger.exception("Could not export assay %s for %s", assay.pk, user.pk)
                continue
            if data is not None:
                archive.writestr(
                    f"{folder}/toxtemp.json",
                    json.dumps(data, indent=2, ensure_ascii=False, default=str),
                )
            archive.writestr(f"{folder}/toxtemp.md", markdown)
    return buffer.getvalue()


def deletion_blockers(user: Person) -> dict:
    """Return what stops ``user`` from deleting their account; empty when nothing.

    Owned workspaces block it: they cannot be handed over, and deleting them on
    the user's behalf would take investigations away from other members unseen.
    """
    blockers: dict = {}
    owned = list(Workspace.objects.filter(owner=user).order_by("name"))
    if owned:
        blockers["owned_workspaces"] = owned
    # QuestionSet.created_by is PROTECT: maintainers who created a template version.
    if QuestionSet.objects.filter(created_by=user).exists():
        blockers["question_sets"] = True
    if user.is_superuser:
        blockers["superuser"] = True
    return blockers


def deletion_summary(user: Person) -> dict:
    """Describe what deleting ``user``'s account removes, for the confirmation."""
    investigations = Investigation.objects.filter(owner=user)
    shared_elsewhere = (
        WorkspaceInvestigation.objects.filter(investigation__in=investigations)
        .select_related("investigation", "workspace")
        .order_by("investigation__title", "workspace__name")
    )
    return {
        "investigation_count": investigations.count(),
        "shared_investigations": [
            f"{link.investigation.title} ({link.workspace.name})"
            for link in shared_elsewhere
        ],
        "document_count": shared_files(user).count(),
    }


def delete_account(person: Person) -> None:
    """Delete an account and everything that belongs to it.

    Investigations the person owns go first, with their studies, ToxTemps and
    answers (``Investigation.owner`` is PROTECT). Documents they uploaded go with
    the account, which removes the stored objects. Their queued and sent emails
    are removed. Studies and ToxTemps they created in other people's
    investigations stay there. Views check :func:`deletion_blockers` first.
    """
    with transaction.atomic():
        for investigation in Investigation.objects.filter(owner=person):
            investigation.delete()
        EmailLog.objects.filter(user=person).delete()
        person.delete()
