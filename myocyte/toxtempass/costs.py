"""Token usage, prices and cost figures for LLM runs.

Prices are per million tokens and come from the model catalogue
(:mod:`toxtempass.model_metadata`), published in USD and converted with the rate
Azure itself bills at (:mod:`toxtempass.fx`).

Cached input is billed differently from fresh input, so a run's input cost is
split three ways, using the counts the provider reports on each response:

* **cache read** -- prefix tokens served from the prompt cache. OpenAI/Azure
  cache automatically; Anthropic caches what we mark with ``cache_control``.
  Billed at the catalogue's ``cache_read_input_token_cost`` (about 10% of input).
* **cache write** -- tokens written to the cache. Only Anthropic bills these,
  at ``cache_creation_input_token_cost`` (1.25x input for the 5-minute cache).
* **uncached** -- everything else, at the ordinary input price.

A missing cache price falls back to the input price: that overstates the cost
of a cache read rather than understating anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from toxtempass import model_metadata
from toxtempass.azure_registry import cost_unit_symbol

logger = logging.getLogger(__name__)

_PER_MILLION = Decimal(1_000_000)
# The precision the cost DecimalFields store. Prices are rounded to it before
# use, so a stored cost can be recomputed exactly from the stored price.
_STORED_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class TokenUsage:
    """Tokens used by one or more LLM calls.

    ``input`` is the whole prompt, cached parts included; ``cache_read`` and
    ``cache_write`` are the parts of it served from, or written to, the cache.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def from_response(cls, response: object) -> TokenUsage:
        """Read the counts from a langchain response's ``usage_metadata``.

        langchain normalises ``input_tokens`` to the full prompt for every
        provider (it adds Anthropic's cached tokens back in), and reports the
        cached parts under ``input_token_details``. When Anthropic reports the
        write per TTL, the generic ``cache_creation`` is zeroed and the tokens
        sit under the ``ephemeral_*`` keys instead, so all three are summed.
        """
        usage = getattr(response, "usage_metadata", None) or {}
        details = usage.get("input_token_details") or {}
        # `or 0` guards against providers that explicitly return None for a key.
        return cls(
            input=usage.get("input_tokens") or 0,
            output=usage.get("output_tokens") or 0,
            cache_read=details.get("cache_read") or 0,
            cache_write=(
                (details.get("cache_creation") or 0)
                + (details.get("ephemeral_5m_input_tokens") or 0)
                + (details.get("ephemeral_1h_input_tokens") or 0)
            ),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Sum two usages field by field."""
        return TokenUsage(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
        )

    def __bool__(self) -> bool:
        """Whether any token was counted at all."""
        return bool(self.input or self.output)

    @property
    def uncached_input(self) -> int:
        """Input tokens billed at the ordinary input price."""
        return max(self.input - self.cache_read - self.cache_write, 0)


@dataclass(frozen=True)
class CostRates:
    """Per-million-token prices for one deployment, in ``unit``.

    Every price is ``None`` when the catalogue has none for the model.
    ``fx_rate`` is the USD->EUR rate applied, recorded so a stored cost stays
    reproducible once the monthly rate moves; ``None`` when prices are in USD.
    """

    model_id: str = ""
    input: Decimal | None = None
    output: Decimal | None = None
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None
    unit: str = ""
    fx_rate: Decimal | None = None

    def input_cost(self, usage: TokenUsage) -> Decimal | None:
        """Cost of the prompt, with cached tokens at their own price."""
        if self.input is None:
            return None
        read = self.input if self.cache_read is None else self.cache_read
        write = self.input if self.cache_write is None else self.cache_write
        return (
            self.input * usage.uncached_input
            + read * usage.cache_read
            + write * usage.cache_write
        ) / _PER_MILLION

    def output_cost(self, usage: TokenUsage) -> Decimal | None:
        """Cost of the completion."""
        if self.output is None:
            return None
        return self.output * usage.output / _PER_MILLION

    def total_cost(self, usage: TokenUsage) -> Decimal | None:
        """Input plus output cost, or None when neither is priced."""
        cost_input = self.input_cost(usage)
        cost_output = self.output_cost(usage)
        if cost_input is None and cost_output is None:
            return None
        return (cost_input or 0) + (cost_output or 0)


def llm_cost_rates(model_key: str) -> CostRates:
    """Return the prices for the deployment ``model_key`` (``"<endpoint>:<tag>"``).

    Without a known FX rate the USD figures are returned as-is and the unit says
    so, rather than silently presenting dollars as euros.
    """
    from toxtempass.azure_registry import get_model as get_azure_model_entry
    from toxtempass.models import AzureFxRate

    model_id = ""
    tier = residency = None
    try:
        idx_s, tag = model_key.split(":", 1)
        result = get_azure_model_entry(int(idx_s), tag)
        if result is not None:
            _ep, entry = result
            model_id = entry.model_id
            tier = entry.tags.get("tier")
            residency = entry.tags.get("residency")
    except Exception as exc:
        logger.warning("Could not resolve deployment %r: %s", model_key, exc)
        return CostRates(model_id=model_id)

    if not model_id:
        return CostRates()

    metadata = model_metadata.lookup(model_id, tier=tier, residency=residency)
    if metadata.input_cost_per_1m_tokens is None:
        return CostRates(model_id=model_id)
    if metadata.price_is_approximate:
        logger.info(
            "No catalogue row for tier %r; pricing %s at the Global rate, "
            "which understates it.",
            tier,
            model_id,
        )

    rate_row = AzureFxRate.current()
    if rate_row is None:
        logger.warning(
            "No Azure USD->EUR rate stored yet; recording %s costs in USD.",
            model_id,
        )
    rate = rate_row.rate if rate_row is not None else None

    def convert(usd: float | None) -> Decimal | None:
        if usd is None:
            return None
        price = Decimal(str(usd)) * (rate if rate is not None else 1)
        return price.quantize(_STORED_PLACES, rounding=ROUND_HALF_UP)

    return CostRates(
        model_id=model_id,
        input=convert(metadata.input_cost_per_1m_tokens),
        output=convert(metadata.output_cost_per_1m_tokens),
        cache_read=convert(metadata.cache_read_cost_per_1m_tokens),
        cache_write=convert(metadata.cache_write_cost_per_1m_tokens),
        unit="Eur" if rate is not None else "Usd",
        fx_rate=rate,
    )


def format_cost(amount: Any, unit: str = "") -> str:  # noqa: ANN401 - Decimal/float/None
    """Format a money amount for display, e.g. ``€1.23`` or ``€0.0042``.

    Whole cents from one cent up; below that, two significant digits so a cheap
    run does not read as free. Trailing zeros past the cents are dropped.
    """
    if amount is None:
        return "—"
    value = Decimal(str(amount))
    sign = "-" if value < 0 else ""
    value = abs(value)
    places = 2
    if 0 < value < Decimal("0.01"):
        # adjusted() is the exponent of the leading digit: 0.0042 -> -3.
        places = -value.adjusted() + 1
    value = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{value:,.{places}f}"
    if places > 2:
        text = text.rstrip("0")
    return f"{sign}{cost_unit_symbol(unit)}{text}"
