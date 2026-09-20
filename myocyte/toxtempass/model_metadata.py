"""Model limits and prices, read from LiteLLM's published catalogue.

The numbers here used to live in ``AZURE_E<n>_TAGS_*`` and went stale silently:
a ``context-window`` tag naming a model's *total* 400k window let a 275k-token
request reach an endpoint that accepts 272k of input, and every answer came
back empty. They are sourced upstream instead, cached in
:class:`~toxtempass.models.LLMCatalogue` and refreshed by the periodic job.

Two rules keep the upstream dependency from reintroducing that silence.

Lookup is **per field, not per key**. The zoned rows
(``azure/eu/<model>``, ``azure/us/<model>``) are *Data Zone* deployments, priced
at a flat 1.10x the Global row -- verified across all 56 eu and 60 us pairs,
min and max both exactly 1.1000 -- and they carry prices and nothing else. A
third of the azure rows have no ``max_input_tokens``, so resolving a single key
yields Data Zone pricing with no limits at all. Each field instead takes the
first candidate key that has a value, most specific first, giving the right
price *and* the family's limits.

Which zone row applies is decided by the deployment's ``tier`` tag, not its
``residency``: only ``tier:datazone`` pays the Data Zone premium. Reading
residency alone would bill a Global deployment 10% over.

A refresh **never regresses a known value to unknown**. Upstream is edited by
people; if a model loses its ``max_input_tokens`` there, the last known value is
kept rather than dropped, because dropping it is what produced the incident.

``max_input_tokens`` is already the API's input ceiling, so a token budget is
``max_input_tokens`` minus headroom for the response. Subtracting
``max_output_tokens`` as well double-counts and would cut a 272k ceiling to
144k for nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from django.utils import timezone

logger = logging.getLogger(__name__)

# GitHub first; jsDelivr mirrors the same file and often reaches networks that
# block raw.githubusercontent.com.
CATALOGUE_URLS = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json",
    "https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/"
    "model_prices_and_context_window.json",
)
# The upstream file covers every provider LiteLLM knows; only these are ours.
KEEP_PROVIDERS = frozenset({"azure", "azure_ai", "openai", "anthropic"})
KEEP_FIELDS = (
    "max_input_tokens",
    "max_output_tokens",
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "deprecation_date",
)
# A healthy trim yields ~430 chat models. Anything far below that means a
# truncated download or an upstream restructure, and must not replace good data.
MIN_PLAUSIBLE_ENTRIES = 100
FETCH_TIMEOUT_SECONDS = 30.0
# The periodic job ticks every couple of minutes; upstream changes daily at
# most. Without a throttle a conditional GET would hit the mirror ~720 times a
# day purely to be told nothing changed.
CHECK_INTERVAL = timedelta(hours=6)

# Only Data Zone deployments have a zoned row upstream; Global uses the bare
# key. ``regional`` and ``batch`` have no matching row at all, so they fall back
# to Global pricing and are reported as approximate rather than billed silently.
DATA_ZONE_TIER = "datazone"
DATA_ZONE_RESIDENCIES = frozenset({"eu", "us"})
APPROXIMATE_PRICE_TIERS = frozenset({"regional", "batch"})

_PER_MILLION = 1_000_000
_COST_FIELDS = {
    "input_cost_per_token": "input_cost_per_1m_tokens",
    "output_cost_per_token": "output_cost_per_1m_tokens",
    "cache_read_input_token_cost": "cache_read_cost_per_1m_tokens",
}
_LIMIT_FIELDS = ("max_input_tokens", "max_output_tokens")

# Per-process copy of the cached catalogue; invalidated on refresh.
_MEMO: dict[str, dict] | None = None


class CatalogueUnavailableError(RuntimeError):
    """The catalogue could not be read and no cached copy exists."""


@dataclass
class ModelMetadata:
    """What the catalogue knows about one model.

    Every field is optional. The catalogue is incomplete for new models, and a
    missing ``max_input_tokens`` has to be reported rather than papered over
    with a fallback -- the fallback is what hid the original failure.
    """

    model_id: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    input_cost_per_1m_tokens: float | None = None
    output_cost_per_1m_tokens: float | None = None
    cache_read_cost_per_1m_tokens: float | None = None
    deprecation_date: date | None = None
    # True when the deployment's tier has no upstream row, so the price shown is
    # the Global one and understates what Azure actually charges.
    price_is_approximate: bool = False
    # Which catalogue key supplied each field, for the admin and the validator.
    resolved_from: dict[str, str] = field(default_factory=dict)

    @property
    def has_limits(self) -> bool:
        """Whether an input ceiling is known; without one, no budget can be derived."""
        return self.max_input_tokens is not None


def trim(raw: dict[str, Any]) -> dict[str, dict]:
    """Reduce the upstream file to the providers and fields this app uses."""
    return {
        name: {f: entry[f] for f in KEEP_FIELDS if entry.get(f) is not None}
        for name, entry in raw.items()
        if isinstance(entry, dict)
        and entry.get("litellm_provider") in KEEP_PROVIDERS
        and entry.get("mode") == "chat"
    }


def merge(existing: dict[str, dict], incoming: dict[str, dict]) -> dict[str, dict]:
    """Overlay ``incoming`` on ``existing`` without losing known values.

    Upstream is edited by people. A model that loses ``max_input_tokens`` there
    keeps the value already known here, so a refresh can add and correct facts
    but never take one away.
    """
    merged = {name: dict(entry) for name, entry in existing.items()}
    for name, entry in incoming.items():
        merged.setdefault(name, {}).update(entry)
    return merged


def describe_changes(
    existing: dict[str, dict], merged: dict[str, dict]
) -> list[str]:
    """Return human-readable notes for limit changes, for the refresh log."""
    notes = []
    for name, entry in merged.items():
        before = (existing.get(name) or {}).get("max_input_tokens")
        after = entry.get("max_input_tokens")
        if before is not None and after is not None and before != after:
            notes.append(f"{name}: max_input_tokens {before} -> {after}")
        elif before is None and after is not None and name in existing:
            notes.append(f"{name}: max_input_tokens now known ({after})")
    return notes


def _fetch(etag: str) -> tuple[dict[str, Any] | None, str, str]:
    """Fetch the catalogue, returning ``(payload, etag, url)``.

    ``payload`` is ``None`` when upstream reports the copy is unchanged (304).
    Mirrors are tried in order; the last error is raised if all of them fail.
    """
    headers = {"If-None-Match": etag} if etag else {}
    last_error: Exception | None = None
    for url in CATALOGUE_URLS:
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.warning("Catalogue mirror %s unreachable: %s", url, exc)
            last_error = exc
            continue
        if response.status_code == httpx.codes.NOT_MODIFIED:
            return None, etag, url
        response.raise_for_status()
        return response.json(), response.headers.get("etag", ""), url
    raise CatalogueUnavailableError(
        "No catalogue mirror could be reached"
    ) from last_error


def refresh(now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Refresh the cached catalogue. Returns a summary for the caller to log.

    Safe to call on every periodic tick: upstream is contacted at most once per
    :data:`CHECK_INTERVAL`, and that request is conditional on the stored ETag,
    so an unchanged catalogue costs one 304 and no payload. An empty cache
    ignores the throttle -- a cold container needs its first copy now.
    """
    from toxtempass.models import LLMCatalogue

    now = now or timezone.now()
    catalogue = LLMCatalogue.load()
    if (
        not force
        and catalogue.models_json
        and catalogue.checked_at
        and now - catalogue.checked_at < CHECK_INTERVAL
    ):
        return {"changed": False, "reason": "checked recently"}
    payload, etag, url = _fetch(catalogue.etag)
    catalogue.checked_at = now

    if payload is None:
        catalogue.save()
        return {"changed": False, "reason": "unchanged upstream", "url": url}

    incoming = trim(payload)
    if len(incoming) < MIN_PLAUSIBLE_ENTRIES:
        # A truncated download or an upstream restructure. Keeping the old copy
        # is always better than replacing it with something implausible.
        catalogue.save()
        raise CatalogueUnavailableError(
            f"Upstream catalogue held only {len(incoming)} usable models "
            f"(expected at least {MIN_PLAUSIBLE_ENTRIES}); keeping the cached copy."
        )

    existing = catalogue.models_json or {}
    merged = merge(existing, incoming)
    notes = describe_changes(existing, merged)

    catalogue.models_json = merged
    catalogue.etag = etag
    catalogue.source_url = url
    catalogue.fetched_at = now
    catalogue.save()
    invalidate()
    return {
        "changed": True,
        "url": url,
        "models": len(merged),
        "added": len(set(merged) - set(existing)),
        "limit_changes": notes,
    }


