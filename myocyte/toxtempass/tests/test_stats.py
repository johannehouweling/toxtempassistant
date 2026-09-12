"""Tests for the staff-only KPI dashboard (/stats) and its aggregation layer."""

import csv
import datetime as dt
import io

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toxtempass import config
from toxtempass.models import Assay, AssayCost, Feedback, LLMStatus, Person
from toxtempass.stats import (
    build_stats,
    humanize_seconds,
    real_assays,
    resolve_range,
    to_csv_rows,
    to_json_payload,
)
from toxtempass.tests.fixtures.factories import (
    AdminFactory,
    AnswerFactory,
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    QuestionFactory,
    StudyFactory,
)


def _assay_for(owner: Person, **kwargs) -> Assay:
    """Create a real (non-demo) assay owned by ``owner``."""
    investigation = InvestigationFactory.create(owner=owner)
    study = StudyFactory.create(investigation=investigation)
    return AssayFactory.create(study=study, **kwargs)


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


class StatsAggregationTests(TestCase):
    def setUp(self):
        self.user = PersonFactory.create(organization="Utrecht University")
        self.other = PersonFactory.create(organization="RIVM")
        # Pin the QuestionSet label: QuestionSetFactory's "v{n}" sequence keeps
        # counting across tests and eventually hits the "v1" set the migrations
        # already seeded. Both questions share a subsection so only one set exists.
        self.question = QuestionFactory.create(
            subsection__section__question_set__label="kpitest"
        )
        self.question2 = QuestionFactory.create(subsection=self.question.subsection)

        # One fully accepted assay for self.user
        self.done = _assay_for(self.user, status=LLMStatus.DONE)
        AnswerFactory.create(assay=self.done, question=self.question, accepted=True)

        # One partially accepted assay for self.other
        self.partial = _assay_for(self.other, status=LLMStatus.DONE)
        AnswerFactory.create(assay=self.partial, question=self.question, accepted=True)
        AnswerFactory.create(
            assay=self.partial,
            question=self.question2,
            accepted=False,
            answer_text=config.not_found_string,
        )

    def test_demo_assays_are_excluded(self):
        template = _assay_for(self.user, demo_template=True)
        copy = _assay_for(self.user, demo_lock=True, demo_source=template)
        pks = set(real_assays().values_list("pk", flat=True))
        self.assertNotIn(template.pk, pks)
        self.assertNotIn(copy.pk, pks)
        self.assertIn(self.done.pk, pks)

    def test_headline_counts_real_assays_only(self):
        _assay_for(self.user, demo_template=True)
        stats = build_stats("all")
        self.assertEqual(stats["headline"]["assays"]["total"], 2)
        self.assertEqual(stats["headline"]["completed_assays"]["total"], 1)

    def test_funnel_stages_are_monotonically_non_increasing(self):
        counts = [stage["count"] for stage in build_stats("all")["funnel"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts[0], 2)
        self.assertEqual(counts[-1], 1)

    def test_answer_quality_rates(self):
        answers = build_stats("all")["answers"]
        self.assertEqual(answers["total"], 3)
        self.assertEqual(answers["accepted"], 2)
        self.assertEqual(answers["not_found"], 1)

    def test_organisation_rows_are_institutions_not_people(self):
        rows = {row["organisation"]: row for row in build_stats("all")["organisations"]}
        self.assertIn("Utrecht University", rows)
        self.assertIn("RIVM", rows)
        self.assertEqual(rows["Utrecht University"]["assays"], 1)
        self.assertEqual(rows["Utrecht University"]["completed"], 1)
        self.assertEqual(rows["RIVM"]["completed"], 0)

    def test_guardian_anonymous_user_is_not_counted(self):
        from django.conf import settings

        sentinel = getattr(settings, "ANONYMOUS_USER_NAME", "AnonymousUser")
        Person.objects.get_or_create(**{Person.USERNAME_FIELD: sentinel})
        real = Person.objects.exclude(**{Person.USERNAME_FIELD: sentinel}).count()
        self.assertEqual(build_stats("all")["headline"]["users"]["total"], real)

    def test_blank_organisation_is_pooled(self):
        PersonFactory.create(organization="")
        labels = [row["organisation"] for row in build_stats("all")["organisations"]]
        self.assertIn(config.stats_unknown_organisation, labels)
        self.assertNotIn("", labels)

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

    def test_growth_series_fills_empty_buckets(self):
        """An empty month must render as a zero, not compress the time axis."""
        old = _assay_for(self.user)
        Assay.objects.filter(pk=old.pk).update(
            submission_date=timezone.now() - dt.timedelta(days=90)
        )
        growth = build_stats("12m")["growth"]
        self.assertEqual(len(growth["labels"]), len(growth["assays"]))
        self.assertEqual(len(growth["labels"]), len(growth["users"]))
        # ~3 months apart, so at least one intermediate bucket must be present.
        self.assertGreaterEqual(len(growth["labels"]), 3)
        self.assertIn(0, growth["assays"])

    def test_range_filter_excludes_older_rows(self):
        old = _assay_for(self.user)
        Assay.objects.filter(pk=old.pk).update(
            submission_date=timezone.now() - dt.timedelta(days=400)
        )
        self.assertEqual(build_stats("all")["headline"]["assays"]["total"], 3)
        self.assertEqual(build_stats("30d")["headline"]["assays"]["period"], 2)


class StatsViewTests(TestCase):
    def setUp(self):
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

    def test_staff_sees_dashboard(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("stats_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "KPI dashboard")
        self.assertContains(resp, "Utrecht University")

    def test_dashboard_never_leaks_personal_identifiers(self):
        self.client.force_login(self.admin)
        body = self.client.get(reverse("stats_dashboard")).content.decode()
        # The account e-mail legitimately appears in the shared header/offcanvas,
        # so assert on the *other* user's identifiers, which must not be present.
        self.assertNotIn(self.user.email, body)
        self.assertNotIn(self.user.last_name, body)

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
