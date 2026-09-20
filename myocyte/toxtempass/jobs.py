"""The periodic job the task queue runs every few minutes.

Scheduled by ``manage.py setup_schedules``, which django_startup.sh calls.
"""

import logging

from django.utils import timezone

from toxtempass import fx, model_metadata, notifications, privacy

logger = logging.getLogger(__name__)


def run_periodic_jobs() -> None:
    """Send due emails, delete expired files, and refresh externally-sourced data.

    Each part is safe to repeat, and one failing does not stop the others. The
    two refreshes throttle themselves -- this job ticks every couple of minutes
    while the model catalogue changes daily at most and the Azure FX rate
    monthly -- so they are cheap no-ops between their own intervals.
    """
    now = timezone.now()
    for job in (
        notifications.run_email_jobs,
        privacy.delete_withdrawn_files,
        model_metadata.refresh,
        fx.refresh_fx_rate,
    ):
        try:
            job(now)
        except Exception:
            logger.exception("Periodic job %s failed", job.__name__)
