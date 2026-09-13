"""Aggregation layer for the staff-only KPI dashboard at ``/stats``.

Everything in here returns **aggregates only**. No row of the returned payload
may identify a natural person: no names, e-mail addresses, ORCID iDs, assay or
investigation titles, IP addresses, or free-text feedback ever leave this
module. Two named dimensions are exposed, deliberately:

* Institutions — ``Person.organization``, grouped by its ROR match — which are
  organisations, not people (see ``organisation_rows``).
* Names of shared workspaces (see ``collaboration``). This is a reasoned
  exception: a workspace name is a label a team chose for a joint project, not
  an attribute of a person, and naming the collaborations is what makes the
  count credible to a stakeholder. It is limited to workspaces that are
  evidently collaborations in use — more than one member and at least one
  counted ToxTemp shared into them — so a private or abandoned workspace, whose
  name is likelier to be personal, never appears. Nothing else about a
  workspace (owner, members, contents) is exposed, and the page is staff-only.

Who counts is decided in one place, :func:`real_assays` and :func:`people`.
Every query below starts from one of them, so demo content and synthetic
evaluation accounts never reach a figure. Staff accounts are left out of the
per-account figures (users, active time, uploads), but their ToxTemps count.

The module is deliberately free of HTTP concerns so the same payload can be
rendered as HTML, serialised to JSON, or flattened to CSV by
:mod:`toxtempass.views`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Avg,
    Count,
    F,
    Min,
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
    Feedback,
    FileAsset,
    Investigation,
    LLMStatus,
    Person,
    Question,
    QuestionSet,
    Section,
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


def _exclude_non_users(qs: QuerySet, prefix: str = "", *, staff: bool = True) -> QuerySet:
    """Drop rows whose person at ``prefix`` has an excluded e-mail domain or is staff.

    ``staff=False`` keeps staff accounts' rows and drops only the excluded
    domains. Chained ``exclude()`` calls rather than one OR'd ``Q``: across a
    nullable foreign key Django keeps the rows where the relation is NULL (a
    ToxTemp with no recorded creator is not thrown out), and an exclude on a
    forward key can never fan one row out into duplicates.
    """
    if staff:
        qs = qs.exclude(**{f"{prefix}is_staff": True})
    for domain in config.stats_excluded_email_domains:
        qs = qs.exclude(**{f"{prefix}email__iendswith": f"@{domain}"})
    return qs


def real_assays() -> QuerySet[Assay]:
    """ToxTemps that count as uptake.

    Leaves out the seeded demo template and its per-user copies, and ToxTemps
    created by — or sitting in an investigation owned by — an account in
    ``Config.stats_excluded_email_domains``. A ToxTemp with no recorded creator
    is judged on its investigation owner alone. Staff ToxTemps count: they are
    part of the tool's record, even though staff accounts are not counted as
    users (see :func:`people`).
    """
    qs = _exclude_non_users(
        Assay.objects.filter(REAL_ASSAY_Q), "created_by__", staff=False
    )
    return _exclude_non_users(qs, "study__investigation__owner__", staff=False)


def people() -> QuerySet[Person]:
    """Accounts that count as users.

    Excludes django-guardian's AnonymousUser sentinel — guardian materialises a
    Person row for anonymous object-permission lookups (``ANONYMOUS_USER_NAME``,
    default ``"AnonymousUser"``), which would offset every per-account KPI by one
    — plus staff accounts and accounts in ``Config.stats_excluded_email_domains``.
    """
    sentinel = getattr(settings, "ANONYMOUS_USER_NAME", "AnonymousUser")
    qs = _exclude_non_users(Person.objects.all())
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


def _mean(values: list[int | float]) -> float | None:
    """Mean of ``values`` rounded to one decimal (empty list yields ``None``)."""
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def humanize_seconds(seconds: float | None) -> str:
    """Render a duration as ``9d 4h`` / ``4h 12m`` / ``38m`` / ``45s``.

    Days matter because the draft-to-export figure spans calendar time — without
    them a two-week ToxTemp reads as "336h 00m", which nobody can parse at a
    glance. ``None`` renders as an em dash.
    """
    if seconds is None:
        return "—"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
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


# ── Institutions ─────────────────────────────────────────────────────────────


def _institution_key(ror_id: str, organization: str) -> str:
    """Identity of an institution: the ROR id when matched, else the folded name.

    ``""`` means no institution on file.
    """
    return (ror_id or "").strip() or (organization or "").strip().casefold()


def _institutions() -> tuple[dict[int, str], dict[str, dict[str, Any]]]:
    """Group :func:`people` by institution.

    Returns ``(person id -> institution key, key -> group)``. Each group carries
    its ``users`` count, the ``first_joined`` date of its earliest account, and
    the ``name`` to show: the ROR display name when matched, else the most
    common raw spelling among its accounts (ties broken alphabetically).

    A ROR match is trusted only while ``ror_checked_organization`` still equals
    ``organization``: after an edit whose lookup has not run (or failed), the
    old id belongs to the old name, so the account falls back to its raw text.
    """
    person_key: dict[int, str] = {}
    spellings: dict[str, Counter[str]] = {}
    groups: dict[str, dict[str, Any]] = {}
    rows = people().values_list(
        "pk", "ror_id", "ror_name", "ror_checked_organization", "organization",
        "date_joined",
    )
    for pk, ror_id, ror_name, checked, organization, joined in rows:
        if checked != organization:
            ror_id = ror_name = ""
        key = _institution_key(ror_id, organization)
        person_key[pk] = key
        group = groups.setdefault(key, {"users": 0, "first_joined": joined})
        group["users"] += 1
        group["first_joined"] = min(group["first_joined"], joined)
        matched = (ror_id or "").strip() and (ror_name or "").strip()
        name = ror_name.strip() if matched else (organization or "").strip()
        if name:
            spellings.setdefault(key, Counter())[name] += 1
    for key, group in groups.items():
        names = spellings.get(key)
        group["name"] = (
            min(names.items(), key=lambda kv: (-kv[1], kv[0]))[0] if names else ""
        )
    return person_key, groups


# ── Section builders ─────────────────────────────────────────────────────────


def headline(rng: StatsRange) -> dict[str, Any]:
    """All-time totals plus the in-period delta for the hero tile row."""
    assays = real_assays()
    persons = people()
    completed = _completed_assays(assays)

    named = [group for key, group in _institutions()[1].items() if key]
    n_new_orgs = sum(
        1 for group in named if rng.since is None or group["first_joined"] >= rng.since
    )

    in_period = _scoped(assays, "submission_date", rng)
    n_period = in_period.count()
    # A ToxTemp somebody has accepted at least one answer on is being worked on,
    # as opposed to one that was created and left. It is the outer ring of the
    # ToxTemps dial, with "completed" as the inner ring inside it.
    n_worked_on = in_period.filter(answers__accepted=True).distinct().count()
    n_completed = _scoped(completed, "submission_date", rng).count()
    completed_pct = _pct(n_completed, n_period) or 0.0
    worked_pct = _pct(n_worked_on, n_period) or 0.0

    return {
        "users": {
            "total": persons.count(),
            "period": _scoped(persons, "date_joined", rng).count(),
        },
        # "period" counts institutions whose earliest counted account joined
        # inside the window — institutions new to the tool — so a short range
        # does not re-count every institution that merely had a later signup.
        "organisations": {"total": len(named), "period": n_new_orgs},
        "assays": {
            "total": assays.count(),
            "period": n_period,
            "worked_on": n_worked_on,
            "worked_on_pct": worked_pct,
            # Bands for the headline bar: complete, worked on but not finished,
            # and created-then-left. The three always sum to 100.
            "in_progress": n_worked_on - n_completed,
            "in_progress_pct": round(worked_pct - completed_pct, 1),
            "untouched": n_period - n_worked_on,
            "untouched_pct": round(100.0 - worked_pct, 1),
        },
        "completed_assays": {
            "total": completed.count(),
            "period": n_completed,
            "pct": completed_pct,
            # Pre-built so the template does not have to concatenate an int and
            # a string — Django's `add` filter silently returns "" for that.
            "fraction": f"{n_completed}/{n_period}",
        },
    }


def _real_costs() -> QuerySet[AssayCost]:
    """Cost rows of counted ToxTemps only."""
    return AssayCost.objects.filter(assay__in=real_assays().values("pk"))


def _currency_symbol() -> str:
    """Most frequently recorded cost-unit symbol across counted ToxTemps' cost rows."""
    row = (
        _real_costs()
        .exclude(cost_unit="")
        .values("cost_unit")
        .annotate(n=Count("pk"))
        .order_by("-n")
        .first()
    )
    if not row:
        return "€"
    from toxtempass.azure_registry import cost_unit_symbol

    return cost_unit_symbol(row["cost_unit"])


