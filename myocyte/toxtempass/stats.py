"""Aggregation layer for the staff-only KPI dashboard at ``/stats``.

Everything in here returns **aggregates only**. No row of the returned payload
may identify a natural person: no names, e-mail addresses, ORCID iDs, assay or
investigation titles, IP addresses, or free-text feedback ever leave this
module. The single identifying dimension we do expose is
``Person.organization`` — institutions, not people (see ``organisation_rows``).

The module is deliberately free of HTTP concerns so the same payload can be
rendered as HTML, serialised to JSON, or flattened to CSV by
:mod:`toxtempass.views`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db.models import (
    Avg,
    Count,
    F,
    Q,
    QuerySet,
    Sum,
)
from django.db.models.functions import TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from toxtempass import config
from toxtempass.models import (
    Answer,
    Assay,
    AssayCost,
    AssayTimeLog,
    AssayView,
    Feedback,
    FileAsset,
    FileDownloadLog,
    Investigation,
    LLMStatus,
    Person,
    Section,
    Study,
    Workspace,
    WorkspaceInvestigation,
    WorkspaceMember,
)

# ── Range selection ──────────────────────────────────────────────────────────

_TRUNC = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth}
_BUCKET_FMT = {"day": "%d %b", "week": "%d %b", "month": "%b %Y"}


@dataclass(frozen=True)
class StatsRange:
    """A resolved time window plus the bucket width its time-series uses."""

    key: str
    label: str
    since: dt.datetime | None
    bucket: str

    @property
    def is_all_time(self) -> bool:
        """True when no lower bound is applied."""
        return self.since is None

    def as_dict(self) -> dict[str, Any]:
        """Return a serialisable representation for the JSON endpoint."""
        return {
            "key": self.key,
            "label": self.label,
            "since": self.since.isoformat() if self.since else None,
            "bucket": self.bucket,
        }


def resolve_range(key: str | None, now: dt.datetime | None = None) -> StatsRange:
    """Resolve a query-param range key into a :class:`StatsRange`.

    Unknown or missing keys fall back to ``Config.stats_default_range``.
    """
    now = now or timezone.now()
    spec = config.stats_ranges.get(key or "")
    if spec is None:
        key = config.stats_default_range
        spec = config.stats_ranges[key]
    label, days, bucket = spec
    since = now - dt.timedelta(days=days) if days else None
    return StatsRange(key=key, label=label, since=since, bucket=bucket)


# ── Base querysets ───────────────────────────────────────────────────────────

# Demo content is seeded automatically for every new account, so counting it
# would inflate every usage KPI. Mirrors the filter used by AssayAdmin.
REAL_ASSAY_Q = Q(demo_template=False, demo_lock=False, demo_source__isnull=True)


def real_assays() -> QuerySet[Assay]:
    """All assays created by users, excluding the seeded demo template/copies."""
    return Assay.objects.filter(REAL_ASSAY_Q)


def people() -> QuerySet[Person]:
    """All real accounts, excluding django-guardian's AnonymousUser sentinel.

    Guardian materialises a Person row for anonymous object-permission lookups
    (``ANONYMOUS_USER_NAME``, default ``"AnonymousUser"``). It is not a user of
    the app, so counting it would offset every per-account KPI by one.
    """
    sentinel = getattr(settings, "ANONYMOUS_USER_NAME", "AnonymousUser")
    qs = Person.objects.all()
    if sentinel:
        qs = qs.exclude(**{Person.USERNAME_FIELD: sentinel})
    return qs


def _scoped(qs: QuerySet, field: str, rng: StatsRange) -> QuerySet:
    """Apply the range lower bound to ``field``, or pass through for all-time."""
    if rng.since is None:
        return qs
    return qs.filter(**{f"{field}__gte": rng.since})


def _completed_assays(qs: QuerySet[Assay]) -> QuerySet[Assay]:
    """Assays where every seeded answer has been accepted by a user."""
    return qs.annotate(
        _n_answers=Count("answers", distinct=True),
        _n_accepted=Count("answers", filter=Q(answers__accepted=True), distinct=True),
    ).filter(_n_answers__gt=0, _n_answers=F("_n_accepted"))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _pct(part: int | float | None, whole: int | float | None) -> float | None:
    """Percentage of ``part`` in ``whole``, or ``None`` when undefined."""
    if not whole:
        return None
    return round(100.0 * (part or 0) / whole, 1)


def _f(value: Decimal | float | None) -> float | None:
    """Coerce Decimal/int/float to float, preserving ``None``.

    Rounded to the 6 decimals the cost DecimalFields store, so summing them does
    not surface binary-float noise like ``12.822785999999999`` in the payload.
    """
    if value is None:
        return None
    return round(float(value), 6)


def _median(values: list[int | float]) -> float | None:
    """Median of ``values`` (empty list yields ``None``)."""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def humanize_seconds(seconds: float | None) -> str:
    """Render a duration as ``4h 12m`` / ``38m`` / ``45s`` (``—`` when unknown)."""
    if seconds is None:
        return "—"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _timeseries(qs: QuerySet, field: str, rng: StatsRange) -> dict[dt.date, int]:
    """Bucket ``qs`` by ``field`` at the range's bucket width -> {date: count}."""
    trunc = _TRUNC[rng.bucket]
    rows = (
        _scoped(qs, field, rng)
        .annotate(_bucket=trunc(field))
        .values("_bucket")
        .annotate(n=Count("pk", distinct=True))
        .order_by("_bucket")
    )
    out: dict[dt.date, int] = {}
    for row in rows:
        bucket = row["_bucket"]
        if bucket is None:
            continue
        if isinstance(bucket, dt.datetime):
            if timezone.is_aware(bucket):
                bucket = timezone.localtime(bucket)
            bucket = bucket.date()
        out[bucket] = row["n"]
    return out


