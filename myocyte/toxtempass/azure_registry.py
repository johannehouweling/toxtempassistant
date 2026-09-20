"""Auto-discover Azure AI Foundry endpoints and models from environment variables.

Env-var convention
==================
Each endpoint is numbered E1, E2, ... and carries:

    AZURE_E{n}_ENDPOINT   – full base URL  (e.g. https://proj.westeurope.models.ai.azure.com)
    AZURE_E{n}_KEY        – API key for that endpoint

Models hosted on the endpoint are declared as pairs (plus optional tags):

    AZURE_E{n}_DEPLOY_{tag}  – deployment name visible in Foundry
    AZURE_E{n}_MODEL_{tag}   – underlying model id
    AZURE_E{n}_TAGS_{tag}    – (optional) comma-separated key:value metadata,
                               e.g. ``type:globalstandard,version:1``

``{tag}`` is an arbitrary uppercase label (GPT4O, MISTRAL, LLAMA, …) that
ties a deployment to its model.  The same tag must appear in both DEPLOY and
MODEL vars.

Example .env block::

    AZURE_E1_ENDPOINT=https://project-1.westeurope.models.ai.azure.com
    AZURE_E1_KEY=sk-...
    AZURE_E1_DEPLOY_GPT4O=gpt-4o-deployment
    AZURE_E1_MODEL_GPT4O=gpt-4o

The module exposes:
    ``build_registry()``  – returns a list of ``EndpointEntry`` dicts
    ``get_registry()``    – cached version of the above
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

logger = logging.getLogger("llm")

# Regex that matches AZURE_E<n>_ENDPOINT
_ENDPOINT_RE = re.compile(r"^AZURE_E(\d+)_ENDPOINT$")
# Regex for per-model vars (DEPLOY / MODEL / TAGS) under an endpoint index
_DEPLOY_RE = re.compile(r"^AZURE_E(\d+)_DEPLOY_(.+)$")
_MODEL_RE = re.compile(r"^AZURE_E(\d+)_MODEL_(.+)$")
_TAGS_RE = re.compile(r"^AZURE_E(\d+)_TAGS_(.+)$")


def _parse_tags(raw: str) -> dict[str, str]:
    """Parse ``key1:val1,key2:val2`` into a dict; bare tokens become ``label``.

    Keys are lower-cased so downstream code can rely on canonical names
    (``tier``, ``residency``, ``provider``, ``direct-from-azure``, ``version``).
    """
    out: dict[str, str] = {}
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            k, _, v = token.partition(":")
            out[k.strip().lower()] = v.strip()
        else:
            out.setdefault("label", token)
    return out


# Controlled vocabulary — surfaced for UI filters and validator warnings.
KNOWN_TAG_KEYS = {
    "tier", "residency", "provider", "direct-from-azure",
    "version", "label", "api", "retirement-date", "default",
    "temperature",
}

# Retired: model limits and prices now come from the published catalogue (see
# toxtempass.model_metadata) rather than from hand-maintained tags that went
# stale silently. Listed so _validate_tags can name them specifically instead of
# reporting them as unknown keys.
RETIRED_TAG_KEYS = {
    "context-window",
    "cost-input-1mtoken",
    "cost-output-1mtoken",
    "cost-unit",
}

# Maps uppercase ISO 4217 currency codes to display symbols.
COST_UNIT_SYMBOLS: dict[str, str] = {"EUR": "€", "USD": "$", "GBP": "£"}


def cost_unit_symbol(unit: str) -> str:
    """Return the display symbol for a currency unit code (e.g. ``Eur`` → ``€``).

    Falls back to the raw ``unit`` string for unrecognised codes, and to ``€``
    when ``unit`` is empty.
    """
    return COST_UNIT_SYMBOLS.get(unit.upper(), unit or "€")

# Number of days before retirement when a model starts showing as "retiring soon".
RETIRING_SOON_DAYS = 30
KNOWN_TIERS = {"regional", "datazone", "global", "batch"}
KNOWN_RESIDENCIES = {"eu", "us", "global"}
KNOWN_APIS = {"openai", "azure-openai", "anthropic", "foundry"}  # wire protocol


class PrivacyBadge:
    """UI-friendly data-handling labels derived from model tags."""

    EU_RESIDENT = "eu-resident"          # green — processing stays in EU
    AZURE_MSFT = "azure-msft-processor"  # amber — global routing, Microsoft as processor
    THIRD_PARTY = "third-party-global"   # red — global routing via third-party MaaS
    UNKNOWN = "unknown"                  # grey — tags missing / incomplete


def badge_icon(badge: str) -> str:
    """Return the privacy-badge icon (flag/globe/warning) for a given badge value."""
    return _BADGE_ICON.get(badge, "❓")


def badge_short(badge: str) -> str:
    """Return the short text label for a privacy badge."""
    return _BADGE_SHORT.get(badge, "Unknown")


def badge_color(badge: str) -> str:
    """Return a CSS hex colour for a privacy badge."""
    return _BADGE_COLOR.get(badge, "#8a8a8a")


def privacy_badge(tags: dict[str, str]) -> str:
    """Derive a coarse data-handling badge from a model's tag dict."""
    residency = tags.get("residency", "").lower()
    direct = tags.get("direct-from-azure", "").lower()
    if residency == "eu":
        return PrivacyBadge.EU_RESIDENT
    if residency in KNOWN_RESIDENCIES and direct == "true":
        return PrivacyBadge.AZURE_MSFT
    if residency in KNOWN_RESIDENCIES and direct == "false":
        return PrivacyBadge.THIRD_PARTY
    return PrivacyBadge.UNKNOWN


