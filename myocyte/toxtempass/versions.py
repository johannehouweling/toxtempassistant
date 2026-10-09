"""Versions of a ToxTemp, read from the histories of its answers and of the assay.

A ToxTemp has no stored versions. Every saved change to one of its answers, and
every change to the assay's title, description or questionnaire, leaves a history
row, and each row is a state: the ToxTemp as it was right after that change. The
id of a state is derived from the row (a UUIDv5), so it is the same every time it
is asked for, needs no storage, and never changes.

One action saves many rows (submitting the answers page saves every answer, and a
drafting run saves them as they finish), so the versions a person sees group them:
saves by the same person, or by the drafting run, with no more than
``Config._version_gap_seconds`` between them are one version, shown as the state
after the last of them (see :func:`history`). Every individual state keeps its id
and can still be asked for, so an id that was once listed never stops working.

An old version is rebuilt from the same rows: each answer as it was last saved
before that moment. Only what the histories keep can be rebuilt. The study and the
investigation, the questions, and the credit setting of the authors are read as
they are now, and the models that drafted an answer are the ones recorded before
that moment (a later run replaces the record of an earlier one).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.db.models import F, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce, Greatest

from toxtempass import config
from toxtempass.models import Answer, Assay

if TYPE_CHECKING:
    from uuid import UUID

# Any fixed value: version ids are derived from it, so it must never change.
VERSION_NAMESPACE = uuid.UUID("3b1f0c7e-6d1a-5c1e-9a57-0f6b2f8d4c11")

# Breaks a tie between a change to an answer and a change to the assay made in the
# same instant: the answer is taken to come first.
ANSWER_RANK, ASSAY_RANK = 0, 1
_KIND = {ANSWER_RANK: "answer", ASSAY_RANK: "assay"}


@dataclass(frozen=True)
class Version:
    """One state of a ToxTemp: the moment right after one saved change."""

    assay_id: int
    rank: int
    history_id: int
    at: datetime
    # Who saved it, or None for the drafting run. Only used to group saves.
    user_id: int | None = field(default=None, compare=False)

    @property
    def id(self) -> UUID:
        """Return the id of this version, the same every time."""
        name = f"{self.assay_id}:{_KIND[self.rank]}:{self.history_id}"
        return uuid.uuid5(VERSION_NAMESPACE, name)

    @property
    def key(self) -> tuple[datetime, int, int]:
        """Return what orders versions in time."""
        return (self.at, self.rank, self.history_id)


def versions(assay: Assay) -> list[Version]:
    """Return every saved state of ``assay``, the newest first.

    This is every history row, so a single submit of the answers page is many of
    them. What a person is shown is :func:`history`.
    """
    answer_rows = Answer.history.model.objects.filter(assay_id=assay.pk).values_list(
        "history_id", "history_date", "history_user_id"
    )
    assay_rows = Assay.history.model.objects.filter(id=assay.pk).values_list(
        "history_id", "history_date", "history_user_id"
    )
    found = [Version(assay.pk, ANSWER_RANK, h, d, u) for h, d, u in answer_rows]
    found += [Version(assay.pk, ASSAY_RANK, h, d, u) for h, d, u in assay_rows]
    return sorted(found, key=lambda version: version.key, reverse=True)


def history(assay: Assay) -> list[Version]:
    """Return the versions of ``assay`` a person sees, the newest first.

    Saves by the same person (or by the drafting run, which has no person) with no
    more than ``Config._version_gap_seconds`` between one and the next are one
    version, represented by the last of them: the state once the burst was over.
    """
    gap = timedelta(seconds=config._version_gap_seconds)
    bursts: list[Version] = []
    for state in reversed(versions(assay)):  # oldest first
        last = bursts[-1] if bursts else None
        same_burst = last is not None and (
            state.user_id == last.user_id and state.at - last.at <= gap
        )
        if same_burst:
            bursts[-1] = state  # the burst goes on; its last save represents it
        else:
            bursts.append(state)
    return bursts[::-1]


def find(assay: Assay, version_id: UUID) -> Version | None:
    """Return the state of ``assay`` with this id, or None.

    Any saved state resolves, not only the ones :func:`history` lists, so an id that
    was listed once keeps working after later saves join its burst.
    """
    return next((v for v in versions(assay) if v.id == version_id), None)


def with_latest_versions(queryset: QuerySet[Assay]) -> QuerySet[Assay]:
    """Annotate assays with their newest change and ``last_modified``.

    ``last_modified`` is the later of creation and the newest change of an answer
    or of the assay's title, description or questionnaire.
    """
    answers = Answer.history.model.objects.filter(assay_id=OuterRef("pk")).order_by(
        "-history_date", "-history_id"
    )
    assays = Assay.history.model.objects.filter(id=OuterRef("pk")).order_by(
        "-history_date", "-history_id"
    )
    return queryset.annotate(
        latest_answer_hid=Subquery(answers.values("history_id")[:1]),
        latest_answer_at=Subquery(answers.values("history_date")[:1]),
        latest_assay_hid=Subquery(assays.values("history_id")[:1]),
        latest_assay_at=Subquery(assays.values("history_date")[:1]),
    ).annotate(
        last_modified=Greatest(
            F("submission_date"),
            Coalesce(F("latest_answer_at"), F("submission_date")),
            Coalesce(F("latest_assay_at"), F("submission_date")),
        )
    )


def latest_version(assay: Assay) -> Version | None:
    """Return the newest version of ``assay``, or None if nothing was ever saved.

    Reads the annotations of :func:`with_latest_versions` when they are there, so
    a list of assays costs no query per assay.
    """
    if hasattr(assay, "latest_answer_hid"):
        newest = [
            (ANSWER_RANK, assay.latest_answer_hid, assay.latest_answer_at),
            (ASSAY_RANK, assay.latest_assay_hid, assay.latest_assay_at),
        ]
    else:
        newest = []
        for rank, model, field in (
            (ANSWER_RANK, Answer.history.model, "assay_id"),
            (ASSAY_RANK, Assay.history.model, "id"),
        ):
            row = (
                model.objects.filter(**{field: assay.pk})
                .order_by("-history_date", "-history_id")
                .values_list("history_id", "history_date")
                .first()
            )
            newest.append((rank, *(row if row else (None, None))))
    candidates = [
        Version(assay.pk, rank, hid, at) for rank, hid, at in newest if hid is not None
    ]
    return max(candidates, key=lambda version: version.key, default=None)


def answers_as_of(assay: Assay, version: Version) -> dict[int, object]:
    """Return each answer as last saved up to ``version``, by question id.

    The rows are the answers' history rows, which carry the same fields. An answer
    that had been deleted by then is left out.
    """
    latest: dict[int, object] = {}
    rows = Answer.history.model.objects.filter(
        assay_id=assay.pk, history_date__lte=version.at
    ).order_by("history_date", "history_id")
    for row in rows:
        if (row.history_date, ANSWER_RANK, row.history_id) <= version.key:
            latest[row.id] = row
    return {row.question_id: row for row in latest.values() if row.history_type != "-"}


def assay_as_of(assay: Assay, version: Version) -> object | None:
    """Return the assay's title, description and questionnaire as of ``version``.

    Before the assay's history began (it only starts when the assay is first saved
    after the history was introduced) the earliest row is the best that is known.
    None means there is no history of the assay at all.
    """
    rows = Assay.history.model.objects.filter(id=assay.pk).order_by(
        "history_date", "history_id"
    )
    first = chosen = None
    for row in rows:
        first = first or row
        if (row.history_date, ASSAY_RANK, row.history_id) <= version.key:
            chosen = row
    return chosen or first