# ── Section builders ─────────────────────────────────────────────────────────


def _cost_sum(qs: QuerySet[AssayCost]) -> Decimal:
    """Return combined input + output cost over ``qs`` (missing prices count as 0)."""
    agg = qs.aggregate(cost_in=Sum("cost_input"), cost_out=Sum("cost_output"))
    return (agg["cost_in"] or Decimal(0)) + (agg["cost_out"] or Decimal(0))


def headline(rng: StatsRange) -> dict[str, Any]:
    """All-time totals plus the in-period delta for the hero tile row."""
    assays = real_assays()
    persons = people()
    completed = _completed_assays(assays)

    answers = Answer.objects.filter(assay__in=assays.values("pk"))
    n_answers = answers.count()
    n_accepted = answers.filter(accepted=True).count()

    named = persons.exclude(organization="")
    n_orgs = named.values("organization").distinct().count()
    n_orgs_period = (
        _scoped(named, "date_joined", rng).values("organization").distinct().count()
    )

    return {
        "users": {
            "total": persons.count(),
            "period": _scoped(persons, "date_joined", rng).count(),
        },
        # "period" counts institutions whose first account arrived inside the
        # window, so it reads on the same basis as the users figure beside it.
        "organisations": {"total": n_orgs, "period": n_orgs_period},
        "assays": {
            "total": assays.count(),
            "period": _scoped(assays, "submission_date", rng).count(),
        },
        "completed_assays": {
            "total": completed.count(),
            "period": _scoped(completed, "submission_date", rng).count(),
            "pct": _pct(
                _scoped(completed, "submission_date", rng).count(),
                _scoped(assays, "submission_date", rng).count(),
            ),
        },
        "acceptance_rate": _pct(n_accepted, n_answers),
        "llm_cost": {
            "total": _f(_cost_sum(AssayCost.objects.all())),
            "period": _f(
                _cost_sum(_scoped(AssayCost.objects.all(), "created_at", rng))
            ),
            "currency": _currency_symbol(),
        },
    }


def _currency_symbol() -> str:
    """Most frequently recorded cost-unit symbol across all cost rows."""
    row = (
        AssayCost.objects.exclude(cost_unit="")
        .values("cost_unit")
        .annotate(n=Count("pk"))
        .order_by("-n")
        .first()
    )
    if not row:
        return "€"
    from toxtempass.azure_registry import cost_unit_symbol

    return cost_unit_symbol(row["cost_unit"])


