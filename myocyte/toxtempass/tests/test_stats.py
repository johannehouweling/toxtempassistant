"""Tests for the staff-only KPI dashboard (/stats) and its aggregation layer."""

import csv
import datetime as dt
import io
import json
import tempfile
import uuid
from pathlib import Path

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from toxtempass import config
from toxtempass.models import (
    Answer,
    AnswerFile,
    Assay,
    AssayCost,
    AssayTimeLog,
    Feedback,
    LLMStatus,
    Person,
    QuestionSet,
)
from toxtempass.stats import (
    build_stats,
    cached_stats,
    clear_stats_cache,
    humanize_seconds,
    people,
    real_assays,
    resolve_range,
    to_csv_rows,
    to_json_payload,
)
from toxtempass.tests.fixtures.factories import (
    AdminFactory,
    AnswerFactory,
    AssayFactory,
    FileAssetFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionFactory,
    QuestionSetFactory,
    StudyFactory,
    WorkspaceFactory,
    WorkspaceInvestigationFactory,
    WorkspaceMemberFactory,
)


def _person(**kwargs) -> Person:
    """Create an account that counts as a user.

    PersonFactory's default e-mail ends in @test.com, which /stats excludes as a
    synthetic account, so counted users need a real-looking domain.
    """
    kwargs.setdefault("email", f"kpi.{uuid.uuid4().hex[:12]}@example.org")
    return PersonFactory.create(**kwargs)


def _assay_for(owner: Person, **kwargs) -> Assay:
    """Create a real (non-demo) assay owned by ``owner``."""
    investigation = InvestigationFactory.create(owner=owner)
    study = StudyFactory.create(investigation=investigation)
    return AssayFactory.create(study=study, **kwargs)


def _set_time(model, pk, field: str, value: dt.datetime) -> None:
    """Overwrite an auto_now_add timestamp, which create() cannot set."""
    model.objects.filter(pk=pk).update(**{field: value})


def _backdate(assay: Assay, days: int) -> None:
    """Move ``assay``'s creation ``days`` into the past."""
    past = timezone.now() - dt.timedelta(days=days)
    _set_time(Assay, assay.pk, "submission_date", past)


class StatsRangeTests(TestCase):
    def test_unknown_key_falls_back_to_default(self):
        rng = resolve_range("not-a-range")
        self.assertEqual(rng.key, config.stats_default_range)

    def test_all_time_has_no_lower_bound(self):
        self.assertIsNone(resolve_range("all").since)
        self.assertTrue(resolve_range("all").is_all_time)

    def test_30d_window_is_thirty_days_wide(self):
        now = timezone.now()
        rng = resolve_range("30d", now=now)
        self.assertEqual((now - rng.since).days, 30)
        self.assertEqual(rng.bucket, "day")

    def test_humanize_seconds(self):
        self.assertEqual(humanize_seconds(None), "—")
        self.assertEqual(humanize_seconds(45), "45s")
        self.assertEqual(humanize_seconds(125), "2m 05s")
        self.assertEqual(humanize_seconds(7320), "2h 02m")
        # Draft-to-export spans calendar time, so days must not collapse into hours.
        self.assertEqual(humanize_seconds(86400 * 9 + 3600 * 4), "9d 4h")
        self.assertEqual(humanize_seconds(86400 * 2), "2d")