def _bucket_start(day: dt.date, bucket: str) -> dt.date:
    """First day of the bucket ``day`` falls in (weeks start Monday, as TruncWeek)."""
    if bucket == "month":
        return day.replace(day=1)
    if bucket == "week":
        return day - dt.timedelta(days=day.weekday())
    return day


def _bucket_keys(start: dt.date, end: dt.date, bucket: str) -> list[dt.date]:
    """Every bucket from the one holding ``start`` through ``end``.

    A month with no signups must render as a zero, not vanish — dropping it
    silently compresses the time axis and makes the trend read wrong. Running
    through the current bucket also shows a quiet spell at the right edge,
    instead of ending the line on the last busy month.
    """
    out: list[dt.date] = []
    current = _bucket_start(start, bucket)
    while current <= end and len(out) < 400:
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

    The axis runs from the window start (the first bucket with data, for all
    time) through the current bucket, zero-filled.
    """
    all_people, all_assays = people(), real_assays()
    users = _timeseries(all_people, "date_joined", rng)
    assays = _timeseries(all_assays, "submission_date", rng)

    today = _bucket_start(timezone.localdate(), rng.bucket)
    if rng.since is not None:
        start = timezone.localtime(rng.since).date()
    else:
        start = min(set(users) | set(assays), default=today)
    keys = _bucket_keys(start, today, rng.bucket)
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


# ── Answer bands ─────────────────────────────────────────────────────────────

# The four mutually exclusive states of one answer, in the order they are drawn:
#   accepted  — accepted by an expert, whatever the text;
#   drafted   — not accepted, with text other than the not-found sentence;
#   not_found — not accepted, and the model wrote Config.not_found_string;
#   empty     — not accepted, no text.
_BANDS = ("accepted", "drafted", "not_found", "empty")
# ``accepted`` is a nullable boolean and a fresh answer is NULL, so "not
# accepted" has to name both values — NOT (accepted = true) drops the NULLs.
_NOT_ACCEPTED = Q(accepted=False) | Q(accepted__isnull=True)


def _band_annotations() -> dict[str, Count]:
    """Count annotations for grouping Answer rows into the four bands."""
    not_found = Q(answer_text__icontains=config.not_found_string)
    return {
        "n_total": Count("pk"),
        "n_accepted": Count("pk", filter=Q(accepted=True)),
        "n_not_found": Count("pk", filter=_NOT_ACCEPTED & not_found),
        "n_empty": Count("pk", filter=_NOT_ACCEPTED & Q(answer_text="")),
        "n_any_not_found": Count("pk", filter=not_found),
        "n_any_empty": Count("pk", filter=Q(answer_text="")),
    }


def _bands_from_row(row: dict[str, Any]) -> dict[str, int]:
    """Turn one annotated row into band counts plus ``answered``.

    ``answered`` is the completeness measure: answers with real text, accepted or
    not — so an accepted not-found sentence is not an answer from the documents.
    """
    total = row["n_total"]
    accepted, not_found, empty = row["n_accepted"], row["n_not_found"], row["n_empty"]
    return {
        "total": total,
        "accepted": accepted,
        "drafted": total - accepted - not_found - empty,
        "not_found": not_found,
        "empty": empty,
        "answered": total - row["n_any_empty"] - row["n_any_not_found"],
    }


def _band_shares(entries: list[dict[str, int]]) -> dict[str, float]:
    """Mean per-ToxTemp share (%) of each band over ``entries``.

    Each ToxTemp is scored on its own answers and the scores are then averaged,
    so every ToxTemp counts the same regardless of questionnaire size. An entry
    with no answer rows (a section a ToxTemp has none for) scores as all empty.
    """
    if not entries:
        return dict.fromkeys(_BANDS, 0.0)
    sums = dict.fromkeys(_BANDS, 0.0)
    for entry in entries:
        if not entry["total"]:
            sums["empty"] += 100.0
            continue
        for band in _BANDS:
            sums[band] += 100.0 * entry[band] / entry["total"]
    return {band: round(sums[band] / len(entries), 1) for band in _BANDS}


def _drafted_assays(rng: StatsRange) -> dict[int, dict[str, Any]]:
    """Per-ToxTemp answer bands for ToxTemps created in the window that have answers.

    Answer rows are seeded on the first document upload, not when a ToxTemp is
    created, so a ToxTemp without any has never been drafted. It is left out of
    every average built on this — its bands would be undefined, not zero — and
    each figure states this base. ``documents`` is the number of distinct
    context-document names across the ToxTemp's answers (0 when none).
    ``questions`` is how many questions the ToxTemp's questionnaire defines —
    never fewer than its answer rows, which is also the fallback when it has no
    questionnaire.
    """
    assay_ids = _scoped(real_assays(), "submission_date", rng).values("pk")
    rows = list(
        Answer.objects.filter(assay__in=assay_ids)
        .values("assay_id", "assay__question_set_id")
        .annotate(**_band_annotations())
        .order_by()
    )
    sizes = dict(
        Question.objects.filter(
            subsection__section__question_set_id__in={
                row["assay__question_set_id"] for row in rows
            }
        )
        .values("subsection__section__question_set_id")
        .annotate(n=Count("pk"))
        .order_by()
        .values_list("subsection__section__question_set_id", "n")
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        bands = _bands_from_row(row)
        out[row["assay_id"]] = {
            **bands,
            "question_set": row["assay__question_set_id"],
            "questions": max(sizes.get(row["assay__question_set_id"], 0), bands["total"]),
            "documents": 0,
        }

    # Answer.answer_documents holds the document names that were in the payload
    # for that drafting run — what the user supplied, not what the model cited.
    # It is written whether or not the user consented to storing the files, so
    # it sees grounding that FileAsset cannot. Every answer of one run carries
    # the same list, so DISTINCT collapses the rows to about one per run.
    names: dict[int, set[str]] = {}
    documents = (
        Answer.objects.filter(assay__in=assay_ids, answer_documents__isnull=False)
        .values_list("assay_id", "answer_documents")
        .order_by()
        .distinct()
    )
    for assay_id, docs in documents:
        if docs:
            names.setdefault(assay_id, set()).update(docs)
    for assay_id, docs in names.items():
        if assay_id in out:
            out[assay_id]["documents"] = len(docs)
    return out


def average_progress(drafted: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Mean per-ToxTemp share of answers in each band, over drafted ToxTemps."""
    return {"assays": len(drafted), **_band_shares(list(drafted.values()))}