def _fill_buckets(keys: list[dt.date], bucket: str) -> list[dt.date]:
    """Return ``keys`` with interior gaps filled, so the x-axis stays even.

    A month with no signups must render as a zero, not vanish — dropping it
    silently compresses the time axis and makes the trend read wrong.
    """
    if len(keys) < 2:
        return keys
    out: list[dt.date] = []
    current, last = keys[0], keys[-1]
    while current <= last and len(out) < 400:
        out.append(current)
        if bucket == "month":
            year, month = divmod(current.month, 12)
            current = current.replace(year=current.year + year, month=month + 1, day=1)
        else:
            current = current + dt.timedelta(days=7 if bucket == "week" else 1)
    return out


def _running_total(per_bucket: list[int], start: int) -> list[int]:
    """Accumulate ``per_bucket`` on top of ``start`` (the count before the window)."""
    total = start
    out = []
    for n in per_bucket:
        total += n
        out.append(total)
    return out


def growth(rng: StatsRange) -> dict[str, Any]:
    """Reach over time: new accounts and ToxTemps per bucket, and running totals.

    The dashboard plots the running totals — for a low-traffic instrument the
    per-bucket counts are too spiky to read, and the question stakeholders ask
    is how far the tool has got, not what happened last month. Both series are
    kept in the payload so the CSV/JSON export can answer either question.
    """
    all_people, all_assays = people(), real_assays()
    users = _timeseries(all_people, "date_joined", rng)
    assays = _timeseries(all_assays, "submission_date", rng)

    keys = _fill_buckets(sorted(set(users) | set(assays)), rng.bucket)
    fmt = _BUCKET_FMT[rng.bucket]

    # A windowed view still shows the true cumulative line, so the curve does
    # not restart from zero at the window edge.
    before_users = before_assays = 0
    if rng.since is not None:
        before_users = all_people.filter(date_joined__lt=rng.since).count()
        before_assays = all_assays.filter(submission_date__lt=rng.since).count()

    per_user = [users.get(k, 0) for k in keys]
    per_assay = [assays.get(k, 0) for k in keys]
    return {
        "labels": [k.strftime(fmt) for k in keys],
        "users": per_user,
        "assays": per_assay,
        "users_cumulative": _running_total(per_user, before_users),
        "assays_cumulative": _running_total(per_assay, before_assays),
        "bucket": rng.bucket,
    }


def completion_marks(rng: StatsRange) -> dict[str, Any]:
    """One mark per ToxTemp, bucketed by how much of the template is accepted.

    Drives the unit strip at the top of the dashboard: every ToxTemp created in
    the window gets its own mark, sorted most-complete first so the strip reads
    as a distribution rather than noise. Buckets are ordinal (0 = nothing
    accepted ... 3 = every answer accepted), which is what lets the strip use a
    single-hue ramp instead of arbitrary categorical colours.
    """
    rows = (
        _scoped(real_assays(), "submission_date", rng)
        .annotate(
            n_answers=Count("answers", distinct=True),
            n_accepted=Count(
                "answers", filter=Q(answers__accepted=True), distinct=True
            ),
        )
        .values_list("n_answers", "n_accepted")
    )

    shares = []
    for n_answers, n_accepted in rows:
        shares.append(n_accepted / n_answers if n_answers else 0.0)
    shares.sort(reverse=True)

    def _bucket(share: float) -> int:
        if share >= 1.0:
            return 3
        if share >= 0.5:
            return 2
        if share > 0.0:
            return 1
        return 0

    marks = [_bucket(s) for s in shares]
    limit = config.stats_unit_marks_max
    counts = [marks.count(i) for i in range(4)]
    return {
        "marks": marks[:limit],
        "total": len(marks),
        "truncated": len(marks) > limit,
        "legend": [
            {"key": 3, "label": "complete", "count": counts[3]},
            {"key": 2, "label": "over half accepted", "count": counts[2]},
            {"key": 1, "label": "under half accepted", "count": counts[1]},
            {"key": 0, "label": "not started", "count": counts[0]},
        ],
    }