def _validate_tags(idx: int, tag: str, tags: dict[str, str]) -> None:
    """Warn on unknown tag keys or values outside the controlled vocabulary."""
    for k in tags:
        if k in RETIRED_TAG_KEYS:
            logger.warning(
                "AZURE_E%d_TAGS_%s: %r is no longer read -- limits and prices "
                "come from the model catalogue now. It is ignored; remove it "
                "when convenient.",
                idx,
                tag,
                k,
            )
        elif k not in KNOWN_TAG_KEYS:
            logger.warning("AZURE_E%d_TAGS_%s: unknown key %r", idx, tag, k)
    tier = tags.get("tier")
    if tier and tier not in KNOWN_TIERS:
        logger.warning(
            "AZURE_E%d_TAGS_%s: tier=%r not in %s", idx, tag, tier, sorted(KNOWN_TIERS)
        )
    residency = tags.get("residency")
    if residency and residency not in KNOWN_RESIDENCIES:
        logger.warning(
            "AZURE_E%d_TAGS_%s: residency=%r not in %s",
            idx, tag, residency, sorted(KNOWN_RESIDENCIES),
        )
    api = tags.get("api")
    if api and api not in KNOWN_APIS:
        logger.warning(
            "AZURE_E%d_TAGS_%s: api=%r not in %s",
            idx, tag, api, sorted(KNOWN_APIS),
        )
    temp = tags.get("temperature")
    if temp is not None and temp.strip().lower() not in ("none", "null", ""):
        try:
            float(temp)
        except ValueError:
            logger.warning(
                "AZURE_E%d_TAGS_%s: temperature=%r is not a number or 'none'",
                idx, tag, temp,
            )


@dataclass
class ModelEntry:
    """A single model available on an endpoint."""

    tag: str  # e.g. "GPT4O"
    deployment_name: str  # e.g. "gpt-4o-deployment"
    model_id: str  # e.g. "gpt-4o"
    # e.g. {"tier": "global", "residency": "eu", "provider": "openai"}
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def badge(self) -> str:
        """Return the privacy badge derived from tags. See ``PrivacyBadge`` for values."""
        return privacy_badge(self.tags)

    @property
    def api(self) -> str:
        """Return the wire protocol this deployment speaks (``openai`` or ``anthropic``)."""
        return self.tags.get("api", "openai").lower()

    @property
    def temperature_policy(self) -> float | str | None:
        """Per-model temperature taken from the optional ``temperature`` tag.

        - ``float``  → call the model with exactly this temperature
          (e.g. ``temperature:0``, ``temperature:0.7``).
        - ``"omit"`` → the model rejects the parameter (``temperature:none``);
          the client builder omits it so the provider applies its own default.
          Needed by models like Claude Opus 4.x that 400 on a ``temperature``.
        - ``None``   → tag absent; the caller-supplied temperature applies.
        """
        raw = self.tags.get("temperature")
        if raw is None:
            return None
        raw = raw.strip().lower()
        if raw in ("none", "null", ""):
            return "omit"
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid temperature tag %r on %s; ignoring it", raw, self.tag
            )
            return None

    @property
    def is_env_default(self) -> bool:
        """True when the deployment carries ``default:true`` in its TAGS env var.

        Used as a bootstrap default when no admin ``LLMConfig`` row exists yet
        (fresh deploys, headless tests, CI). Only the first such model wins.
        """
        return (self.tags.get("default") or "").lower() == "true"

    @property
    def retirement_date(self) -> "date | None":
        """Parse the ``retirement-date`` tag as a ``datetime.date``; ``None`` if absent/invalid."""
        from datetime import date as _date

        raw = self.tags.get("retirement-date", "").strip()
        if not raw:
            return None
        try:
            return _date.fromisoformat(raw)
        except ValueError:
            logger.warning("Invalid retirement-date %r on tag %s", raw, self.tag)
            return None

    @property
    def retirement_status(self) -> str:
        """Return ``"active" | "retiring_soon" | "retired"``.

        ``active`` is also used when no retirement date is set.
        """
        from datetime import date as _date

        rd = self.retirement_date
        if rd is None:
            return "active"
        today = _date.today()
        if rd <= today:
            return "retired"
        if (rd - today).days <= RETIRING_SOON_DAYS:
            return "retiring_soon"
        return "active"

    @property
    def days_until_retirement(self) -> int | None:
        """Return days until retirement, negative if already retired, or ``None``."""
        from datetime import date as _date

        rd = self.retirement_date
        if rd is None:
            return None
        return (rd - _date.today()).days


