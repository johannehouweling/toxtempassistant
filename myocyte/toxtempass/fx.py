"""The USD->EUR rate Azure bills at, derived from Azure's own price list.

Azure prices everything in USD and converts with "London closing spot rates
that are captured in the two business days prior to the last business day of
the previous month end", fixed for the following calendar month. The EUR figure
is therefore derived, not administered, which is why costs are stored in USD and
converted here rather than the other way round -- and why the rate must come
from the retail price list rather than an FX provider, so it reproduces the
invoice exactly.

Two measured facts shape this module.

Published EUR prices are **rounded**, so the ratio taken from a cheap meter is
wrong: across all Foundry meters the implied rate ranged 0.588 to 1.667, while
meters above USD 5 agreed to seven decimal places. Only high-value meters are
used.

``effectiveStartDate`` is **not** the FX reset date -- it records when the USD
list price last changed, and these meters still carry 2024 dates while their EUR
figure floats monthly on top. The rate change is detected by comparing the rate
itself, checked at most once a day.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from django.utils import timezone

logger = logging.getLogger(__name__)

AZURE_PRICES_URL = "https://prices.azure.com/api/retail/prices"
# One stable, high-value meter family: ~22 KB per currency instead of the ~300 KB
# an unfiltered service query returns, which matters for a repeated check.
PRICE_FILTER = (
    "serviceName eq 'Foundry Models' "
    "and meterName eq 'Provisioned Throughput Units'"
)
# Below this the published EUR figure's rounding dominates the ratio.
MIN_METER_USD = Decimal("5")
# A monthly rate needs no more than a daily check.
CHECK_INTERVAL = timedelta(hours=24)
# Ignore differences smaller than this; the source rounds at the eighth decimal.
RATE_EPSILON = Decimal("0.0000001")
# A sane band for EUR per USD. Anything outside is a bad response, not a rate.
RATE_MIN = Decimal("0.5")
RATE_MAX = Decimal("2.0")
FETCH_TIMEOUT_SECONDS = 30.0


class FxRateUnavailableError(RuntimeError):
    """The price list could not be read, or held no usable meter pair."""


def _fetch_prices(currency: str) -> dict[str, dict[str, Any]]:
    """Return the filtered price list for ``currency``, keyed by meter id."""
    response = httpx.get(
        AZURE_PRICES_URL,
        params={"currencyCode": f"'{currency}'", "$filter": PRICE_FILTER},
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return {item["meterId"]: item for item in response.json().get("Items", [])}


def fetch_rate() -> tuple[Decimal, str]:
    """Return ``(eur_per_usd, source_meter)`` derived from the price list.

    The same meters are read in both currencies and the ratio is taken across
    every pair priced at or above :data:`MIN_METER_USD`; the median absorbs the
    last-decimal rounding of individual rows.
    """
    usd = _fetch_prices("USD")
    eur = _fetch_prices("EUR")
    ratios: list[Decimal] = []
    meter = ""
    for meter_id, usd_item in usd.items():
        eur_item = eur.get(meter_id)
        if eur_item is None:
            continue
        usd_price = Decimal(str(usd_item.get("retailPrice") or 0))
        eur_price = Decimal(str(eur_item.get("retailPrice") or 0))
        if usd_price < MIN_METER_USD or eur_price <= 0:
            continue
        ratios.append(eur_price / usd_price)
        meter = meter or str(usd_item.get("meterName") or "")
    if not ratios:
        raise FxRateUnavailableError(
            "The Azure price list held no meter priced at or above "
            f"USD {MIN_METER_USD} in both currencies; the rate cannot be "
            "derived from rounded low-value meters."
        )
    rate = statistics.median(ratios)
    if not RATE_MIN <= rate <= RATE_MAX:
        raise FxRateUnavailableError(
            f"Derived rate {rate} is outside the plausible band "
            f"{RATE_MIN}-{RATE_MAX}; treating the response as bad."
        )
    return rate, meter


def refresh_fx_rate(now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    """Refresh the stored rate if it is due a check. Returns a summary to log.

    Safe on every periodic tick: the price list is only contacted when the
    current rate has not been confirmed within :data:`CHECK_INTERVAL`.
    """
    from toxtempass.models import AzureFxRate

    now = now or timezone.now()
    current = AzureFxRate.current()
    if (
        not force
        and current is not None
        and current.confirmed_at
        and now - current.confirmed_at < CHECK_INTERVAL
    ):
        return {"checked": False, "reason": "confirmed recently", "rate": current.rate}

    rate, meter = fetch_rate()

    if current is not None and abs(current.rate - rate) < RATE_EPSILON:
        # Touches confirmed_at so the next tick skips the fetch.
        current.save(update_fields=["confirmed_at"])
        return {"checked": True, "changed": False, "rate": current.rate}

    today = now.date()
    row, created = AzureFxRate.objects.update_or_create(
        observed_on=today,
        defaults={"rate": rate, "source_meter": meter},
    )
    logger.info(
        "Azure USD->EUR rate %s on %s (from %r, previous %s)",
        rate,
        today,
        meter,
        current.rate if current else "none",
    )
    return {
        "checked": True,
        "changed": True,
        "created": created,
        "rate": row.rate,
        "previous": current.rate if current else None,
    }