def assay_status(rng: StatsRange) -> list[dict[str, Any]]:
    """Return the LLM status distribution over assays created in the window."""
    qs = _scoped(real_assays(), "submission_date", rng)
    counts = {
        row["status"]: row["n"]
        for row in qs.values("status").annotate(n=Count("pk"))
    }
    total = sum(counts.values())
    return [
        {
            "key": value,
            "label": label,
            "count": counts.get(value, 0),
            "pct": _pct(counts.get(value, 0), total),
        }
        for value, label in LLMStatus.choices
    ]


def funnel(rng: StatsRange) -> list[dict[str, Any]]:
    """Return the created -> drafted -> partly -> fully accepted stages."""
    qs = _scoped(real_assays(), "submission_date", rng)
    created = qs.count()

    drafted = qs.filter(answers__answer_text__gt="").distinct().count()
    partly = qs.filter(answers__accepted=True).distinct().count()
    done = _completed_assays(qs).count()

    stages = [
        ("Created", created),
        ("Draft answers generated", drafted),
        ("At least one answer accepted", partly),
        ("Fully accepted", done),
    ]
    return [
        {"label": label, "count": n, "pct": _pct(n, created)} for label, n in stages
    ]


def _progress_split(total: int, drafted: int, accepted: int) -> dict[str, float]:
    """Split one questionnaire into accepted / awaiting / undrafted percentages.

    The three always sum to 100 so a stacked bar can be rendered straight from
    them. ``accepted`` is a subset of ``drafted``, so the middle band is the
    answers that have a draft nobody has signed off yet.
    """
    if not total:
        return {"accepted": 0.0, "awaiting": 0.0, "undrafted": 100.0, "drafted": 0.0}
    accepted_pct = 100.0 * accepted / total
    drafted_pct = 100.0 * drafted / total
    return {
        "accepted": round(accepted_pct, 1),
        "awaiting": round(drafted_pct - accepted_pct, 1),
        "undrafted": round(100.0 - drafted_pct, 1),
        "drafted": round(drafted_pct, 1),
    }


def average_progress(rng: StatsRange) -> dict[str, Any]:
    """Mean progress per ToxTemp, split into drafted and expert-accepted.

    Each ToxTemp is scored on its own questionnaire and the scores are then
    averaged, so every ToxTemp counts the same regardless of how many questions
    its questionnaire version has. A ToxTemp with no questions seeded yet counts
    as 0% rather than being dropped — it was still created.
    """
    rows = (
        _scoped(real_assays(), "submission_date", rng)
        .annotate(
            n_total=Count("answers", distinct=True),
            n_drafted=Count(
                "answers", filter=Q(answers__answer_text__gt=""), distinct=True
            ),
            n_accepted=Count(
                "answers", filter=Q(answers__accepted=True), distinct=True
            ),
        )
        .values_list("n_total", "n_drafted", "n_accepted")
    )

    drafted_shares, accepted_shares, unseeded = [], [], 0
    for total, drafted, accepted in rows:
        if not total:
            unseeded += 1
            drafted_shares.append(0.0)
            accepted_shares.append(0.0)
            continue
        drafted_shares.append(100.0 * drafted / total)
        accepted_shares.append(100.0 * accepted / total)

    n = len(drafted_shares)
    drafted_pct = round(sum(drafted_shares) / n, 1) if n else 0.0
    accepted_pct = round(sum(accepted_shares) / n, 1) if n else 0.0
    return {
        "assays": n,
        "assays_without_questions": unseeded,
        "drafted": drafted_pct,
        "accepted": accepted_pct,
        "awaiting": round(drafted_pct - accepted_pct, 1),
        "undrafted": round(100.0 - drafted_pct, 1),
    }


