from __future__ import annotations

import logging

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from toxtempass.demo import seed_demo_assay_for_user
from toxtempass.tasks import queue_ror_lookup

from .models import FileAsset, Person

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=FileAsset)
def delete_object_from_storage(sender:FileAsset, instance: FileAsset, **kwargs) -> None:
    """Remove the object from storage (S3/MinIO) after the DB row is deleted."""
    key = (instance.object_key or "").strip()
    if not key:
        return

    try:
        if default_storage.exists(key):
            default_storage.delete(key)
    except Exception:
        # Decide your policy: log and swallow, or re-raise
        logger.exception("Failed to delete storage object: %s", key)

@receiver(post_save, sender=Person, dispatch_uid="person_seed_demo_assay")
def seed_demo(sender:Person, instance: Person, created: bool, **kwargs) -> None:
    """Seed a demo assay for newly created users."""
    if not created:
            return
    seed_demo_assay_for_user(instance)


@receiver(post_save, sender=Person, dispatch_uid="person_resolve_ror")
def resolve_ror(sender: Person, instance: Person, **kwargs: object) -> None:
    """Queue a ROR lookup when the organization changed since the last check.

    A cleared organization is queued too; resolve_person then blanks the ROR fields
    without calling the API. Fixture loads and saves that do not write organization
    (e.g. update_last_login on every login) are skipped, so an unchecked account
    does not queue another lookup each time it signs in.
    """
    if not settings.ROR_LOOKUP_ENABLED or kwargs.get("raw"):
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "organization" not in update_fields:
        return
    if instance.organization != instance.ror_checked_organization:
        pk = instance.pk
        transaction.on_commit(lambda: queue_ror_lookup(pk))