def invalidate() -> None:
    """Drop the per-process copy so the next lookup re-reads the cache."""
    global _MEMO  # noqa: PLW0603 - module-level memo, invalidated on refresh
    _MEMO = None


def _catalogue(allow_fetch: bool = False) -> dict[str, dict]:
    """Return the cached catalogue, optionally filling it if it is empty.

    Never called at import time: a network blip must not become a startup
    failure. ``allow_fetch`` is opt-in because the cold-start fetch can take
    seconds, which is fine in the background task that drafts answers and not
    fine in a request that is rendering a page.
    """
    global _MEMO  # noqa: PLW0603 - module-level memo, invalidated on refresh
    if _MEMO is not None:
        return _MEMO
    from toxtempass.models import LLMCatalogue

    catalogue = LLMCatalogue.load()
    if not catalogue.models_json and allow_fetch:
        try:
            refresh()
        except (CatalogueUnavailableError, httpx.HTTPError, ValueError):
            logger.exception(
                "Could not fill the empty model catalogue; limits and prices are "
                "unavailable until the periodic refresh succeeds."
            )
            return {}
        catalogue = LLMCatalogue.load()
    _MEMO = catalogue.models_json or {}
    return _MEMO


def candidate_keys(
    model_id: str, tier: str | None = None, residency: str | None = None
) -> list[str]:
    """Return catalogue keys to try for ``model_id``, most specific first.

    The zoned key leads, but only for a Data Zone deployment, because that is
    the one that carries the 10% premium. The unzoned azure keys and the bare
    model id follow, because that is where the limits live.
    """
    model = model_id.strip().lower()
    if not model:
        return []
    keys = []
    zone = (residency or "").strip().lower()
    if (tier or "").strip().lower() == DATA_ZONE_TIER and zone in DATA_ZONE_RESIDENCIES:
        keys.append(f"azure/{zone}/{model}")
    keys += [f"azure/{model}", f"azure_ai/{model}", model]
    # Keep order while dropping duplicates (residency may repeat a later key).
    return list(dict.fromkeys(keys))