def section_progress(rng: StatsRange) -> dict[str, Any]:
    """Mean per-ToxTemp progress for each section of the questionnaire.

    Scoped to whichever QuestionSet the most ToxTemps in the window use, since
    sections are not comparable across questionnaire versions — merging a v1
    section with a similarly titled v2 one would silently average two different
    question lists. Sections keep their seeded order (pk), which is the order
    they appear in the ToxTemp itself.
    """
    assays = _scoped(real_assays(), "submission_date", rng).exclude(question_set=None)
    busiest = (
        assays.values("question_set_id", "question_set__label",
                      "question_set__display_name")
        .annotate(n=Count("pk"))
        .order_by("-n")
        .first()
    )
    if not busiest:
        return {"question_set": None, "assays": 0, "sections": []}

    qset_id = busiest["question_set_id"]
    assay_ids = assays.filter(question_set_id=qset_id).values("pk")

    # One row per (ToxTemp, section): how many of that section's questions are
    # drafted and accepted in that ToxTemp.
    rows = (
        Answer.objects.filter(
            assay__in=assay_ids, question__subsection__section__question_set_id=qset_id
        )
        .values("assay_id", "question__subsection__section_id")
        .annotate(
            total=Count("pk"),
            drafted=Count("pk", filter=Q(answer_text__gt="")),
            accepted=Count("pk", filter=Q(accepted=True)),
        )
    )

    per_section: dict[int, list[tuple[int, int, int]]] = {}
    for row in rows:
        section_id = row["question__subsection__section_id"]
        per_section.setdefault(section_id, []).append(
            (row["total"], row["drafted"], row["accepted"])
        )

    titles = dict(
        Section.objects.filter(question_set_id=qset_id)
        .order_by("pk")
        .values_list("pk", "title")
    )

    sections = []
    for section_id, title in titles.items():
        entries = per_section.get(section_id, [])
        if not entries:
            sections.append(
                {"title": title, "questions": 0, "accepted": 0.0, "awaiting": 0.0,
                 "undrafted": 100.0, "drafted": 0.0}
            )
            continue
        splits = [_progress_split(*entry) for entry in entries]
        count = len(splits)
        sections.append(
            {
                "title": title,
                "questions": max(entry[0] for entry in entries),
                "accepted": round(sum(s["accepted"] for s in splits) / count, 1),
                "awaiting": round(sum(s["awaiting"] for s in splits) / count, 1),
                "undrafted": round(sum(s["undrafted"] for s in splits) / count, 1),
                "drafted": round(sum(s["drafted"] for s in splits) / count, 1),
            }
        )

    return {
        "question_set": busiest["question_set__display_name"]
        or busiest["question_set__label"],
        "assays": busiest["n"],
        "sections": sections,
    }


def answer_quality(rng: StatsRange) -> dict[str, Any]:
    """Return acceptance, coverage and human-edit rates for answers in the window."""
    assay_ids = _scoped(real_assays(), "submission_date", rng).values("pk")
    answers = Answer.objects.filter(assay__in=assay_ids)

    total = answers.count()
    accepted = answers.filter(accepted=True).count()
    empty = answers.filter(answer_text="").count()
    not_found = answers.filter(answer_text__icontains=config.not_found_string).count()

    # simple_history writes one row per save; >1 row means a human touched the
    # LLM draft at least once after it was first written.
    historical = Answer.history.model.objects.filter(assay__in=assay_ids)
    edited = (
        historical.values("id")
        .annotate(n=Count("history_id"))
        .filter(n__gt=1)
        .count()
    )

    answered = total - empty
    return {
        "total": total,
        "accepted": accepted,
        "accepted_pct": _pct(accepted, total),
        "empty": empty,
        "not_found": not_found,
        "not_found_pct": _pct(not_found, answered),
        "answered": answered,
        "answered_pct": _pct(answered, total),
        "edited": edited,
        "edited_pct": _pct(edited, total),
    }


