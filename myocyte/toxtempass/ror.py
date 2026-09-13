"""Match free-text ``Person.organization`` to a Research Organization Registry record.

Institutions are counted on /stats by ROR id, so spelling variants and acronyms
of one institution count once. Two calls are tried because neither covers both
cases: the affiliation endpoint resolves full names (an item with chosen=True is
a confident match) but returns nothing for a bare acronym such as "RIVM", which
an exact name/acronym hit on the query endpoint does catch. Names matching
neither keep their raw string.
"""

from __future__ import annotations

import logging
import os

import requests
from django.db.models import QuerySet

from toxtempass import config
from toxtempass.models import Person

_LOG = logging.getLogger(__name__)


class RorLookupError(Exception):
    """The ROR API was unreachable or answered with something unusable."""


def _items(params: dict) -> list[dict]:
    """Return the ``items`` of one ROR API call; raise RorLookupError on any failure."""
    client_id = os.getenv("ROR_CLIENT_ID")
    try:
        response = requests.get(
            config.ror_organization_api_url,
            params=params,
            headers={"Client-Id": client_id} if client_id else {},
            timeout=config.ror_lookup_timeout_seconds,
        )
        if response.status_code != 200:
            raise RorLookupError(f"HTTP {response.status_code}")
        items = response.json()["items"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        raise RorLookupError(str(exc)) from exc
    if not isinstance(items, list):
        raise RorLookupError("items is not a list")
    return items


def _display_name(organization: dict) -> str:
    return next(
        (
            n["value"]
            for n in organization.get("names") or []
            if "ror_display" in (n.get("types") or [])
        ),
        "",
    )


def match_organization(name: str) -> tuple[str, str] | None:
    """Return ``(ror_id, display_name)``, or None when there is no confident match.

    Raises RorLookupError when the API fails, so callers can retry later instead of
    recording the name as unmatched.
    """
    name = (name or "").strip()
    if not name:
        return None
    try:
        for item in _items({"affiliation": name}):
            if item.get("chosen") is True:
                organization = item["organization"]
                return organization["id"], _display_name(organization)
        key = name.casefold()
        # ponytail: only the first result page (20 items) is checked for a second
        # exact-name holder; page on number_of_results if a false match turns up.
        hits = [
            item
            for item in _items({"query": name})
            if any(n["value"].casefold() == key for n in item.get("names") or [])
        ]
        return (hits[0]["id"], _display_name(hits[0])) if len(hits) == 1 else None
    except (KeyError, TypeError, AttributeError) as exc:
        raise RorLookupError(f"malformed ROR payload: {exc!r}") from exc


def lookup_organization(name: str) -> tuple[str, str] | None:
    """Like match_organization, but logs API failures and returns None; never raises."""
    try:
        return match_organization(name)
    except RorLookupError as exc:
        _LOG.warning("ROR lookup failed for %r: %s", name, exc)
        return None


def save_match(
    persons: QuerySet[Person], organization: str, match: tuple[str, str] | None
) -> int:
    """Record a lookup result on ``persons`` with .update(), so post_save stays quiet."""
    ror_id, ror_name = match or ("", "")
    return persons.update(
        ror_id=ror_id, ror_name=ror_name, ror_checked_organization=organization
    )


def resolve_person(person_or_pk: Person | int) -> None:
    """Match a Person's organization unless it was already checked.

    An API failure leaves the Person unchecked so a later save or backfill retries.
    """
    pk = getattr(person_or_pk, "pk", person_or_pk)
    person = Person.objects.filter(pk=pk).first()
    if person is None or person.organization == person.ror_checked_organization:
        return
    try:
        match = match_organization(person.organization)
    except RorLookupError as exc:
        _LOG.warning("ROR lookup failed for person %s: %s", pk, exc)
        return
    # Filtering on organization skips the write if it changed mid-lookup; that
    # save queued its own lookup.
    save_match(
        Person.objects.filter(pk=pk, organization=person.organization),
        person.organization,
        match,
    )
