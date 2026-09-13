"""Create or update the task-queue schedule that runs the email jobs."""

from django.core.management.base import BaseCommand
from django_q.models import Schedule

from toxtempass import config

SCHEDULE_NAME = "toxtempass email jobs"


class Command(BaseCommand):
    help = (
        "Schedule toxtempass.notifications.run_email_jobs in the task queue. "
        "Safe to run on every start."
    )

    def handle(self, *args, **options) -> None:
        """Create the schedule, or bring an existing one up to date."""
        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": "toxtempass.notifications.run_email_jobs",
                "schedule_type": Schedule.MINUTES,
                "minutes": config._email_jobs_interval_minutes,
                "repeats": -1,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            f"{action} schedule '{SCHEDULE_NAME}': every {schedule.minutes} minutes."
        )