@dataclass
class EndpointEntry:
    """One Azure AI Foundry endpoint with its models."""

    index: int  # 1, 2, …
    endpoint: str  # full URL
    api_key: str
    api_version: str = ""  # e.g. "2024-05-01-preview" for Azure OpenAI / Foundry
    models: list[ModelEntry] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Human-friendly label derived from the URL."""
        # e.g. "project-1.westeurope" from https://project-1.westeurope.models.ai.azure.com
        try:
            host = self.endpoint.split("//")[1].split(".models.")[0]
        except (IndexError, AttributeError):
            host = self.endpoint
        return host

    def model_choices(self) -> list[tuple[str, str]]:
        """Return Django-style (value, display) choices for this endpoint's models."""
        return [(m.tag, _format_model_label(m)) for m in self.models]


_BADGE_ICON = {
    PrivacyBadge.EU_RESIDENT: "🇪🇺",   # data stays in the EU
    PrivacyBadge.AZURE_MSFT: "🌐",     # global routing, Microsoft as processor
    PrivacyBadge.THIRD_PARTY: "⚠️",   # global routing via third-party MaaS
    PrivacyBadge.UNKNOWN: "❓",       # tags missing / incomplete
}

_BADGE_SHORT = {
    PrivacyBadge.EU_RESIDENT: "EU-resident",
    PrivacyBadge.AZURE_MSFT: "Global (MS)",
    PrivacyBadge.THIRD_PARTY: "Global (3P)",
    PrivacyBadge.UNKNOWN: "Unknown",
}

_BADGE_COLOR = {
    PrivacyBadge.EU_RESIDENT: "#0f62fe",   # EU blue — trust
    PrivacyBadge.AZURE_MSFT: "#6f6f6f",    # neutral grey
    PrivacyBadge.THIRD_PARTY: "#a75d00",   # caution amber
    PrivacyBadge.UNKNOWN: "#8a8a8a",       # muted
}


def _format_model_label(m: "ModelEntry") -> str:
    """Render a compact, scan-friendly label: ``🇪🇺 gpt-4o · OpenAI · v1``."""
    icon = _BADGE_ICON.get(m.badge, "❓")
    provider = m.tags.get("provider", "").strip()
    version = m.tags.get("version", "").strip()

    parts = [f"{icon} {m.model_id}"]
    if provider:
        parts.append(provider.title())
    parts.append(_BADGE_SHORT.get(m.badge, ""))
    if version:
        parts.append(f"v{version}")
    if m.api != "openai":
        parts.append(f"api={m.api}")
    return " · ".join(p for p in parts if p)


