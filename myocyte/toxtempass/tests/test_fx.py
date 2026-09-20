"""The FX rate must come from meters where rounding cannot distort it.

Azure publishes EUR figures rounded for display. Across all Foundry meters the
implied rate ranged 0.588 to 1.667; only meters above USD 5 agreed to seven
decimals. These tests pin the guards that follow from that.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from toxtempass import fx
from toxtempass.models import AzureFxRate

RATE = Decimal("0.8586639")


def _prices(rows):
    """Build a meterId-keyed price map like the Azure API returns."""
    return {
        f"m{i}": {"meterId": f"m{i}", "retailPrice": price, "meterName": name}
        for i, (price, name) in enumerate(rows)
    }


def _both(usd_rows, eur_rows):
    return lambda currency: _prices(usd_rows if currency == "USD" else eur_rows)


def test_fetch_rate_ignores_cheap_meters_whose_published_price_is_rounded():
    """A per-token meter rounds to a ratio that is wildly wrong."""
    # m0 is cheap and rounds to a nonsense ratio; m1 is high-value and correct.
    usd = [(0.0001, "per token"), (312.0, "Provisioned Throughput Units")]
    eur = [(0.0002, "per token"), (267.9031, "Provisioned Throughput Units")]
    with patch.object(fx, "_fetch_prices", side_effect=_both(usd, eur)):
        rate, meter = fx.fetch_rate()
    # 0.0002/0.0001 would be 2.0; the cheap meter must not contribute.
    assert rate == pytest.approx(Decimal("0.85866378"), abs=Decimal("1e-7"))
    assert meter == "Provisioned Throughput Units"


def test_fetch_rate_refuses_when_only_cheap_meters_are_available():
    usd = [(0.0001, "per token")]
    eur = [(0.0002, "per token")]
    with patch.object(fx, "_fetch_prices", side_effect=_both(usd, eur)):
        with pytest.raises(fx.FxRateUnavailableError, match="USD 5"):
            fx.fetch_rate()


def test_fetch_rate_refuses_an_implausible_rate():
    """A bad response must not be mistaken for a currency collapse."""
    usd = [(100.0, "x")]
    eur = [(900.0, "x")]
    with patch.object(fx, "_fetch_prices", side_effect=_both(usd, eur)):
        with pytest.raises(fx.FxRateUnavailableError, match="plausible band"):
            fx.fetch_rate()


def test_fetch_rate_skips_meters_missing_from_the_other_currency():
    usd = [(312.0, "a"), (500.0, "b")]
    eur = [(267.9031, "a")]
    prices = {"USD": _prices(usd), "EUR": {"m0": _prices(eur)["m0"]}}
    with patch.object(fx, "_fetch_prices", side_effect=lambda c: prices[c]):
        rate, _ = fx.fetch_rate()
    assert rate == pytest.approx(Decimal("0.85866378"), abs=Decimal("1e-7"))


@pytest.mark.django_db
def test_refresh_skips_the_fetch_when_the_rate_was_confirmed_recently():
    AzureFxRate.objects.create(rate=RATE, observed_on=date(2026, 9, 1))
    with patch.object(fx, "fetch_rate", side_effect=AssertionError("must not fetch")):
        result = fx.refresh_fx_rate()
    assert result["checked"] is False


@pytest.mark.django_db
def test_refresh_records_a_new_row_when_the_rate_moved():
    old = AzureFxRate.objects.create(rate=Decimal("0.9"), observed_on=date(2026, 8, 1))
    AzureFxRate.objects.filter(pk=old.pk).update(
        confirmed_at=timezone.now() - timedelta(days=40)
    )
    with patch.object(fx, "fetch_rate", return_value=(RATE, "PTU")):
        result = fx.refresh_fx_rate()
    assert result["changed"] is True
    assert AzureFxRate.objects.count() == 2
    # current() is the newest, and the old row survives so past runs re-price.
    assert AzureFxRate.current().rate == pytest.approx(RATE, abs=Decimal("1e-7"))
    assert AzureFxRate.objects.filter(observed_on=date(2026, 8, 1)).exists()


@pytest.mark.django_db
def test_refresh_confirms_an_unchanged_rate_without_adding_a_row():
    row = AzureFxRate.objects.create(rate=RATE, observed_on=date(2026, 9, 1))
    AzureFxRate.objects.filter(pk=row.pk).update(
        confirmed_at=timezone.now() - timedelta(days=2)
    )
    before = AzureFxRate.objects.get(pk=row.pk).confirmed_at
    with patch.object(fx, "fetch_rate", return_value=(RATE, "PTU")):
        result = fx.refresh_fx_rate()
    assert result["changed"] is False
    assert AzureFxRate.objects.count() == 1
    assert AzureFxRate.objects.get(pk=row.pk).confirmed_at > before


@pytest.mark.django_db
def test_force_bypasses_the_daily_throttle():
    AzureFxRate.objects.create(rate=RATE, observed_on=date(2026, 9, 1))
    with patch.object(fx, "fetch_rate", return_value=(RATE, "PTU")) as fetch:
        fx.refresh_fx_rate(force=True)
    assert fetch.called