def organisation_rows() -> list[dict[str, Any]]:
    """All-time per-institution usage.

    Institutions — never individuals — are the finest grain exposed anywhere in
    this dashboard. Users who left ``organization`` blank are pooled under a
    single "Not specified" row rather than listed separately.
    """
    def _by_org(qs: QuerySet, field: str) -> dict[str, int]:
        """Group ``qs`` by the organisation reachable at ``field`` -> {org: count}."""
        return {
            row[field] or "": row["n"]
            for row in qs.values(field).annotate(n=Count("pk"))
        }

    owner_org = "study__investigation__owner__organization"
    users = _by_org(people(), "organization")
    investigations = _by_org(Investigation.objects.all(), "owner__organization")
    assays = _by_org(real_assays(), owner_org)
    # _completed_assays() already carries Count annotations; grouping again on
    # top of them would fold those into the GROUP BY, so resolve to ids first.
    completed_ids = list(_completed_assays(real_assays()).values_list("pk", flat=True))
    completed = _by_org(Assay.objects.filter(pk__in=completed_ids), owner_org)

    rows = []
    for org in set(users) | set(investigations) | set(assays):
        rows.append(
            {
                "organisation": org or config.stats_unknown_organisation,
                "users": users.get(org, 0),
                "investigations": investigations.get(org, 0),
                "assays": assays.get(org, 0),
                "completed": completed.get(org, 0),
            }
        )
    rows.sort(key=lambda r: (-r["assays"], -r["users"], r["organisation"]))
    # Each row carries its own share of the busiest institution, so the table can
    # draw an inline scale instead of needing a chart beside it.
    busiest = max((r["assays"] for r in rows), default=0)
    for row in rows:
        row["share"] = _pct(row["assays"], busiest) or 0.0
    return rows


def llm_usage(rng: StatsRange) -> dict[str, Any]:
    """Token and cost totals for the window, broken down per model."""
    qs = _scoped(AssayCost.objects.all(), "created_at", rng)
    totals = qs.aggregate(
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        cost_input=Sum("cost_input"),
        cost_output=Sum("cost_output"),
        runs=Count("pk"),
        assays=Count("assay", distinct=True),
    )
    by_model = [
        {
            "model": row["model_id"] or row["model_key"],
            "runs": row["runs"],
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cost_input": _f(row["cost_input"]) or 0.0,
            "cost_output": _f(row["cost_output"]) or 0.0,
            "cost_total": round(
                (_f(row["cost_input"]) or 0.0) + (_f(row["cost_output"]) or 0.0), 6
            ),
        }
        for row in qs.values("model_id", "model_key")
        .annotate(
            runs=Count("pk"),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            cost_input=Sum("cost_input"),
            cost_output=Sum("cost_output"),
        )
        .order_by("-input_tokens")
    ]
    cost_total = round(
        (_f(totals["cost_input"]) or 0.0) + (_f(totals["cost_output"]) or 0.0), 6
    )
    n_assays = totals["assays"] or 0
    return {
        "runs": totals["runs"] or 0,
        "assays": n_assays,
        "input_tokens": totals["input_tokens"] or 0,
        "output_tokens": totals["output_tokens"] or 0,
        "cost_total": cost_total,
        "cost_per_assay": round(cost_total / n_assays, 4) if n_assays else None,
        "currency": _currency_symbol(),
        "by_model": by_model,
    }


def engagement(rng: StatsRange) -> dict[str, Any]:
    """Active-time, recency and view/download counters for the window."""
    assays = _scoped(real_assays(), "submission_date", rng)
    assay_ids = assays.values("pk")

    seconds = (
        AssayTimeLog.objects.filter(assay__in=assay_ids).aggregate(s=Sum("seconds"))["s"]
        or 0
    )
    completion_times = list(
        _completed_assays(assays)
        .exclude(completion_time_seconds=None)
        .values_list("completion_time_seconds", flat=True)
    )

    now = timezone.now()
    active = {
        window: people().filter(
            last_login__gte=now - dt.timedelta(days=window)
        ).count()
        for window in config.stats_active_user_windows
    }

    return {
        "active_seconds": seconds,
        "active_hours": round(seconds / 3600.0, 1),
        "median_completion_seconds": _median(completion_times),
        "median_completion_display": humanize_seconds(_median(completion_times)),
        "completion_samples": len(completion_times),
        "active_users": active,
        "assay_views": AssayView.objects.filter(assay__in=assay_ids).count(),
        "file_downloads": _scoped(
            FileDownloadLog.objects.all(), "downloaded_at", rng
        ).count(),
        "orcid_linked": people().exclude(orcid_id=None).count(),
        "tos_accepted": people().filter(has_accepted_tos=True).count(),
        "users_total": people().count(),
    }