def build_registry() -> list[EndpointEntry]:
    """Scan ``os.environ`` and return a sorted list of discovered endpoints."""
    # 1. Find all endpoint indices
    endpoints: dict[int, EndpointEntry] = {}
    for key, value in os.environ.items():
        m = _ENDPOINT_RE.match(key)
        if m:
            idx = int(m.group(1))
            api_key = os.environ.get(f"AZURE_E{idx}_KEY", "")
            if not api_key:
                logger.warning("AZURE_E%d_KEY is missing – skipping endpoint", idx)
                continue
            endpoints[idx] = EndpointEntry(
                index=idx,
                endpoint=value,
                api_key=api_key,
                api_version=os.environ.get(f"AZURE_E{idx}_API_VERSION", ""),
            )

    # 2. Attach deploy/model pairs (and optional tags)
    deploy_map: dict[tuple[int, str], str] = {}
    model_map: dict[tuple[int, str], str] = {}
    tags_map: dict[tuple[int, str], dict[str, str]] = {}

    for key, value in os.environ.items():
        dm = _DEPLOY_RE.match(key)
        if dm:
            deploy_map[(int(dm.group(1)), dm.group(2).upper())] = value
            continue
        mm = _MODEL_RE.match(key)
        if mm:
            model_map[(int(mm.group(1)), mm.group(2).upper())] = value
            continue
        tm = _TAGS_RE.match(key)
        if tm:
            tags_map[(int(tm.group(1)), tm.group(2).upper())] = _parse_tags(value)

    # Merge: only include tags where both DEPLOY and MODEL exist
    for (idx, tag), deploy_name in deploy_map.items():
        model_id = model_map.get((idx, tag))
        if not model_id:
            logger.warning(
                "AZURE_E%d_DEPLOY_%s has no matching MODEL_%s – skipping", idx, tag, tag
            )
            continue
        ep = endpoints.get(idx)
        if ep is None:
            logger.warning(
                "AZURE_E%d_DEPLOY_%s found but no AZURE_E%d_ENDPOINT – skipping",
                idx, tag, idx,
            )
            continue
        model_tags = tags_map.get((idx, tag), {})
        if model_tags:
            _validate_tags(idx, tag, model_tags)
        ep.models.append(ModelEntry(
            tag=tag,
            deployment_name=deploy_name,
            model_id=model_id,
            tags=model_tags,
        ))

    # Sort endpoints by index, models by tag
    result = sorted(endpoints.values(), key=lambda e: e.index)
    for ep in result:
        ep.models.sort(key=lambda m: m.tag)

    if result:
        total_models = sum(len(ep.models) for ep in result)
        logger.info(
            "Azure registry: %d endpoint(s), %d model(s) discovered",
            len(result), total_models,
        )
    else:
        logger.info("Azure registry: no AZURE_E*_ENDPOINT vars found")

    return result


@lru_cache(maxsize=1)
def get_registry() -> list[EndpointEntry]:
    """Return the cached result of ``build_registry()``."""
    return build_registry()


def get_endpoint(index: int) -> EndpointEntry | None:
    """Look up an endpoint by its index number."""
    for ep in get_registry():
        if ep.index == index:
            return ep
    return None


def get_model(endpoint_index: int, tag: str) -> tuple[EndpointEntry, ModelEntry] | None:
    """Look up a specific model on a specific endpoint."""
    ep = get_endpoint(endpoint_index)
    if ep is None:
        return None
    for m in ep.models:
        if m.tag == tag:
            return ep, m
    return None


def env_default_key() -> str | None:
    """Return the ``"idx:tag"`` of the first deployment tagged ``default:true``.

    Returns ``None`` if no env-tagged default exists. Logs a warning if more than
    one deployment carries the tag (first one wins).
    """
    matches: list[tuple[int, str]] = []
    for ep in get_registry():
        for m in ep.models:
            if m.is_env_default and m.retirement_status != "retired":
                matches.append((ep.index, m.tag))
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "Multiple deployments tagged default:true (%s) — using the first.",
            ", ".join(f"E{i}:{t}" for i, t in matches),
        )
    idx, tag = matches[0]
    return f"{idx}:{tag}"


def find_by_model_id(
    model_id: str, prefer_residency: str = "eu"
) -> tuple[EndpointEntry, ModelEntry] | None:
    """Resolve a bare model id (e.g. ``"gpt-4o-mini"``) to a concrete deployment.

    When multiple deployments match, prefers those whose residency tag matches
    ``prefer_residency`` (default ``"eu"``), then falls back to the first match.
    Returns ``None`` if no deployment hosts the requested model id.
    """
    matches: list[tuple[EndpointEntry, ModelEntry]] = []
    for ep in get_registry():
        for m in ep.models:
            if m.model_id == model_id:
                matches.append((ep, m))
    if not matches:
        return None
    # Prefer the requested residency when available
    preferred = [
        (ep, m) for ep, m in matches if m.tags.get("residency") == prefer_residency
    ]
    return preferred[0] if preferred else matches[0]


def endpoint_choices() -> list[tuple[int, str]]:
    """Return Django-style (value, display) choices for all endpoints."""
    return [(ep.index, ep.label) for ep in get_registry()]


def all_model_choices() -> list[tuple[str, str]]:
    """Flat list of (endpoint_index:tag, display) for every discovered model."""
    choices = []
    for ep in get_registry():
        for m in ep.models:
            value = f"{ep.index}:{m.tag}"
            label = f"[E{ep.index}] {_format_model_label(m)}"
            choices.append((value, label))
    return choices
