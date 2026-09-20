"""READ-ONLY: measure how long the answers the model writes actually are.

The token budget reserves a flat ``Config.context_window_headroom_tokens`` for
the prompts and the response. That constant was picked without evidence, and on
a model with a small window it is the difference between a document fitting and
not. This measures the distribution so the reservation can be set from data.

Two cohorts are reported, because they answer slightly different questions:

* ``history`` -- snapshots with no ``history_user``, which is the worker
  writing a draft rather than a person editing one. This is the model's own
  output and the right basis for an output cap. Only available for drafts
  written after drafting started going through ``save()``.
* ``current`` -- every non-empty ``Answer.answer_text`` now. Broader, covers
  the older data, but includes human edits.

Aggregates only: counts and percentiles, never answer text.

    sudo docker compose exec djangoapp python manage.py answer_length_stats
    sudo docker compose exec djangoapp python manage.py answer_length_stats --include-demo
"""

from __future__ import annotations

from argparse import ArgumentParser

from django.core.management.base import BaseCommand
from django.db.models import QuerySet

from toxtempass import config
from toxtempass.demo import DEMO_ASSAY
from toxtempass.filehandling import estimate_token_count
from toxtempass.models import Answer

PERCENTILES = (50, 75, 90, 95, 99, 100)


def _percentile(sorted_values: list[int], pct: int) -> int:
    """Return the ``pct``-th percentile of an already-sorted list."""
    if not sorted_values:
        return 0
    if pct >= 100:
        return sorted_values[-1]
    index = (len(sorted_values) - 1) * pct // 100
    return sorted_values[index]


def _demo_assay_ids() -> "QuerySet":
    """Return the ids of seeded demo assays, which nobody wrote."""
    from toxtempass.models import Assay

    return Assay.objects.filter(DEMO_ASSAY).values("id")


class Command(BaseCommand):
    help = "Read-only: token-length distribution of answers, to size the headroom."

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register the command's options."""
        parser.add_argument(
            "--include-demo",
            action="store_true",
            help="Include the seeded demo assays (excluded by default).",
        )

    def _report(self, label: str, lengths: list[int]) -> int:
        """Print the distribution for one cohort and return its 99th percentile."""
        if not lengths:
            self.stdout.write(self.style.WARNING(f"{label}: no rows"))
            return 0
        lengths.sort()
        total = sum(lengths)
        self.stdout.write(self.style.HTTP_INFO(f"\n{label}  (n={len(lengths)})"))
        self.stdout.write(f"  mean   {total // len(lengths):>8,} tokens")
        for pct in PERCENTILES:
            name = "max" if pct >= 100 else f"p{pct}"
            self.stdout.write(f"  {name:<6} {_percentile(lengths, pct):>8,} tokens")
        return _percentile(lengths, 99)

    def handle(self, *args: object, **options: object) -> None:
        """Measure both cohorts and suggest a reservation."""
        include_demo = bool(options["include_demo"])

        answers = Answer.objects.exclude(answer_text="").exclude(answer_text=None)
        if not include_demo:
            answers = answers.exclude(assay__in=_demo_assay_ids())
        current = [
            estimate_token_count(text)
            for text in answers.values_list("answer_text", flat=True).iterator()
            if text
        ]

        historical = Answer.history.model.objects.filter(
            history_user__isnull=True
        ).exclude(answer_text="").exclude(answer_text=None)
        if not include_demo:
            historical = historical.exclude(assay_id__in=_demo_assay_ids())
        drafts = [
            estimate_token_count(text)
            for text in historical.values_list("answer_text", flat=True).iterator()
            if text
        ]

        p99_draft = self._report("history  (worker-written drafts)", drafts)
        p99_current = self._report("current  (includes human edits)", current)

        basis = p99_draft or p99_current
        headroom = config.context_window_headroom_tokens
        if not basis:
            self.stdout.write(
                self.style.WARNING(
                    "\nNo answers to measure, so this says nothing about the "
                    f"current {headroom:,}-token headroom."
                )
            )
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"\nHeadroom is currently {headroom:,} tokens, covering the "
                "prompts and the response together.\n"
                f"The 99th-percentile answer is {basis:,} tokens."
            )
        )
        self.stdout.write(
            "Sizing from this: the reservation still has to cover the base "
            "prompt, the assay title and description, the question, and any "
            "subsection context -- the answer is only one part of it. And the "
            "tail is what matters, not the mean: one question needing a long "
            "answer costs more than the context it would free."
        )