class StatsAggregationTests(TestCase):
    def setUp(self):
        self.user = _person(organization="Utrecht University")
        self.other = _person(organization="RIVM")
        # Pin the QuestionSet label: QuestionSetFactory's "v{n}" sequence keeps
        # counting across tests and eventually hits the "v1" set the migrations
        # already seeded. Both questions share a subsection so only one set exists.
        self.question = QuestionFactory.create(
            subsection__section__question_set__label="kpitest"
        )
        self.question2 = QuestionFactory.create(subsection=self.question.subsection)
        self.question_set = self.question.subsection.section.question_set

        # One fully accepted assay for self.user
        self.done = _assay_for(
            self.user, status=LLMStatus.DONE, question_set=self.question_set
        )
        AnswerFactory.create(assay=self.done, question=self.question, accepted=True)

        # One partially accepted assay for self.other
        self.partial = _assay_for(
            self.other, status=LLMStatus.DONE, question_set=self.question_set
        )
        AnswerFactory.create(assay=self.partial, question=self.question, accepted=True)
        AnswerFactory.create(
            assay=self.partial,
            question=self.question2,
            accepted=False,
            answer_text=config.not_found_string,
        )

    # ── Exclusions ───────────────────────────────────────────────────────────

    def test_demo_assays_are_excluded(self):
        template = _assay_for(self.user, demo_template=True)
        copy = _assay_for(self.user, demo_lock=True, demo_source=template)
        pks = set(real_assays().values_list("pk", flat=True))
        self.assertNotIn(template.pk, pks)
        self.assertNotIn(copy.pk, pks)
        self.assertIn(self.done.pk, pks)

    def test_staff_created_toxtemp_is_excluded(self):
        staff = AdminFactory.create(email="staff.creator@example.org")
        by_staff = _assay_for(self.user, created_by=staff)
        pks = set(real_assays().values_list("pk", flat=True))
        self.assertNotIn(by_staff.pk, pks)
        # No recorded creator is not a reason to drop a ToxTemp.
        self.assertIsNone(self.done.created_by_id)
        self.assertIn(self.done.pk, pks)

    def test_toxtemp_in_staff_owned_investigation_is_excluded(self):
        staff = AdminFactory.create(email="staff.owner@example.org")
        in_staff_investigation = _assay_for(staff, created_by=self.user)
        self.assertNotIn(
            in_staff_investigation.pk, set(real_assays().values_list("pk", flat=True))
        )
        self.assertNotIn(staff.pk, set(people().values_list("pk", flat=True)))

    def test_synthetic_account_and_its_toxtemps_are_excluded(self):
        synthetic = PersonFactory.create(organization="Evaluation Harness")
        self.assertTrue(synthetic.email.endswith("@test.com"))
        owned = _assay_for(synthetic)
        created = _assay_for(self.user, created_by=synthetic)
        pks = set(real_assays().values_list("pk", flat=True))
        self.assertNotIn(owned.pk, pks)
        self.assertNotIn(created.pk, pks)
        self.assertNotIn(synthetic.pk, set(people().values_list("pk", flat=True)))
        labels = [row["organisation"] for row in build_stats("all")["organisations"]]
        self.assertNotIn("Evaluation Harness", labels)

    def test_demo_feedback_and_costs_are_excluded(self):
        template = _assay_for(self.user, demo_template=True)
        copy = _assay_for(self.user, demo_lock=True, demo_source=template)
        Feedback.objects.create(
            user=self.user, assay=copy, feedback_text="x", usefulness_rating=1.0
        )
        AssayCost.objects.create(
            assay=template,
            model_key="1:GPT4O",
            input_tokens=999,
            cost_input="1.000000",
            cost_output="1.000000",
        )
        stats = build_stats("all")
        self.assertEqual(stats["feedback"]["count"], 0)
        self.assertIsNone(stats["feedback"]["mean"])
        self.assertEqual(stats["llm"]["runs"], 0)
        self.assertEqual(stats["llm"]["input_tokens"], 0)
        self.assertEqual(stats["llm"]["cost_total"], 0.0)
        self.assertEqual(stats["engagement"]["export_samples"], 0)

    def test_guardian_anonymous_user_is_not_counted(self):
        from django.conf import settings

        sentinel = getattr(settings, "ANONYMOUS_USER_NAME", "AnonymousUser")
        Person.objects.get_or_create(**{Person.USERNAME_FIELD: sentinel})
        real = (
            Person.objects.exclude(**{Person.USERNAME_FIELD: sentinel})
            .exclude(is_staff=True)
            .exclude(email__iendswith="@test.com")
            .count()
        )
        self.assertEqual(build_stats("all")["headline"]["users"]["total"], real)

    # ── Headline ─────────────────────────────────────────────────────────────

    def test_headline_counts_real_assays_only(self):
        _assay_for(self.user, demo_template=True)
        stats = build_stats("all")
        self.assertEqual(stats["headline"]["assays"]["total"], 2)
        self.assertEqual(stats["headline"]["completed_assays"]["total"], 1)

    def test_assay_bands_sum_to_the_created_count(self):
        """The three bands on the headline card must reconcile to the total.

        They are rendered as one bar with a key each, so if they do not add up
        a reader doing the subtraction sees a number that is simply wrong.
        """
        _assay_for(self.user, question_set=self.question_set)  # no answers
        for key in ("all", "12m"):
            with self.subTest(range=key):
                head = build_stats(key)["headline"]
                self.assertEqual(
                    head["completed_assays"]["period"]
                    + head["assays"]["in_progress"]
                    + head["assays"]["untouched"],
                    head["assays"]["period"],
                )

    def test_assay_band_percentages_sum_to_a_hundred(self):
        head = build_stats("all")["headline"]
        self.assertAlmostEqual(
            head["completed_assays"]["pct"]
            + head["assays"]["in_progress_pct"]
            + head["assays"]["untouched_pct"],
            100.0,
            places=1,
        )

    def test_range_filter_excludes_older_rows(self):
        old = _assay_for(self.user)
        _backdate(old, 400)
        self.assertEqual(build_stats("all")["headline"]["assays"]["total"], 3)
        self.assertEqual(build_stats("30d")["headline"]["assays"]["period"], 2)

    # ── Institutions ─────────────────────────────────────────────────────────

    def test_organisation_rows_are_institutions_not_people(self):
        rows = {row["organisation"]: row for row in build_stats("all")["organisations"]}
        self.assertIn("Utrecht University", rows)
        self.assertIn("RIVM", rows)
        self.assertEqual(rows["Utrecht University"]["assays"], 1)
        self.assertEqual(rows["Utrecht University"]["completed"], 1)
        self.assertEqual(rows["RIVM"]["completed"], 0)

    def test_blank_organisation_is_pooled(self):
        _person(organization="")
        labels = [row["organisation"] for row in build_stats("all")["organisations"]]
        self.assertIn(config.stats_unknown_organisation, labels)
        self.assertNotIn("", labels)

    def test_institutions_group_by_ror_and_count_new_ones(self):
        ror_id = "https://ror.org/01cesdt21"
        ror_name = "National Institute for Public Health and the Environment"
        for organization in (
            "RIVM Bilthoven", "Rijksinstituut voor Volksgezondheid en Milieu"
        ):
            _person(
                organization=organization,
                ror_id=ror_id,
                ror_name=ror_name,
                ror_checked_organization=organization,
            )
        # Organization edited since the match was made: the old id no longer applies.
        _person(
            organization="Leiden University",
            ror_id=ror_id,
            ror_name=ror_name,
            ror_checked_organization="RIVM Bilthoven",
        )
        early = _person(organization=" utrecht university ")
        _person(organization="Utrecht University")
        old = _person(organization="Old Institute")
        long_ago = timezone.now() - dt.timedelta(days=400)
        Person.objects.filter(pk__in=[early.pk, old.pk]).update(date_joined=long_ago)

        rows = {r["organisation"]: r for r in build_stats("all")["organisations"]}
        # Matched accounts are one institution, shown by its ROR display name.
        self.assertEqual(rows[ror_name]["users"], 2)
        self.assertNotIn("RIVM Bilthoven", rows)
        self.assertEqual(rows["Leiden University"]["users"], 1)
        # Unmatched spellings fold on case and whitespace, shown by the commonest.
        self.assertEqual(rows["Utrecht University"]["users"], 3)
        self.assertNotIn("utrecht university", rows)

        # Utrecht, RIVM (unmatched, from setUp), the ROR match, Leiden, Old Institute.
        head = build_stats("all")["headline"]["organisations"]
        self.assertEqual(head, {"total": 5, "period": 5})
        # Utrecht's and Old Institute's earliest accounts predate the window.
        new = build_stats("30d")["headline"]["organisations"]
        self.assertEqual(new, {"total": 5, "period": 3})

    def test_demo_investigation_is_not_counted_for_an_institution(self):
        template = _assay_for(
            self.user, demo_template=True, question_set=self.question_set
        )
        newcomer = _person(organization="Demo Institute")  # the signal seeds a demo copy
        self.assertTrue(
            Assay.objects.filter(
                demo_source=template, study__investigation__owner=newcomer
            ).exists()
        )
        rows = {r["organisation"]: r for r in build_stats("all")["organisations"]}
        self.assertEqual(rows["Demo Institute"]["investigations"], 0)
        # The template's own investigation holds no counted ToxTemp either.
        self.assertEqual(rows["Utrecht University"]["investigations"], 1)

    def test_toxtemps_follow_their_creator_and_unknown_sets_no_scale(self):
        # Created by the RIVM user inside a Utrecht investigation.
        _assay_for(self.user, created_by=self.other)
        nobody = _person(organization="")
        for _ in range(3):
            _assay_for(nobody)

        rows = {r["organisation"]: r for r in build_stats("all")["organisations"]}
        self.assertEqual(rows["RIVM"]["assays"], 2)
        self.assertEqual(rows["Utrecht University"]["assays"], 1)
        unknown = rows[config.stats_unknown_organisation]
        self.assertEqual(unknown["assays"], 3)
        # The pooled row is the busiest, but it gets no bar and sets no scale.
        self.assertIsNone(unknown["share"])
        self.assertEqual(rows["RIVM"]["share"], 100.0)
        self.assertEqual(rows["Utrecht University"]["share"], 50.0)

    # ── Growth ───────────────────────────────────────────────────────────────

    def test_growth_series_fills_empty_buckets(self):
        """An empty month must render as a zero, not compress the time axis."""
        old = _assay_for(self.user)
        _backdate(old, 90)
        growth = build_stats("12m")["growth"]
        self.assertEqual(len(growth["labels"]), len(growth["assays"]))
        self.assertEqual(len(growth["labels"]), len(growth["users"]))
        self.assertGreaterEqual(len(growth["labels"]), 3)
        self.assertIn(0, growth["assays"])

    def test_growth_runs_through_the_current_bucket(self):
        """A quiet spell must show at the right edge, not end the line early."""
        past = timezone.now() - dt.timedelta(days=90)
        Assay.objects.update(submission_date=past)
        Person.objects.update(date_joined=past)
        today = timezone.localdate()
        for key, fmt in (("all", "%b %Y"), ("12m", "%b %Y"), ("30d", "%d %b")):
            with self.subTest(range=key):
                growth = build_stats(key)["growth"]
                self.assertEqual(growth["labels"][-1], today.strftime(fmt))
                self.assertEqual(growth["assays"][-1], 0)
                self.assertEqual(growth["users"][-1], 0)
        # All time starts at the first bucket with data.
        first = build_stats("all")["growth"]["labels"][0]
        self.assertEqual(first, timezone.localtime(past).strftime("%b %Y"))

    def test_cumulative_series_never_decreases(self):
        old = _assay_for(self.user)
        _backdate(old, 60)
        growth = build_stats("all")["growth"]
        running = growth["assays_cumulative"]
        self.assertEqual(running, sorted(running))
        self.assertEqual(running[-1], sum(growth["assays"]))
        self.assertEqual(len(running), len(growth["labels"]))

    def test_windowed_cumulative_starts_from_prior_total(self):
        """A windowed curve must not restart at zero at the window edge."""
        old = _assay_for(self.user)
        _backdate(old, 400)
        growth = build_stats("12m")["growth"]
        # The pre-window assay is excluded from the per-bucket counts but must
        # still be carried in the running total.
        self.assertGreater(growth["assays_cumulative"][0], growth["assays"][0])
        self.assertEqual(growth["assays_cumulative"][-1], real_assays().count())

    # ── Answer bands ─────────────────────────────────────────────────────────

    def test_answer_quality_rates(self):
        answers = build_stats("all")["answers"]
        self.assertEqual(answers["total"], 3)
        self.assertEqual(answers["accepted"], 2)
        self.assertEqual(answers["not_found"], 1)

    def test_average_progress_is_a_mean_of_per_assay_shares(self):
        """One fully accepted and one half accepted averages to 75%, not 66%.

        Pooling would give 2 accepted of 3 questions = 66.7%; averaging each
        ToxTemp's own share gives (100 + 50) / 2 = 75%.
        """
        progress = build_stats("all")["progress"]
        self.assertEqual(progress["assays"], 2)
        self.assertEqual(progress["accepted"], 75.0)
        self.assertEqual(progress["drafted"], 0.0)
        self.assertEqual(progress["not_found"], 25.0)
        self.assertEqual(progress["empty"], 0.0)

    def test_four_answer_bands(self):
        subsection = self.question.subsection
        extra = [QuestionFactory.create(subsection=subsection) for _ in range(2)]
        mixed = _assay_for(self.user, question_set=self.question_set)
        # Accepted wins whatever the text, even the not-found sentence.
        AnswerFactory.create(
            assay=mixed, question=self.question, accepted=True,
            answer_text=config.not_found_string,
        )
        # Not-found matches case-insensitively, and NULL counts as not accepted.
        AnswerFactory.create(
            assay=mixed, question=self.question2, accepted=None,
            answer_text=f"Sorry: {config.not_found_string.lower()}",
        )
        AnswerFactory.create(assay=mixed, question=extra[0], accepted=False)
        AnswerFactory.create(
            assay=mixed, question=extra[1], accepted=None, answer_text=""
        )
        _assay_for(self.user)  # never drafted: not part of the base

        progress = build_stats("all")["progress"]
        # done (100/0/0/0), partial (50/0/50/0), mixed (25/25/25/25)
        self.assertEqual(progress["assays"], 3)
        self.assertEqual(progress["accepted"], 58.3)
        self.assertEqual(progress["drafted"], 8.3)
        self.assertEqual(progress["not_found"], 25.0)
        self.assertEqual(progress["empty"], 8.3)

    def test_progress_bands_always_sum_to_a_hundred(self):
        for key in ("all", "12m"):
            with self.subTest(range=key):
                p = build_stats(key)["progress"]
                self.assertAlmostEqual(
                    p["accepted"] + p["drafted"] + p["not_found"] + p["empty"],
                    100.0,
                    places=1,
                )

    def test_undrafted_assay_is_left_out_of_the_average(self):
        """Answer rows are seeded on first upload; without them there is no draft."""
        _assay_for(self.user)  # no answers at all
        progress = build_stats("all")["progress"]
        self.assertEqual(progress["assays"], 2)
        self.assertEqual(progress["accepted"], 75.0)
        # The gap to the created count is reported, so the page can explain it.
        self.assertEqual(progress["without_answers"], 1)

    def test_section_progress_is_scoped_to_the_busiest_question_set(self):
        section = self.question.subsection.section
        progress = build_stats("all")["section_progress"]
        self.assertEqual(progress["assays"], 2)
        titles = [entry["title"] for entry in progress["sections"]]
        self.assertEqual(titles, [section.title])
        band = progress["sections"][0]
        self.assertAlmostEqual(
            band["accepted"] + band["drafted"] + band["not_found"] + band["empty"],
            100.0,
            places=1,
        )
        self.assertEqual(band["accepted"], 75.0)
        self.assertEqual(band["questions"], 2)

    def test_section_question_count_comes_from_the_questionnaire(self):
        QuestionFactory.create(subsection=self.question.subsection)  # never answered
        _assay_for(self.user, question_set=self.question_set)  # undrafted
        progress = build_stats("all")["section_progress"]
        self.assertEqual(progress["sections"][0]["questions"], 3)
        self.assertEqual(progress["assays"], 2)

    def test_section_progress_is_empty_without_a_question_set(self):
        Assay.objects.update(question_set=None)
        progress = build_stats("all")["section_progress"]
        self.assertIsNone(progress["question_set"])
        self.assertEqual(progress["sections"], [])

    # ── Drafting ─────────────────────────────────────────────────────────────

    def test_completeness_and_document_bands(self):
        Answer.objects.filter(assay=self.done).update(answer_documents=["a.pdf", "b.pdf"])
        _assay_for(self.user)  # undrafted: outside the base

        stats = build_stats("all")
        c = stats["completeness"]
        # Both use the 2-question questionnaire. done has an answer row for only
        # one question, and the missing one counts as unanswered: 1 of 2. partial
        # is also 1 of 2, because its not-found answer does not count.
        self.assertEqual(c["assays"], 2)
        self.assertEqual(c["answered_mean"], 1.0)
        self.assertEqual(c["questions_mean"], 2.0)
        self.assertEqual(c["share"], 50.0)

        self.assertEqual(
            [b["label"] for b in c["bands"]],
            [label for label, _, _ in config.stats_document_bands],
        )
        bands = {b["label"]: (b["assays"], b["completeness"]) for b in c["bands"]}
        self.assertEqual(bands["0"], (1, 50.0))
        self.assertEqual(bands["2–3"], (1, 50.0))
        self.assertEqual(bands["1"], (0, None))

        grounding = stats["grounding"]
        # A drafted ToxTemp with no documents counts as 0: median of [0, 2].
        self.assertEqual(grounding["assays"], 2)
        self.assertEqual(grounding["assays_with_documents"], 1)
        self.assertEqual(grounding["median_documents"], 1.0)

    def test_accepted_not_found_answer_is_not_complete(self):
        Answer.objects.filter(assay=self.done).update(answer_text=config.not_found_string)
        c = build_stats("all")["completeness"]
        self.assertEqual(c["share"], 25.0)  # done 0 of 2, partial 1 of 2

    def test_completeness_without_a_questionnaire_uses_answer_rows(self):
        loose = _assay_for(self.user)
        AnswerFactory.create(assay=loose, question=self.question)
        AnswerFactory.create(assay=loose, question=self.question2, answer_text="")
        c = build_stats("all")["completeness"]
        # done 1/2, partial 1/2, loose 1/2 (no questionnaire: its 2 answer rows).
        self.assertEqual(c["questions_mean"], 2.0)
        self.assertEqual(c["share"], 50.0)

    def test_time_from_first_draft_to_export(self):
        base = timezone.now() - dt.timedelta(days=10)
        Assay.objects.filter(pk__in=[self.done.pk, self.partial.pk]).update(
            submission_date=base
        )
        # done: first draft finished at +1h (a later model run must not move it),
        # exported at +3h -> 2h.
        first = AssayCost.objects.create(assay=self.done, model_key="1:A")
        later = AssayCost.objects.create(assay=self.done, model_key="1:B")
        _set_time(AssayCost, first.pk, "created_at", base + dt.timedelta(hours=1))
        _set_time(AssayCost, later.pk, "created_at", base + dt.timedelta(hours=2))
        f1 = Feedback.objects.create(user=self.user, assay=self.done, feedback_text="x")
        _set_time(Feedback, f1.pk, "submission_date", base + dt.timedelta(hours=3))
        # partial: no cost row, so the clock starts at creation -> 4h.
        f2 = Feedback.objects.create(
            user=self.other, assay=self.partial, feedback_text="x"
        )
        _set_time(Feedback, f2.pk, "submission_date", base + dt.timedelta(hours=4))
        # Re-drafted after its export: a cost row written after the rating cannot
        # be the first draft, so the clock starts at creation -> 6h.
        redrafted = _assay_for(self.user)
        _set_time(Assay, redrafted.pk, "submission_date", base)
        cost = AssayCost.objects.create(assay=redrafted, model_key="1:A")
        _set_time(AssayCost, cost.pk, "created_at", base + dt.timedelta(hours=7))
        f3 = Feedback.objects.create(user=self.user, assay=redrafted, feedback_text="x")
        _set_time(Feedback, f3.pk, "submission_date", base + dt.timedelta(hours=6))
        # Rated before its recorded creation: a non-positive span is ignored.
        skewed = _assay_for(self.user)
        f4 = Feedback.objects.create(user=self.user, assay=skewed, feedback_text="x")
        _set_time(Feedback, f4.pk, "submission_date", base + dt.timedelta(hours=4))

        engagement = build_stats("all")["engagement"]
        self.assertEqual(engagement["export_samples"], 3)
        self.assertEqual(engagement["median_to_export_seconds"], 4 * 3600)
        self.assertEqual(engagement["median_to_export_display"], "4h 00m")

    def test_questionnaire_versions_link_only_to_existing_json(self):
        _assay_for(self.user)  # no questionnaire: left out
        with tempfile.TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp)):
            rows = build_stats("all")["question_sets"]
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["url"])

            (Path(tmp) / "ToxTemp_kpitest.json").write_text("{}")
            rows = build_stats("all")["question_sets"]
        self.assertEqual(
            rows[0]["url"], config.questionnaire_json_url_template.format(label="kpitest")
        )
        self.assertEqual(rows[0]["count"], 2)

    def test_questionnaire_versions_with_the_same_name_show_their_label(self):
        QuestionSet.objects.filter(pk=self.question_set.pk).update(display_name="ToxTemp")
        twin = QuestionSetFactory.create(label="kpitwin", display_name="ToxTemp")
        _assay_for(self.user, question_set=twin)
        labels = sorted(r["label"] for r in build_stats("all")["question_sets"])
        self.assertEqual(labels, ["ToxTemp (kpitest)", "ToxTemp (kpitwin)"])

    # ── Ratings and costs ────────────────────────────────────────────────────

    def test_llm_costs_are_summed_per_model(self):
        AssayCost.objects.create(
            assay=self.done,
            model_key="1:GPT4O",
            model_id="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost_input="0.100000",
            cost_output="0.200000",
            cost_unit="Eur",
        )
        llm = build_stats("all")["llm"]
        self.assertEqual(llm["input_tokens"], 1000)
        self.assertAlmostEqual(llm["cost_total"], 0.3, places=6)
        self.assertEqual(llm["by_model"][0]["model"], "gpt-4o")
        self.assertAlmostEqual(llm["by_model"][0]["cost_total"], 0.3, places=6)

    def test_feedback_histogram_bins_the_rating(self):
        Feedback.objects.create(
            user=self.user, assay=self.done, feedback_text="x", usefulness_rating=4.2
        )
        feedback = build_stats("all")["feedback"]
        self.assertEqual(feedback["count"], 1)
        self.assertEqual(feedback["mean"], 4.2)
        binned = {b["label"]: b["count"] for b in feedback["bins"]}
        self.assertEqual(binned["4.0–4.5"], 1)
        self.assertEqual(sum(b["count"] for b in feedback["bins"]), 1)

    def test_top_of_scale_rating_lands_in_last_bin(self):
        Feedback.objects.create(
            user=self.user, assay=self.done, feedback_text="x", usefulness_rating=5.0
        )
        bins = {b["label"]: b["count"] for b in build_stats("all")["feedback"]["bins"]}
        self.assertEqual(bins["4.5–5.0"], 1)

    def test_response_rate_is_over_toxtemps_created_in_the_window(self):
        old = _assay_for(self.user)
        _backdate(old, 60)
        for assay in (old, self.done):
            Feedback.objects.create(user=self.user, assay=assay, feedback_text="x")
        # Two ratings in the window, but only one of the two ToxTemps created in it.
        feedback = build_stats("30d")["feedback"]
        self.assertEqual(feedback["count"], 2)
        self.assertEqual(feedback["response_rate"], 50.0)

    def test_files_and_time_skip_uncounted_toxtemps_and_accounts(self):
        staff = AdminFactory.create(email="staff.files@example.org")
        FileAssetFactory.create(uploaded_by=self.user, size_bytes=1_048_576)
        FileAssetFactory.create(uploaded_by=staff, size_bytes=1_048_576)
        # Uploaded by a counted user, but into a ToxTemp that does not count.
        in_staff_investigation = _assay_for(staff, created_by=self.user)
        linked = FileAssetFactory.create(uploaded_by=self.user, size_bytes=1_048_576)
        answer = AnswerFactory.create(
            assay=in_staff_investigation, question=self.question
        )
        AnswerFile.objects.create(answer=answer, file=linked)

        AssayTimeLog.objects.create(user=self.user, assay=self.done, seconds=60)
        AssayTimeLog.objects.create(user=staff, assay=self.done, seconds=600)

        stats = build_stats("all")
        self.assertEqual(stats["files"]["count"], 1)
        self.assertEqual(stats["files"]["megabytes"], 1.0)
        self.assertEqual(stats["engagement"]["active_seconds"], 60)

    # ── Collaboration ────────────────────────────────────────────────────────

    def test_shared_workspaces_need_a_second_member_and_a_real_toxtemp(self):
        consortium = WorkspaceFactory.create(owner=self.user, name="Liver MPS consortium")
        WorkspaceMemberFactory.create(workspace=consortium, user=self.other)
        WorkspaceInvestigationFactory.create(
            workspace=consortium, investigation=self.done.study.investigation
        )
        AssayFactory.create(study=self.done.study)  # a second ToxTemp in it

        alpha = WorkspaceFactory.create(owner=self.other, name="Alpha group")
        WorkspaceMemberFactory.create(workspace=alpha, user=self.user)
        WorkspaceInvestigationFactory.create(
            workspace=alpha, investigation=self.partial.study.investigation
        )

        # Solo: Workspace.save() adds the owner, so one member means nobody joined.
        solo = WorkspaceFactory.create(owner=self.user, name="Solo")
        WorkspaceInvestigationFactory.create(
            workspace=solo, investigation=self.partial.study.investigation
        )
        # Invited, but nothing shared into it.
        idle = WorkspaceFactory.create(owner=self.user, name="Idle")
        WorkspaceMemberFactory.create(workspace=idle, user=self.other)
        # Only demo content shared into it.
        demo = WorkspaceFactory.create(owner=self.user, name="Demo only")
        WorkspaceMemberFactory.create(workspace=demo, user=self.other)
        WorkspaceInvestigationFactory.create(
            workspace=demo,
            investigation=_assay_for(self.user, demo_template=True).study.investigation,
        )
        # The only other member is a staff account: not a collaboration.
        support = WorkspaceFactory.create(owner=self.user, name="Support")
        WorkspaceMemberFactory.create(
            workspace=support, user=AdminFactory.create(email="staff.ws@example.org")
        )
        WorkspaceInvestigationFactory.create(
            workspace=support, investigation=self.partial.study.investigation
        )

        collab = build_stats("all")["collaboration"]
        self.assertEqual(collab["workspaces_created"], 6)
        self.assertEqual(collab["shared_workspaces"], 2)
        self.assertEqual(collab["members"], 4)
        self.assertEqual(collab["shared_investigations"], 2)
        self.assertEqual(
            collab["workspaces"],
            [
                {"name": "Liver MPS consortium", "assays": 2},
                {"name": "Alpha group", "assays": 1},
            ],
        )
        self.assertNotIn("collaborating_users", collab)
        self.assertNotIn("cross_institution_workspaces", collab)

    # ── Payload shape ────────────────────────────────────────────────────────

    def test_removed_figures_are_gone_from_payload_and_csv(self):
        AssayCost.objects.create(
            assay=self.done, model_key="1:GPT4O", model_id="gpt-4o",
            cost_input="0.100000", cost_output="0.200000",
        )
        stats = build_stats("all")
        payload = to_json_payload(stats)
        json.dumps(payload)

        for key in ("funnel", "content", "completion"):
            self.assertNotIn(key, payload)
        self.assertNotIn("acceptance_rate", payload["headline"])
        self.assertNotIn("llm_cost", payload["headline"])
        for key in (
            "active_users", "assay_views", "file_downloads", "orcid_linked",
            "tos_accepted", "users_total", "median_elapsed_seconds",
        ):
            self.assertNotIn(key, payload["engagement"])
        model = payload["llm"]["by_model"][0]
        self.assertNotIn("cost_input", model)
        self.assertNotIn("cost_output", model)
        self.assertIn("cost_total", model)
        # Kept for the export.
        self.assertIn("active_hours", payload["engagement"])
        self.assertIn("median_completion_seconds", payload["engagement"])
        self.assertIn("edited", payload["answers"])
        self.assertIn("assay_status", payload)

        rows = to_csv_rows(stats)
        metrics = [(r[0], r[1]) for r in rows if len(r) == 3]
        self.assertFalse({s for s, _ in metrics} & {"funnel", "content"})
        names = {m for _, m in metrics}
        for gone in ("acceptance_rate", "llm_cost.total", "active_users.30d"):
            self.assertNotIn(gone, names)
        self.assertNotIn("gpt-4o.cost_input", names)
        self.assertIn("gpt-4o.cost_total", names)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StatsCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _person(organization="RIVM")
        _assay_for(self.user)

    def test_second_read_comes_from_cache(self):
        first = cached_stats("all")
        _assay_for(self.user)  # would change the answer if it recomputed
        second = cached_stats("all")
        self.assertEqual(second["headline"]["assays"]["period"], 1)
        self.assertEqual(second["generated_at"], first["generated_at"])

    def test_refresh_recomputes_and_replaces_the_cached_copy(self):
        cached_stats("all")
        _assay_for(self.user)
        refreshed = cached_stats("all", refresh=True)
        self.assertEqual(refreshed["headline"]["assays"]["period"], 2)
        # The refreshed payload is now what a plain read returns.
        self.assertEqual(cached_stats("all")["headline"]["assays"]["period"], 2)

    def test_ranges_are_cached_independently(self):
        cached_stats("all")
        with self.assertNumQueries(0):
            cached_stats("all")

        # A different window has its own key, so it still has to build once.
        # CaptureQueriesContext rather than connection.queries_log, which only
        # fills when DEBUG is on and is empty under the test settings.
        with CaptureQueriesContext(connection) as queries:
            cached_stats("12m")
        self.assertGreater(len(queries), 0)

        with self.assertNumQueries(0):
            cached_stats("12m")

    def test_clear_drops_every_range(self):
        cached_stats("all")
        cached_stats("12m")
        _assay_for(self.user)
        clear_stats_cache()
        self.assertEqual(cached_stats("all")["headline"]["assays"]["period"], 2)
        self.assertEqual(cached_stats("12m")["headline"]["assays"]["period"], 2)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StatsViewTests(TestCase):
    def setUp(self):
        # The views read through the cache, which outlives a single test method,
        # so without this each test would be asserting against whatever payload
        # the previous one happened to build.
        cache.clear()
        self.admin = AdminFactory.create()
        # Deliberately unusual identifiers so the privacy assertion below cannot
        # pass (or fail) by coincidence against ordinary page copy.
        self.user = PersonFactory.create(
            organization="Utrecht University",
            first_name="Zyqwra",
            last_name="Pfhlundt",
            email="zyqwra.pfhlundt@example.org",
        )
        _assay_for(self.user)

    def test_anonymous_is_redirected(self):
        for name in ("stats_dashboard", "stats_data", "stats_export_csv"):
            with self.subTest(view=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 302)
                self.assertIn("/login/", resp["Location"])

    def test_non_staff_is_redirected(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("stats_dashboard"))
        self.assertEqual(resp.status_code, 302)

    def test_offcanvas_link_is_staff_only(self):
        """The link is cosmetic — the view decorator is the real gate — but a
        regression here would advertise the page to every signed-in user."""
        url = reverse("stats_dashboard")

        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("overview")), f'href="{url}"')

        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("overview")), f'href="{url}"')

    def test_staff_sees_dashboard(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("stats_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "KPI dashboard")
        self.assertContains(resp, "Utrecht University")
        for label in (
            "not started", "time from first draft to export", "answer not found"
        ):
            self.assertContains(resp, label)
        self.assertNotContains(resp, "none accepted yet")
        self.assertNotContains(resp, "people in a shared workspace")
        # A {# #} comment cannot span lines; a multi-line one renders as page text.
        self.assertNotContains(resp, "{#")

    def test_dashboard_never_leaks_personal_identifiers(self):
        self.client.force_login(self.admin)
        body = self.client.get(reverse("stats_dashboard")).content.decode()
        # The account e-mail legitimately appears in the shared header/offcanvas,
        # so assert on the *other* user's identifiers, which must not be present.
        self.assertNotIn(self.user.email, body)
        self.assertNotIn(self.user.last_name, body)

    def test_refresh_clears_the_cache_and_redirects(self):
        self.client.force_login(self.admin)
        self.client.get(reverse("stats_dashboard"), {"range": "all"})
        _assay_for(self.user)

        # Without a refresh the page still shows the cached figure.
        body = self.client.get(reverse("stats_dashboard"), {"range": "all"}).content
        self.assertIn(b">1</div>", body)
        self.assertNotIn(b">2</div>", body)

        resp = self.client.get(
            reverse("stats_dashboard"), {"range": "all", "refresh": "1"}
        )
        self.assertEqual(resp.status_code, 302)
        # Redirects back without the refresh flag, so a reload is not a re-clear.
        self.assertNotIn("refresh", resp["Location"])
        self.assertIn("range=all", resp["Location"])
        self.assertIn(b">2</div>", self.client.get(resp["Location"]).content)

    def test_json_endpoint_returns_aggregates(self):
        self.client.force_login(self.admin)
        payload = self.client.get(reverse("stats_data"), {"range": "all"}).json()
        self.assertEqual(payload["range"]["key"], "all")
        self.assertEqual(payload["headline"]["assays"]["total"], 1)
        self.assertIn("organisations", payload)

    def test_csv_export_is_an_attachment(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("stats_export_csv"), {"range": "all"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment;", resp["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(resp.content.decode())))
        self.assertEqual(rows[0], ["section", "metric", "value"])
        self.assertTrue(any(r and r[0] == "Utrecht University" for r in rows))

    def test_json_payload_is_serialisable(self):
        payload = to_json_payload(build_stats("all"))
        self.assertIsInstance(payload["generated_at"], str)
        self.assertIsInstance(payload["range"], dict)

    def test_csv_rows_have_uniform_header(self):
        rows = to_csv_rows(build_stats("all"))
        self.assertEqual(rows[0], ["section", "metric", "value"])
