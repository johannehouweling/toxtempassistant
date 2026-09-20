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
from collections.abc import Iterable

from django.core.management.base import BaseCommand
from django.db.models import Count, QuerySet

from toxtempass import config
from toxtempass.demo import DEMO_ASSAY
from toxtempass.filehandling import estimate_token_count
from toxtempass.models import Answer
from toxtempass.utilities import is_standard_abstention

PERCENTILES = (50, 75, 90, 95, 99, 100)


def _percentile(sorted_values: list[int], pct: int) -> int:
    """Return the ``pct``-th percentile of an already-sorted list."""
    if not sorted_values:
        return 0
    if pct >= 100:
        return sorted_values[-1]
    index = (len(sorted_values) - 1) * pct // 100
    return sorted_values[index]


def _split(texts: Iterable[str | None]) -> tuple[list[int], int]:
    """Return ``(token counts of substantive answers, abstention count)``.

    Uses the same check that records ``Answer.llm_abstained`` rather than
    matching the phrase here, so a paraphrase from a different model is caught
    too.
    """
    kept: list[int] = []
    skipped = 0
    for text in texts:
        if not text or not text.strip():
            continue
        if is_standard_abstention(text):
            skipped += 1
            continue
        kept.append(estimate_token_count(text))
    return kept, skipped


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

    def _report_abstentions(self, label: str, kept: list[int], skipped: int) -> None:
        """Report how much of the cohort was the model declining to answer.

        These are excluded from the length figures below: "Answer not found in
        documents." is six tokens, and enough of them drag a median to six
        tokens while saying nothing about how long a real answer runs.
        """
        total = len(kept) + skipped
        if not total:
            return
        self.stdout.write(
            f"{label:<9} {skipped:,} of {total:,} "
            f"({skipped * 100 // total}%) were standard abstentions, excluded below"
        )

    def _report_billed(self) -> None:
        """Compare billed completion tokens with the visible text stored.

        ``max_completion_tokens`` budgets visible output and invisible
        reasoning tokens together, so sizing it from stored answer text alone
        is wrong. Billed completion tokens include both; the gap between the
        two is what reasoning costs, and that decides whether a cap near the
        visible p99 is safe or reckless.

        Both counters are per *run* -- one call to the drafting task, many
        questions -- so this reports run totals and a per-answer mean, not a
        per-call p99.
        """
        from toxtempass.models import LLMRun

        runs = list(
            LLMRun.objects.filter(output_tokens__gt=0).values_list(
                "output_tokens", "assay_id"
            )
        )
        if not runs:
            self.stdout.write(
                self.style.WARNING(
                    "billed: no LLMRun rows with output tokens yet, so the "
                    "reasoning overhead cannot be measured and any output cap "
                    "is a guess."
                )
            )
            return
        totals = sorted(t for t, _ in runs)
        self.stdout.write(
            self.style.HTTP_INFO(
                f"\nbilled completion tokens per RUN  (n={len(totals)})"
            )
        )
        for pct in PERCENTILES:
            name = "max" if pct >= 100 else f"p{pct}"
            self.stdout.write(f"  {name:<6} {_percentile(totals, pct):>8,} tokens")

        counts = dict(
            Answer.objects.filter(assay_id__in={a for _, a in runs})
            .values_list("assay_id")
            .annotate(n=Count("id"))
        )
        per_answer = sorted(
            total // counts[assay_id]
            for total, assay_id in runs
            if counts.get(assay_id)
        )
        if per_answer:
            self.stdout.write(
                "\n  billed per answer (a run's total over its answers): "
                f"p50 {_percentile(per_answer, 50):,} | "
                f"p95 {_percentile(per_answer, 95):,} | "
                f"max {_percentile(per_answer, 100):,} tokens"
            )
            self.stdout.write(
                "  Against the visible lengths below, the difference is what "
                "reasoning costs -- an output cap has to cover both."
            )

    def handle(self, *args: object, **options: object) -> None:
        """Measure both cohorts and suggest a reservation."""
        include_demo = bool(options["include_demo"])

        answers = Answer.objects.exclude(answer_text="").exclude(answer_text=None)
        if not include_demo:
            answers = answers.exclude(assay__in=_demo_assay_ids())
        current, current_abstained = _split(
            answers.values_list("answer_text", flat=True).iterator()
        )

        historical = Answer.history.model.objects.filter(
            history_user__isnull=True
        ).exclude(answer_text="").exclude(answer_text=None)
        if not include_demo:
            historical = historical.exclude(assay_id__in=_demo_assay_ids())
        drafts, drafts_abstained = _split(
            historical.values_list("answer_text", flat=True).iterator()
        )
        self._report_abstentions("history", drafts, drafts_abstained)
        self._report_abstentions("current", current, current_abstained)

        self._report_billed()
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