def _parse_date(raw: object) -> date | None:
    """Parse an ISO date from the catalogue, ignoring anything malformed."""
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def lookup(
    model_id: str,
    tier: str | None = None,
    residency: str | None = None,
    allow_fetch: bool = False,
) -> ModelMetadata:
    """Resolve ``model_id`` against the catalogue, field by field.

    Fields no candidate key supplies stay ``None``; callers decide whether that
    is fatal (see :attr:`ModelMetadata.has_limits`). Pass ``allow_fetch`` only
    from a background task -- see :func:`_catalogue`.
    """
    metadata = ModelMetadata(
        model_id=model_id,
        price_is_approximate=(tier or "").strip().lower() in APPROXIMATE_PRICE_TIERS,
    )
    catalogue = _catalogue(allow_fetch=allow_fetch)
    for key in candidate_keys(model_id, tier, residency):
        entry = catalogue.get(key)
        if not entry:
            continue
        for source, attribute in _COST_FIELDS.items():
            if getattr(metadata, attribute) is None and entry.get(source) is not None:
                setattr(metadata, attribute, entry[source] * _PER_MILLION)
                metadata.resolved_from[attribute] = key
        for attribute in _LIMIT_FIELDS:
            if getattr(metadata, attribute) is None and entry.get(attribute) is not None:
                setattr(metadata, attribute, entry[attribute])
                metadata.resolved_from[attribute] = key
        if metadata.deprecation_date is None:
            parsed = _parse_date(entry.get("deprecation_date"))
            if parsed is not None:
                metadata.deprecation_date = parsed
                metadata.resolved_from["deprecation_date"] = key
    return metadata