def feedback_stats(rng: StatsRange) -> dict[str, Any]:
    """Rating count/mean and a half-point histogram. Free text is never read."""
    qs = _scoped(Feedback.objects.all(), "submission_date", rng)
    ratings = [
        r for r in qs.values_list("usefulness_rating", flat=True) if r is not None
    ]
    edges = config.stats_rating_bins
    top = edges[-1]

    def _in_bin(rating: float, lo: float, hi: float) -> bool:
        """Half-open bin — the top bin also takes the closing 5.0 endpoint."""
        return lo <= rating < hi or (hi == top and rating == top)

    bins = [
        {
            "label": f"{lo:.1f}–{hi:.1f}",
            "count": sum(1 for r in ratings if _in_bin(r, lo, hi)),
        }
        for lo, hi in zip(edges[:-1], edges[1:], strict=True)
    ]
    return {
        "count": qs.count(),
        "rated": len(ratings),
        "mean": round(qs.aggregate(a=Avg("usefulness_rating"))["a"], 2)
        if ratings
        else None,
        "bins": bins,
        "response_rate": _pct(
            qs.count(), _scoped(real_assays(), "submission_date", rng).count()
        ),
    }


def file_stats(rng: StatsRange) -> dict[str, Any]:
    """Upload volume, stored bytes and the MIME mix for the window."""
    available = FileAsset.objects.filter(status=FileAsset.Status.AVAILABLE)
    qs = _scoped(available, "created_at", rng)
    agg = qs.aggregate(n=Count("pk"), size=Sum("size_bytes"))
    by_type = [
        {"content_type": row["content_type"] or "unknown", "count": row["n"]}
        for row in qs.values("content_type")
        .annotate(n=Count("pk"))
        .order_by("-n")[: config.stats_top_n]
    ]
    return {
        "count": agg["n"] or 0,
        "bytes": agg["size"] or 0,
        "megabytes": round((agg["size"] or 0) / 1_048_576, 1),
        "by_type": by_type,
    }


def collaboration(rng: StatsRange) -> dict[str, Any]:
    """Workspace counts and sharing reach for the window."""
    qs = _scoped(Workspace.objects.all(), "created_at", rng)
    n = qs.count()
    members = WorkspaceMember.objects.filter(workspace__in=qs.values("pk")).count()
    shared = WorkspaceInvestigation.objects.filter(
        workspace__in=qs.values("pk")
    ).count()
    return {
        "workspaces": n,
        "members": members,
        "shared_investigations": shared,
        "avg_members": round(members / n, 1) if n else None,
        "collaborating_users": WorkspaceMember.objects.filter(
            workspace__in=qs.values("pk")
        )
        .values("user")
        .distinct()
        .count(),
    }


def question_set_mix(rng: StatsRange) -> list[dict[str, Any]]:
    """Which questionnaire version assays created in the window are using."""
    qs = _scoped(real_assays(), "submission_date", rng)
    total = qs.count()
    rows = (
        qs.values("question_set__label", "question_set__display_name")
        .annotate(n=Count("pk"))
        .order_by("-n")
    )
    return [
        {
            "label": row["question_set__display_name"]
            or row["question_set__label"]
            or "unassigned",
            "count": row["n"],
            "pct": _pct(row["n"], total),
        }
        for row in rows
    ]


def content_totals(rng: StatsRange) -> dict[str, Any]:
    """Raw object counts for the window, all-time counterparts alongside."""
    investigations = Investigation.objects.all()
    studies = Study.objects.all()
    return {
        "investigations": {
            "total": investigations.count(),
            "period": _scoped(investigations, "submission_date", rng).count(),
        },
        "studies": {
            "total": studies.count(),
            "period": _scoped(studies, "submission_date", rng).count(),
        },
        "assays": {
            "total": real_assays().count(),
            "period": _scoped(real_assays(), "submission_date", rng).count(),
        },
    }


