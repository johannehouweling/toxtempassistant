"""Create or update the task-queue schedule that runs the periodic jobs."""

from django.core.management.base import BaseCommand
from django_q.models import Schedule

from toxtempass import config

SCHEDULE_NAME = "toxtempass periodic jobs"
# Earlier names of the same schedule, removed so the jobs do not run twice.
PREVIOUS_SCHEDULE_NAMES = ("toxtempass email jobs",)


class Command(BaseCommand):
    help = (
        "Schedule toxtempass.jobs.run_periodic_jobs (emails and deletion of "
        "withdrawn shared documents) in the task queue. Safe to run on every start."
    )

    def handle(self, *args, **options) -> None:
        """Create the schedule, or bring an existing one up to date."""
        removed, _ = Schedule.objects.filter(name__in=PREVIOUS_SCHEDULE_NAMES).delete()
        if removed:
            self.stdout.write(f"Removed {removed} schedule(s) under a previous name.")
        schedule, created = Schedule.objects.update_or_create(
            name=SCHEDULE_NAME,
            defaults={
                "func": "toxtempass.jobs.run_periodic_jobs",
                "schedule_type": Schedule.MINUTES,
                "minutes": config._periodic_jobs_interval_minutes,
                "repeats": -1,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(
            f"{action} schedule '{SCHEDULE_NAME}': every {schedule.minutes} minutes."
        )
