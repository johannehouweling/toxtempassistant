"""Match every not-yet-checked Person.organization against ROR, one call per name."""

import time

from django.core.management.base import BaseCommand, CommandParser
from django.db.models import F

from toxtempass.models import Person
from toxtempass.ror import RorLookupError, match_organization, save_match


class Command(BaseCommand):
    help = "Resolve Person.organization strings to ROR ids, one lookup per distinct name."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register --dry-run."""
        parser.add_argument(
            "--dry-run", action="store_true", help="Print matches without writing."
        )

    def handle(self, *args: object, **opts: object) -> None:
        """Look up each distinct unchecked organization once and store the result."""
        organizations = list(
            Person.objects.exclude(organization="")
            .exclude(organization=F("ror_checked_organization"))
            .order_by("organization")
            .values_list("organization", flat=True)
            .distinct()
        )
        matched = unmatched = errors = 0
        for i, organization in enumerate(organizations):
            if i:
                time.sleep(1)  # stay well inside the ROR rate limit
            try:
                match = match_organization(organization)
            except RorLookupError as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(f"error      {organization!r}: {exc}"))
                continue
            if match:
                matched += 1
                ror_id, ror_name = match
                self.stdout.write(f"matched    {organization!r} -> {ror_name} ({ror_id})")
            else:
                unmatched += 1
                self.stdout.write(f"unmatched  {organization!r}")
            if not opts["dry_run"]:
                save_match(
                    Person.objects.filter(organization=organization), organization, match
                )
        prefix = "[dry run] " if opts["dry_run"] else ""
        self.stdout.write(
            f"{prefix}{len(organizations)} organizations: {matched} matched, "
            f"{unmatched} unmatched, {errors} errors"
        )