# ── Entry point ──────────────────────────────────────────────────────────────


def build_stats(range_key: str | None = None) -> dict[str, Any]:
    """Assemble the full KPI payload for ``range_key``.

    The result contains aggregates only and is safe to render, serialise to
    JSON, or export as CSV without further redaction.
    """
    rng = resolve_range(range_key)
    return {
        "generated_at": timezone.now(),
        "range": rng,
        "ranges": [(key, spec[0]) for key, spec in config.stats_ranges.items()],
        "headline": headline(rng),
        "content": content_totals(rng),
        "growth": growth(rng),
        "completion": completion_marks(rng),
        "progress": average_progress(rng),
        "section_progress": section_progress(rng),
        "assay_status": assay_status(rng),
        "funnel": funnel(rng),
        "answers": answer_quality(rng),
        "organisations": organisation_rows(),
        "llm": llm_usage(rng),
        "engagement": engagement(rng),
        "feedback": feedback_stats(rng),
        "files": file_stats(rng),
        "collaboration": collaboration(rng),
        "question_sets": question_set_mix(rng),
    }


def to_json_payload(stats: dict[str, Any]) -> dict[str, Any]:
    """Return ``stats`` with non-JSON-native values coerced for serialisation."""
    payload = dict(stats)
    payload["generated_at"] = stats["generated_at"].isoformat()
    payload["range"] = stats["range"].as_dict()
    payload["ranges"] = [{"key": k, "label": v} for k, v in stats["ranges"]]
    return payload


def to_csv_rows(stats: dict[str, Any]) -> list[list[Any]]:
    """Flatten ``stats`` into ``[section, metric, value]`` rows for CSV export."""
    rows: list[list[Any]] = [["section", "metric", "value"]]

    def add(section: str, metric: str, value: object) -> None:
        """Append one flattened metric row."""
        rows.append([section, metric, "" if value is None else value])

    rng = stats["range"]
    add("meta", "generated_at", stats["generated_at"].isoformat())
    add("meta", "range", rng.label)
    add("meta", "since", rng.since.isoformat() if rng.since else "all time")

    for name, block in stats["headline"].items():
        if isinstance(block, dict):
            for sub, value in block.items():
                add("headline", f"{name}.{sub}", value)
        else:
            add("headline", name, block)

    for name, block in stats["content"].items():
        add("content", f"{name}.total", block["total"])
        add("content", f"{name}.period", block["period"])

    for stage in stats["funnel"]:
        add("funnel", stage["label"], stage["count"])
    for entry in stats["assay_status"]:
        add("assay_status", entry["label"], entry["count"])
    for metric, value in stats["answers"].items():
        add("answers", metric, value)
    for metric, value in stats["llm"].items():
        if metric != "by_model":
            add("llm", metric, value)
    for entry in stats["llm"]["by_model"]:
        for metric, value in entry.items():
            if metric != "model":
                add("llm_by_model", f"{entry['model']}.{metric}", value)
    for metric, value in stats["engagement"].items():
        if metric == "active_users":
            for window, count in value.items():
                add("engagement", f"active_users.{window}d", count)
        else:
            add("engagement", metric, value)
    for metric, value in stats["feedback"].items():
        if metric == "bins":
            for entry in value:
                add("feedback", f"rating {entry['label']}", entry["count"])
        else:
            add("feedback", metric, value)
    for metric, value in stats["files"].items():
        if metric == "by_type":
            for entry in value:
                add("files", f"type {entry['content_type']}", entry["count"])
        else:
            add("files", metric, value)
    for metric, value in stats["collaboration"].items():
        add("collaboration", metric, value)
    for entry in stats["question_sets"]:
        add("question_sets", entry["label"], entry["count"])

    rows.append([])
    rows.append(["organisations", "users", "investigations", "assays", "completed"])
    for entry in stats["organisations"]:
        rows.append(
            [
                entry["organisation"],
                entry["users"],
                entry["investigations"],
                entry["assays"],
                entry["completed"],
            ]
        )
    return rows
