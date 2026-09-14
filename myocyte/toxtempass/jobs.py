"""The periodic job the task queue runs every few minutes.

Scheduled by ``manage.py setup_schedules``, which django_startup.sh calls.
"""

import logging

from django.utils import timezone

from toxtempass import notifications, privacy

logger = logging.getLogger(__name__)


def run_periodic_jobs() -> None:
    """Send due emails and delete withdrawn files whose waiting period is over.

    Each part is safe to repeat, and one failing does not stop the other.
    """
    now = timezone.now()
    for job in (notifications.run_email_jobs, privacy.delete_withdrawn_files):
        try:
            job(now)
        except Exception:
            logger.exception("Periodic job %s failed", job.__name__)
