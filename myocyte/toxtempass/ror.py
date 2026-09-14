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
import re

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


def suggest_organizations(raw_query: str, raw_email: str = "") -> list[dict]:
    """Return ROR records for a typed organization, those at the email domain first.

    Each item has the ``name`` to fill in, a ``label`` with the country and the ROR
    ``id``. Empty for a query that is too short, too long or has unusual characters.
    """

    def _extract_email_domain(raw_email: str) -> str | None:
        value = (raw_email or "").strip().lower()
        if "@" not in value:
            return None
        _, _, domain = value.rpartition("@")
        domain = domain.strip(".")
        if not domain or "." not in domain:
            return None
        if ".." in domain or len(domain) > 253:
            return None
        # RFC 1035-style hostname check: labels start/end alphanumeric, may
        # contain internal hyphens, max 63 chars per label, and include at
        # least one dot-separated suffix label (e.g., example.org).
        if not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+",
            domain,
        ):
            return None
        return domain

    def _fetch_ror_payload(advanced_query: str) -> dict:
        response = requests.get(
            config.ror_organization_api_url,
            params={"query.advanced": advanced_query},
            timeout=config.ror_lookup_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _escape_ror_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    query = " ".join((raw_query or "").split())
    if len(query) < config.ror_domain_lookup_min_query_length:
        return []
    if len(query) > config.ror_max_query_length:
        return []
    if not re.fullmatch(r"[A-Za-z0-9 .,-]+", query):
        return []
    can_run_general_lookup = len(query) >= config.ror_general_lookup_min_query_length
    email_domain = _extract_email_domain(raw_email)
    # Proceed when either the general text lookup is allowed or a valid email
    # domain enables the domain-first lookup path.
    if not can_run_general_lookup and email_domain is None:
        return []
    quoted_query = _escape_ror_query_value(query)
    # ROR v2 lists acronyms among the names; it has no `acronyms` field any more and
    # rejects a query that uses one.
    name_or_acronym_query = f'names.value:"{quoted_query}"'

    domain_queries = []
    if email_domain:
        quoted_email_domain = _escape_ror_query_value(email_domain)
        if can_run_general_lookup:
            domain_queries.append(
                f'links.value:"{quoted_email_domain}" AND {name_or_acronym_query}'
            )
        domain_queries.append(f'links.value:"{quoted_email_domain}"')
    query_batches = [domain_queries]
    if can_run_general_lookup:
        query_batches.append([name_or_acronym_query])

    seen_organizations: set[str] = set()
    suggestions = []
    for batch_idx, advanced_queries in enumerate(query_batches):
        is_domain_batch = batch_idx == 0 and bool(email_domain)
        for advanced_query in advanced_queries:
            try:
                payload = _fetch_ror_payload(advanced_query)
            except requests.RequestException:
                _LOG.exception(
                    "ROR lookup failed for query '%s' (advanced query: %s)",
                    query,
                    advanced_query,
                )
                continue

            if payload.get("errors"):
                # ROR answers a query it cannot parse with 200 and an error list.
                _LOG.warning(
                    "ROR rejected the query %s: %s", advanced_query, payload["errors"]
                )
                continue

            for item in payload.get("items", []):
                # ROR API now returns v2 schema: names live in a `names[]` array tagged
                # with `types` (preferred display = "ror_display"), country lives under
                # `locations[].geonames_details.country_name`. Fall back to the legacy
                # v1 flat shape for resilience.
                organization = item.get("organization", item)
                organization_name = organization.get("name")
                country_name = (organization.get("country") or {}).get("country_name")
                if not organization_name:
                    names = organization.get("names") or []
                    display_entry = next(
                        (n for n in names if "ror_display" in (n.get("types") or [])),
                        None,
                    )
                    label_entry = next(
                        (n for n in names if "label" in (n.get("types") or [])),
                        None,
                    )
                    entry = display_entry or label_entry or (names[0] if names else None)
                    organization_name = (entry or {}).get("value")
                if not country_name:
                    locations = organization.get("locations") or []
                    if locations:
                        country_name = (
                            locations[0].get("geonames_details") or {}
                        ).get("country_name")
                if not organization_name:
                    continue

                organization_id = organization.get("id")
                dedupe_key = (
                    organization_id if organization_id is not None else organization_name
                )
                if dedupe_key in seen_organizations:
                    continue
                seen_organizations.add(dedupe_key)

                # Show the name only. ROR names of companies end in their country, as
                # in "Avient Corporation (United States)"; drop it when the record also
                # has the plain name, so the shorter name still matches. A place that
                # is part of the name itself stays.
                country_suffix = f" ({country_name})" if country_name else ""
                if country_suffix and organization_name.endswith(country_suffix):
                    plain_name = organization_name.removesuffix(country_suffix)
                    known_names = {
                        entry.get("value") for entry in organization.get("names") or []
                    }
                    if plain_name in known_names:
                        organization_name = plain_name
                suggestions.append(
                    {
                        "name": organization_name,
                        "label": organization_name,
                        "id": organization.get("id"),
                    }
                )
                if len(suggestions) >= config.ror_max_suggestions:
                    return suggestions

        if email_domain and is_domain_batch and suggestions:
            return suggestions
    return suggestions