def section_progress(
    rng: StatsRange, drafted: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Mean per-ToxTemp answer bands for each section of the questionnaire.

    Scoped to whichever QuestionSet the most drafted ToxTemps in the window use,
    since sections are not comparable across questionnaire versions — merging a
    v1 section with a similarly titled v2 one would silently average two
    different question lists. Sections keep their seeded order (pk), which is the
    order they appear in the ToxTemp itself. ``questions`` is how many questions
    that section of the questionnaire defines.
    """
    usage = Counter(e["question_set"] for e in drafted.values() if e["question_set"])
    if not usage:
        return {"question_set": None, "assays": 0, "sections": []}
    qset_id, n_assays = usage.most_common(1)[0]
    base = [pk for pk, entry in drafted.items() if entry["question_set"] == qset_id]

    # One row per (ToxTemp, section) with that section's band counts.
    rows = (
        Answer.objects.filter(
            assay__in=_scoped(real_assays(), "submission_date", rng)
            .filter(question_set_id=qset_id)
            .values("pk"),
            question__subsection__section__question_set_id=qset_id,
        )
        .values("assay_id", "question__subsection__section_id")
        .annotate(**_band_annotations())
        .order_by()
    )
    per_section: dict[int, dict[int, dict[str, int]]] = {}
    for row in rows:
        per_section.setdefault(row["question__subsection__section_id"], {})[
            row["assay_id"]
        ] = _bands_from_row(row)

    questions = dict(
        Question.objects.filter(subsection__section__question_set_id=qset_id)
        .values("subsection__section_id")
        .annotate(n=Count("pk"))
        .order_by()
        .values_list("subsection__section_id", "n")
    )
    titles = (
        Section.objects.filter(question_set_id=qset_id)
        .order_by("pk")
        .values_list("pk", "title")
    )
    no_rows = {"total": 0}
    sections = []
    for section_id, title in titles:
        entries = per_section.get(section_id, {})
        sections.append(
            {
                "title": title,
                "questions": questions.get(section_id, 0),
                **_band_shares([entries.get(pk, no_rows) for pk in base]),
            }
        )

    qset = QuestionSet.objects.values("label", "display_name").get(pk=qset_id)
    return {
        "question_set": qset["display_name"] or qset["label"],
        "assays": n_assays,
        "sections": sections,
    }


def grounding(drafted: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """How much source material users gave the model, per drafted ToxTemp.

    A drafted ToxTemp with no document names recorded counts as 0, so the median
    is over every ToxTemp in the base rather than only the ones with documents.
    """
    counts = sorted(entry["documents"] for entry in drafted.values())
    return {
        "assays": len(counts),
        "assays_with_documents": sum(1 for c in counts if c),
        "median_documents": _median(counts),
        "max_documents": counts[-1] if counts else 0,
        "distinct_documents": sum(counts),
    }


def completeness(drafted: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Questions answered from the documents, per drafted ToxTemp on average.

    An answer counts when it has text that is not the not-found sentence,
    accepted or not. ``questions_mean`` is the mean size of those ToxTemps'
    questionnaires, and each share is taken over the questionnaire, so a
    question with no answer row counts as unanswered. ``bands`` splits the mean
    share by context-document count (``Config.stats_document_bands``); a band
    with no ToxTemps has ``completeness`` None and draws no bar.
    """
    entries = list(drafted.values())

    def _share(entry: dict[str, Any]) -> float:
        """Share of this ToxTemp's questions answered from the documents."""
        return 100.0 * entry["answered"] / entry["questions"]

    bands = []
    for label, low, high in config.stats_document_bands:
        members = [
            e for e in entries
            if e["documents"] >= low and (high is None or e["documents"] <= high)
        ]
        bands.append(
            {
                "label": label,
                "assays": len(members),
                "completeness": _mean([_share(e) for e in members]),
            }
        )
    return {
        "assays": len(entries),
        "answered_mean": _mean([e["answered"] for e in entries]),
        "questions_mean": _mean([e["questions"] for e in entries]),
        "share": _mean([_share(e) for e in entries]),
        "bands": bands,
    }


def answer_quality(rng: StatsRange) -> dict[str, Any]:
    """Return acceptance, coverage and human-edit rates for answers in the window."""
    assay_ids = _scoped(real_assays(), "submission_date", rng).values("pk")
    answers = Answer.objects.filter(assay__in=assay_ids)

    total = answers.count()
    accepted = answers.filter(accepted=True).count()
    empty = answers.filter(answer_text="").count()
    not_found = answers.filter(answer_text__icontains=config.not_found_string).count()

    # CAUTION: this is "answers a user saved", not "answers a user rewrote".
    # process_llm_async writes drafts with Answer.objects.filter(...).update(),
    # which bypasses save() and so records no history row at all — the model's
    # draft is invisible here. Accepting an answer calls save(), so an untouched
    # answer that was merely accepted also lands in this count. Kept in the
    # export for continuity; do not put it on the page as an edit rate.
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
    this dashboard. Accounts are grouped by ROR id when matched, else by their
    case- and whitespace-folded organisation name. A ToxTemp belongs to the
    institution of its creator, or of the investigation owner when no creator
    was recorded. Accounts with no institution are pooled under a single "Not
    specified" row, which carries no inline bar (``share`` None) and does not
    set the scale — it is not an institution, so it must not dwarf the real ones.
    """
    person_key, groups = _institutions()

    completed_ids = set(_completed_assays(real_assays()).values_list("pk", flat=True))
    assays: Counter[str] = Counter()
    completed: Counter[str] = Counter()
    rows = real_assays().values_list(
        "pk", "created_by_id", "study__investigation__owner_id"
    )
    for pk, creator_id, owner_id in rows:
        key = person_key.get(creator_id if creator_id is not None else owner_id, "")
        assays[key] += 1
        if pk in completed_ids:
            completed[key] += 1
    # Only investigations holding a counted ToxTemp: every account gets a seeded
    # demo investigation, which would otherwise add one per user.
    investigations = Counter(
        person_key.get(owner_id, "")
        for owner_id in Investigation.objects.filter(
            owner__in=people(), pk__in=real_assays().values("study__investigation_id")
        ).values_list("owner_id", flat=True)
    )

    out = []
    for key in set(groups) | set(assays) | set(investigations):
        out.append(
            {
                "organisation": groups[key]["name"]
                if key
                else config.stats_unknown_organisation,
                "users": groups.get(key, {}).get("users", 0),
                "investigations": investigations.get(key, 0),
                "assays": assays.get(key, 0),
                "completed": completed.get(key, 0),
                "named": bool(key),
            }
        )
    out.sort(key=lambda r: (-r["assays"], -r["users"], r["organisation"]))
    # Each named row carries its share of the busiest named institution, so the
    # table can draw an inline scale instead of needing a chart beside it.
    busiest = max((r["assays"] for r in out if r["named"]), default=0)
    for row in out:
        row["share"] = (_pct(row["assays"], busiest) or 0.0) if row.pop("named") else None
    return out


def llm_usage(rng: StatsRange) -> dict[str, Any]:
    """Token and cost totals for the window, broken down per model."""
    qs = _scoped(_real_costs(), "created_at", rng)
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


def _draft_to_export_seconds(rng: StatsRange) -> list[float]:
    """Seconds from first AI draft to first export, per ToxTemp exported in the window.

    Exports are not logged, but a rating is required before a ToxTemp's first
    export and ``Feedback`` is one-to-one with the ToxTemp, so its submission
    date is the first-export moment. The start is the earliest ``AssayCost``
    row written before the export, when the first tracked drafting run finished;
    a ToxTemp with no such row (drafted before cost tracking, perhaps re-drafted
    after the export) falls back to its creation date. Non-positive spans are
    dropped. An estimate, and labelled as one on the page.
    """
    feedback = _scoped(
        Feedback.objects.filter(assay__in=real_assays().values("pk")),
        "submission_date",
        rng,
    )
    exported = list(
        feedback.values_list("assay_id", "submission_date", "assay__submission_date")
    )
    first_draft = dict(
        AssayCost.objects.filter(
            assay__in=feedback.values("assay_id"),
            created_at__lt=F("assay__feedback__submission_date"),
        )
        .values("assay_id")
        .annotate(first=Min("created_at"))
        .order_by()
        .values_list("assay_id", "first")
    )
    spans = []
    for assay_id, exported_at, created_at in exported:
        seconds = (exported_at - first_draft.get(assay_id, created_at)).total_seconds()
        if seconds > 0:
            spans.append(seconds)
    return spans


def engagement(rng: StatsRange) -> dict[str, Any]:
    """Active time and time from first draft to export for the window."""
    assays = _scoped(real_assays(), "submission_date", rng)

    seconds = (
        _exclude_non_users(
            AssayTimeLog.objects.filter(assay__in=assays.values("pk")), "user__"
        ).aggregate(s=Sum("seconds"))["s"]
        or 0
    )
    # Assay.completion_time_seconds is the sum of AssayTimeLog.seconds across
    # every collaborator, captured when the last answer was first accepted — so
    # it is hands-on effort, not how long the ToxTemp sat open. It is summed at
    # write time, so staff or synthetic collaborators cannot be taken out of it
    # here. Export only.
    completion_times = list(
        _completed_assays(assays)
        .exclude(completion_time_seconds=None)
        .values_list("completion_time_seconds", flat=True)
    )
    spans = _draft_to_export_seconds(rng)

    return {
        "active_seconds": seconds,
        "active_hours": round(seconds / 3600.0, 1),
        "median_completion_seconds": _median(completion_times),
        "completion_samples": len(completion_times),
        "median_to_export_seconds": _median(spans),
        "median_to_export_display": humanize_seconds(_median(spans)),
        "export_samples": len(spans),
    }


def feedback_stats(rng: StatsRange) -> dict[str, Any]:
    """Rating count/mean and a half-point histogram. Free text is never read."""
    qs = _scoped(
        Feedback.objects.filter(assay__in=real_assays().values("pk")),
        "submission_date",
        rng,
    )
    ratings = [
        r for r in qs.values_list("usefulness_rating", flat=True) if r is not None
    ]
    edges = config.stats_rating_bins
    top = edges[-1]
    created = _scoped(real_assays(), "submission_date", rng)

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
        # Share of ToxTemps created in the window that have been rated — both
        # counts on the same base, so a short window cannot exceed 100%.
        "response_rate": _pct(
            created.filter(feedback__isnull=False).count(), created.count()
        ),
    }


def file_stats(rng: StatsRange) -> dict[str, Any]:
    """Upload volume, stored bytes and the MIME mix for the window.

    FileAsset has no ToxTemp of its own. It reaches one through ``Answer.files``,
    but that link is written only on the first upload into a ToxTemp, so a
    re-upload has none. Files are therefore filtered on their uploader, and
    those linked to any ToxTemp that does not count are dropped as well.
    """
    uncounted = Assay.objects.exclude(pk__in=real_assays().values("pk"))
    available = _exclude_non_users(
        FileAsset.objects.filter(status=FileAsset.Status.AVAILABLE), "uploaded_by__"
    ).exclude(answers__assay__in=uncounted)
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
    """Shared workspaces created in the window, and their names.

    A workspace counts as shared when it has a second member — ``Workspace.save()``
    adds the owner the moment it is created, so one member means nobody was
    invited — and at least one counted ToxTemp sits in an investigation shared
    into it, so an invitation that never led to joint work is not reported as
    collaboration. The names are listed with that ToxTemp count; the module
    docstring explains why names are allowed here.
    """
    qs = _scoped(Workspace.objects.filter(owner__in=people()), "created_at", rng)
    counted = Q(memberships__user__in=people())
    multi_member = list(
        qs.annotate(n_members=Count("memberships", filter=counted, distinct=True))
        .filter(n_members__gt=1)
        .values_list("pk", flat=True)
    )
    workspace_field = "study__investigation__shared_in_workspaces__workspace_id"
    assay_counts = dict(
        real_assays()
        .filter(**{f"{workspace_field}__in": multi_member})
        .values(workspace_field)
        .annotate(n=Count("pk", distinct=True))
        .order_by()
        .values_list(workspace_field, "n")
    )
    names = dict(
        Workspace.objects.filter(pk__in=list(assay_counts)).values_list("pk", "name")
    )
    workspaces = sorted(
        ({"name": names[pk], "assays": n} for pk, n in assay_counts.items()),
        key=lambda w: (-w["assays"], w["name"]),
    )

    shared_ids = list(assay_counts)
    n_shared = len(shared_ids)
    n_memberships = WorkspaceMember.objects.filter(
        workspace__in=shared_ids, user__in=people()
    ).count()
    return {
        "workspaces_created": qs.count(),
        "shared_workspaces": n_shared,
        "members": n_memberships,
        "avg_members": round(n_memberships / n_shared, 1) if n_shared else None,
        "shared_investigations": WorkspaceInvestigation.objects.filter(
            workspace__in=shared_ids,
            investigation__in=real_assays().values("study__investigation_id"),
        ).count(),
        "workspaces": workspaces,
    }


def question_set_mix(rng: StatsRange) -> list[dict[str, Any]]:
    """Which questionnaire versions ToxTemps created in the window use.

    ToxTemps with no questionnaire are left out. A version is named by its
    display name, falling back to its label when that is blank; when two
    versions would read the same, both show "display name (label)". ``url``
    links the version's source JSON, but only when ``ToxTemp_<label>.json``
    ships with this deployment — a link to a missing file is worse than none.
    """
    qs = _scoped(real_assays(), "submission_date", rng).exclude(question_set=None)
    total = qs.count()
    rows = list(
        qs.values("question_set__label", "question_set__display_name")
        .annotate(n=Count("pk"))
        .order_by("-n")
    )
    shown = [
        (row["question_set__display_name"] or "").strip()
        or (row["question_set__label"] or "")
        for row in rows
    ]
    clashes = Counter(shown)
    out = []
    for row, name in zip(rows, shown, strict=True):
        label = row["question_set__label"] or ""
        display = (row["question_set__display_name"] or "").strip()
        if clashes[name] > 1 and display and label:
            name = f"{display} ({label})"
        has_json = label and (Path(settings.BASE_DIR) / f"ToxTemp_{label}.json").is_file()
        out.append(
            {
                "label": name,
                "url": config.questionnaire_json_url_template.format(label=label)
                if has_json
                else None,
                "count": row["n"],
                "pct": _pct(row["n"], total),
            }
        )
    return out


# ── Entry point ──────────────────────────────────────────────────────────────


def build_stats(range_key: str | None = None) -> dict[str, Any]:
    """Assemble the full KPI payload for ``range_key``.

    The result contains aggregates only and is safe to render, serialise to
    JSON, or export as CSV without further redaction.
    """
    rng = resolve_range(range_key)
    drafted = _drafted_assays(rng)
    head = headline(rng)
    progress = average_progress(drafted)
    # ToxTemps created in the window that never got answer rows: rows are seeded
    # on the first document upload, so these stopped before it. Shown next to the
    # per-ToxTemp averages so their smaller base is not a mystery.
    progress["without_answers"] = head["assays"]["period"] - progress["assays"]
    return {
        "generated_at": timezone.now(),
        "range": rng,
        "ranges": [(key, spec[0]) for key, spec in config.stats_ranges.items()],
        "headline": head,
        "growth": growth(rng),
        "progress": progress,
        "grounding": grounding(drafted),
        "completeness": completeness(drafted),
        "section_progress": section_progress(rng, drafted),
        "assay_status": assay_status(rng),
        "answers": answer_quality(rng),
        "organisations": organisation_rows(),
        "llm": llm_usage(rng),
        "engagement": engagement(rng),
        "feedback": feedback_stats(rng),
        "files": file_stats(rng),
        "collaboration": collaboration(rng),
        "question_sets": question_set_mix(rng),
    }


# The cache lives in the database, so a payload outlives a deploy. Without this,
# a new release reads the previous release's payload for up to a day and fails
# on any key it added (KeyError 'completeness' after v3.41.0). Keying on this
# module's source puts every change to the payload shape on a fresh key; the
# old entries are never read again and expire on their own.
_PAYLOAD_FINGERPRINT = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def _cache_key(range_key: str) -> str:
    """Return the cache key for a resolved range key, scoped to this module's source."""
    return f"{config.stats_cache_key_prefix}{_PAYLOAD_FINGERPRINT}:{range_key}"


def cached_stats(
    range_key: str | None = None, *, refresh: bool = False
) -> dict[str, Any]:
    """Return :func:`build_stats` for ``range_key``, recomputed at most daily.

    A full build is a few dozen aggregate queries over every answer in the
    database. Nothing here moves fast enough to be worth paying that on each
    page view, so the payload is cached for ``Config.stats_cache_seconds`` and
    the ``generated_at`` it carries becomes the "as of" time shown on the page.

    ``refresh=True`` skips the read and recomputes, which is what the dashboard's
    recalculate control does.
    """
    key = _cache_key(resolve_range(range_key).key)
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    stats = build_stats(range_key)
    cache.set(key, stats, config.stats_cache_seconds)
    return stats


def clear_stats_cache() -> None:
    """Drop every cached range, so the next read of any of them recomputes."""
    cache.delete_many([_cache_key(key) for key in config.stats_ranges])


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
        for sub, value in block.items():
            add("headline", f"{name}.{sub}", value)

    for section in ("progress", "grounding", "answers", "engagement"):
        for metric, value in stats[section].items():
            add(section, metric, value)
    for metric, value in stats["completeness"].items():
        if metric == "bands":
            for entry in value:
                add("completeness", f"documents {entry['label']}.assays", entry["assays"])
                add(
                    "completeness",
                    f"documents {entry['label']}.completeness",
                    entry["completeness"],
                )
        else:
            add("completeness", metric, value)
    for entry in stats["assay_status"]:
        add("assay_status", entry["label"], entry["count"])
    for metric, value in stats["llm"].items():
        if metric != "by_model":
            add("llm", metric, value)
    for entry in stats["llm"]["by_model"]:
        for metric, value in entry.items():
            if metric != "model":
                add("llm_by_model", f"{entry['model']}.{metric}", value)
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
        if metric == "workspaces":
            for entry in value:
                add("collaboration", f"workspace {entry['name']}", entry["assays"])
        else:
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
