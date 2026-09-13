"""Ask accounts from before email confirmation existed to confirm their address."""

from django.core.management.base import BaseCommand, CommandParser

from toxtempass import notifications
from toxtempass.models import EmailLog, Person


class Command(BaseCommand):
    help = (
        "Email every unconfirmed account that existed before email confirmation, "
        "asking it to confirm its address. Each account is asked once, so the "
        "command can be run again, e.g. in batches with --limit."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the command options."""
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the accounts that would be emailed, without sending anything.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Email at most this many accounts in this run.",
        )

    def handle(self, *args, **options) -> None:
        """Send the confirmation requests."""
        people = (
            Person.objects.filter(
                email_confirmed_at__isnull=True,
                delete_if_unconfirmed=False,
                is_staff=False,
                is_superuser=False,
            )
            .exclude(
                email_logs__dedup_key__startswith=(
                    f"{notifications.EMAIL_CONFIRMATION}:request:"
                )
            )
            .order_by("pk")
        )
        if options["limit"] is not None:
            people = people[: options["limit"]]

        counts = {"sent": 0, "retry": 0, "not sent": 0}
        for person in people:
            if options["dry_run"]:
                self.stdout.write(f"Would email {person.email}")
                continue
            log = notifications.request_email_confirmation(person)
            if log is None:
                continue
            # Outside a transaction the email is sent before this returns.
            log.refresh_from_db()
            if log.status == EmailLog.Status.SENT:
                counts["sent"] += 1
            elif log.status == EmailLog.Status.PENDING:
                counts["retry"] += 1
            else:
                counts["not sent"] += 1
                self.stderr.write(f"Not sent to {person.email}: {log.error}")

        if not options["dry_run"]:
            self.stdout.write(
                f"Sent {counts['sent']}, will retry {counts['retry']}, "
                f"not sent {counts['not sent']}."
            )
