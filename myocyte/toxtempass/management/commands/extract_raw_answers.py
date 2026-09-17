"""Thin management command for the gold_standard raw dump.

Delegates to ``toxtempass.evaluation.gold_standard.audit.dump_raw`` (mirrors how
``extract_gold_answers`` delegates to ``audit.run``). Read-only; dumps every answer of
every non-demo assay, every saved version of those answers, and the per-assay LLM cost
rows — the inputs the uptake/quality table needs and the gold CSV cannot supply.

    sudo docker exec djangoapp python manage.py extract_raw_answers --out /tmp/raw
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandParser

from toxtempass.evaluation.gold_standard import audit


class Command(BaseCommand):
    """Dump answers + full version history + cost rows as three CSVs (read-only)."""

    help = "Read-only: dump all answers, their version history and LLM cost rows."

    def add_arguments(self, parser: CommandParser) -> None:
        """Register CLI options."""
        parser.add_argument(
            "--out",
            default="",
            help="Path STEM for the three CSVs (/tmp/raw → /tmp/raw_answers.csv, "
            "_history.csv, _costs.csv). A directory gets 'raw' inside it. Default: "
            "output/_analysis/raw_*.csv.",
        )
        parser.add_argument(
            "--exclude-emails",
            default="",
            help="Comma-separated owner emails to drop (e.g. test accounts).",
        )
        parser.add_argument(
            "--min-accepted",
            type=int,
            default=0,
            help="Only assays with at least this many accepted answers. Default 0 — an "
            "assay nobody reviewed is still uptake and belongs in the denominator.",
        )
        parser.add_argument(
            "--limit", type=int, default=None, help="Cap assays processed (quick run)."
        )

    def handle(self, *args: object, **options: object) -> None:
        """Run the dump and print what was written."""
        summary = audit.dump_raw(
            {
                "out": options["out"],
                "exclude_emails": options["exclude_emails"],
                "min_accepted": options["min_accepted"],
                "limit": options["limit"],
            }
        )
        w = self.stdout.write
        w("")
        w("RAW ANSWER DUMP  (read-only)")
        w(f"  assays                      : {summary['n_assays']}")
        w(f"  answers                     : {summary['n_answers']}")
        w(f"    drafted by an LLM run     : {summary['n_drafted']}")
        w(f"    accepted                  : {summary['n_accepted']}")
        w(f"  history rows                : {summary['n_history_rows']}")
        w(f"    saved after a drafting run: {summary['n_post_draft_saves']}")
        w(f"  LLM cost rows               : {summary['n_cost_rows']}")
        for name, path in summary["paths"].items():
            w(f"  wrote {name:8} → {path}")
        w("")
        w("  These CSVs hold answer text and owner emails — delete them from the")
        w("  server once copied off it.")
        w("")
