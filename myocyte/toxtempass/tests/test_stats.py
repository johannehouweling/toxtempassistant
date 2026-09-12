"""Tests for the staff-only KPI dashboard (/stats) and its aggregation layer."""

import csv
import datetime as dt
import io

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from toxtempass import config
from toxtempass.models import Assay, AssayCost, Feedback, LLMStatus, Person
from toxtempass.stats import (
    build_stats,
    cached_stats,
    clear_stats_cache,
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
    WorkspaceFactory,
    WorkspaceMemberFactory,
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
        # Elapsed spans calendar time, so days must not collapse into hours.
        self.assertEqual(humanize_seconds(86400 * 9 + 3600 * 4), "9d 4h")
        self.assertEqual(humanize_seconds(86400 * 2), "2d")


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

    def test_cumulative_series_never_decreases(self):
        old = _assay_for(self.user)
        Assay.objects.filter(pk=old.pk).update(
            submission_date=timezone.now() - dt.timedelta(days=60)
        )
        growth = build_stats("all")["growth"]
        running = growth["assays_cumulative"]
        self.assertEqual(running, sorted(running))
        self.assertEqual(running[-1], sum(growth["assays"]))
        self.assertEqual(len(running), len(growth["labels"]))

    def test_windowed_cumulative_starts_from_prior_total(self):
        """A windowed curve must not restart at zero at the window edge."""
        old = _assay_for(self.user)
        Assay.objects.filter(pk=old.pk).update(
            submission_date=timezone.now() - dt.timedelta(days=400)
        )
        growth = build_stats("12m")["growth"]
        # The pre-window assay is excluded from the per-bucket counts but must
        # still be carried in the running total.
        self.assertGreater(growth["assays_cumulative"][0], growth["assays"][0])
        self.assertEqual(growth["assays_cumulative"][-1], real_assays().count())

    def test_completion_marks_are_sorted_and_bucketed(self):
        marks = build_stats("all")["completion"]
        self.assertEqual(marks["total"], 2)
        # Most complete first, so the strip reads as a distribution.
        self.assertEqual(marks["marks"], sorted(marks["marks"], reverse=True))
        legend = {entry["label"]: entry["count"] for entry in marks["legend"]}
        self.assertEqual(legend["complete"], 1)          # self.done
        self.assertEqual(legend["over half accepted"], 1)  # self.partial, 1 of 2
        self.assertEqual(sum(legend.values()), marks["total"])

    def test_completion_total_matches_the_headline_period_count(self):
        """The strip and the figure beside it must never disagree."""
        for key in ("all", "12m", "30d"):
            with self.subTest(range=key):
                stats = build_stats(key)
                self.assertEqual(
                    stats["completion"]["total"], stats["headline"]["assays"]["period"]
                )

    def test_average_progress_is_a_mean_of_per_assay_shares(self):
        """One fully accepted and one half accepted averages to 75%, not 66%.

        Pooling would give 2 accepted of 3 questions = 66.7%; averaging each
        ToxTemp's own share gives (100 + 50) / 2 = 75%.
        """
        progress = build_stats("all")["progress"]
        self.assertEqual(progress["assays"], 2)
        self.assertEqual(progress["accepted"], 75.0)
        self.assertEqual(progress["drafted"], 100.0)
        self.assertEqual(progress["awaiting"], 25.0)
        self.assertEqual(progress["undrafted"], 0.0)

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

    def test_progress_bands_always_sum_to_a_hundred(self):
        for key in ("all", "12m"):
            with self.subTest(range=key):
                p = build_stats(key)["progress"]
                self.assertAlmostEqual(
                    p["accepted"] + p["awaiting"] + p["undrafted"], 100.0, places=1
                )

    def test_unseeded_assay_counts_as_zero_progress(self):
        """A ToxTemp with no questions was still created, so it drags the mean."""
        _assay_for(self.user)  # no answers at all
        progress = build_stats("all")["progress"]
        self.assertEqual(progress["assays"], 3)
        self.assertEqual(progress["assays_without_questions"], 1)
        self.assertEqual(progress["accepted"], 50.0)  # (100 + 50 + 0) / 3

    def test_section_progress_is_scoped_to_the_busiest_question_set(self):
        section = self.question.subsection.section
        progress = build_stats("all")["section_progress"]
        self.assertEqual(progress["assays"], 2)
        titles = [entry["title"] for entry in progress["sections"]]
        self.assertEqual(titles, [section.title])
        band = progress["sections"][0]
        self.assertAlmostEqual(
            band["accepted"] + band["awaiting"] + band["undrafted"], 100.0, places=1
        )
        self.assertEqual(band["questions"], 2)

    def test_solo_workspace_is_not_counted_as_shared(self):
        """Workspace.save() adds the owner, so one member means nobody joined."""
        WorkspaceFactory.create(owner=self.user)
        collab = build_stats("all")["collaboration"]
        self.assertEqual(collab["workspaces_created"], 1)
        self.assertEqual(collab["shared_workspaces"], 0)
        self.assertEqual(collab["collaborating_users"], 0)

    def test_workspace_with_a_second_member_is_shared(self):
        workspace = WorkspaceFactory.create(owner=self.user)
        WorkspaceMemberFactory.create(workspace=workspace, user=self.other)
        collab = build_stats("all")["collaboration"]
        self.assertEqual(collab["shared_workspaces"], 1)
        self.assertEqual(collab["collaborating_users"], 2)

    def test_cross_institution_needs_two_named_employers(self):
        # self.user is Utrecht, self.other is RIVM.
        crossing = WorkspaceFactory.create(owner=self.user)
        WorkspaceMemberFactory.create(workspace=crossing, user=self.other)

        # Same institution on both sides is not a crossing.
        colleague = PersonFactory.create(organization="Utrecht University")
        internal = WorkspaceFactory.create(owner=self.user)
        WorkspaceMemberFactory.create(workspace=internal, user=colleague)

        # A blank organisation must not manufacture one either.
        unknown = PersonFactory.create(organization="")
        blank = WorkspaceFactory.create(owner=self.user)
        WorkspaceMemberFactory.create(workspace=blank, user=unknown)

        collab = build_stats("all")["collaboration"]
        self.assertEqual(collab["shared_workspaces"], 3)
        self.assertEqual(collab["cross_institution_workspaces"], 1)

    def test_section_progress_is_empty_without_a_question_set(self):
        Assay.objects.update(question_set=None)
        progress = build_stats("all")["section_progress"]
        self.assertIsNone(progress["question_set"])
        self.assertEqual(progress["sections"], [])

    def test_completion_marks_are_capped(self):
        marks = build_stats("all")["completion"]
        self.assertLessEqual(len(marks["marks"]), config.stats_unit_marks_max)
        self.assertFalse(marks["truncated"])

    def test_organisation_rows_carry_an_inline_scale(self):
        rows = {r["organisation"]: r for r in build_stats("all")["organisations"]}
        # The busiest institution anchors the scale at 100%.
        self.assertEqual(max(r["share"] for r in rows.values()), 100.0)
        self.assertEqual(rows["KU Leuven"]["share"] if "KU Leuven" in rows else 0.0, 0.0)

    def test_range_filter_excludes_older_rows(self):
        old = _assay_for(self.user)
        Assay.objects.filter(pk=old.pk).update(
            submission_date=timezone.now() - dt.timedelta(days=400)
        )
        self.assertEqual(build_stats("all")["headline"]["assays"]["total"], 3)
        self.assertEqual(build_stats("30d")["headline"]["assays"]["period"], 2)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
class StatsCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = PersonFactory.create(organization="RIVM")
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
