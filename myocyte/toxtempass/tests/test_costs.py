"""Tests for cache-aware LLM pricing and cost display (toxtempass.costs)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from toxtempass import model_metadata
from toxtempass.azure_registry import ModelEntry
from toxtempass.costs import CostRates, TokenUsage, format_cost, llm_cost_rates
from toxtempass.models import AssayCost, AzureFxRate, LLMCatalogue, LLMRun
from toxtempass.tests.fixtures.factories import AssayFactory
from toxtempass.views import _save_assay_cost


def _response(usage: dict) -> SimpleNamespace:
    return SimpleNamespace(content="x", usage_metadata=usage)


def test_usage_reads_openai_cache_reads():
    usage = TokenUsage.from_response(
        _response(
            {
                "input_tokens": 1000,
                "output_tokens": 50,
                "input_token_details": {"cache_read": 800},
            }
        )
    )
    assert usage == TokenUsage(input=1000, output=50, cache_read=800, cache_write=0)
    assert usage.uncached_input == 200


def test_usage_reads_anthropic_writes_reported_per_ttl():
    """langchain zeroes cache_creation when it splits writes by TTL."""
    usage = TokenUsage.from_response(
        _response(
            {
                "input_tokens": 1000,
                "output_tokens": 50,
                "input_token_details": {
                    "cache_read": 0,
                    "cache_creation": 0,
                    "ephemeral_5m_input_tokens": 900,
                    "ephemeral_1h_input_tokens": 0,
                },
            }
        )
    )
    assert usage.cache_write == 900
    assert usage.uncached_input == 100


def test_usage_without_metadata_is_empty():
    usage = TokenUsage.from_response(SimpleNamespace(content="x"))
    assert usage == TokenUsage()
    assert not usage


def test_usage_adds_up():
    total = TokenUsage(10, 1, 5, 2) + TokenUsage(20, 2, 3, 0)
    assert total == TokenUsage(30, 3, 8, 2)


def test_cached_tokens_are_priced_at_the_cache_prices():
    rates = CostRates(
        input=Decimal("3"),
        output=Decimal("15"),
        cache_read=Decimal("0.3"),
        cache_write=Decimal("3.75"),
    )
    usage = TokenUsage(input=1_000_000, output=0, cache_read=600_000, cache_write=300_000)
    # 100k fresh at 3, 600k read at 0.3, 300k written at 3.75.
    assert rates.input_cost(usage) == Decimal("0.3") + Decimal("0.18") + Decimal("1.125")


def test_missing_cache_prices_fall_back_to_the_input_price():
    """Overstate a cache read rather than understate anything."""
    rates = CostRates(input=Decimal("2"), output=Decimal("8"))
    usage = TokenUsage(input=1_000_000, output=0, cache_read=500_000)
    assert rates.input_cost(usage) == Decimal("2")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, "—"),
        (0, "€0.00"),
        (Decimal("12.3456"), "€12.35"),
        (Decimal("1234.5"), "€1,234.50"),
        (Decimal("0.065"), "€0.07"),
        (Decimal("0.0042"), "<€0.01"),
        (Decimal("0.000123456"), "<€0.01"),
        (Decimal("0.01"), "€0.01"),
        (Decimal("-0.5"), "-€0.50"),
    ],
)
def test_format_cost(amount, expected):
    assert format_cost(amount, "Eur") == expected


def test_format_cost_accepts_a_symbol_and_usd():
    assert format_cost(1, "€") == "€1.00"
    assert format_cost(1, "Usd") == "$1.00"


@pytest.fixture
def priced_model():
    catalogue = LLMCatalogue.load()
    catalogue.models_json = {
        "azure/test-model": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 8e-06,
            "cache_read_input_token_cost": 2e-07,
        }
    }
    catalogue.save()
    model_metadata.invalidate()
    # A rate that does not divide evenly, to check the prices are rounded.
    AzureFxRate.objects.create(rate=Decimal("0.8651234567"), observed_on=date(2026, 9, 1))
    entry = ModelEntry(
        tag="TESTMODEL",
        deployment_name="test-deployment",
        model_id="test-model",
        tags={"tier": "global"},
    )
    with patch(
        "toxtempass.azure_registry.get_model", return_value=(SimpleNamespace(), entry)
    ):
        yield
    model_metadata.invalidate()


@pytest.mark.django_db
def test_rates_are_rounded_to_the_stored_precision(priced_model):
    rates = llm_cost_rates("1:TESTMODEL")
    assert rates.input == Decimal("1.730247")
    assert rates.cache_read == Decimal("0.173025")
    assert rates.cache_write is None


@pytest.mark.django_db
def test_save_assay_cost_prices_cache_reads_and_records_them(priced_model):
    assay = AssayFactory()
    _save_assay_cost(
        assay_id=assay.id,
        model_key="1:TESTMODEL",
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_tokens=1_000_000,
    )

    row = AssayCost.objects.get(assay=assay)
    assert row.cache_read_tokens == 1_000_000
    assert row.cost_cache_read_per_1m == Decimal("0.173025")
    # Every input token was a cache read, so the input cost is the read price.
    assert row.cost_input == Decimal("0.173025")
    # The stored figure is reproducible from the stored price.
    assert row.cost_input == row.cost_cache_read_per_1m
    run = LLMRun.objects.get(assay=assay)
    assert run.cache_read_tokens == 1_000_000
    assert run.cost == Decimal("0.173025")


@pytest.mark.django_db
def test_rerun_adds_cache_tokens_to_the_full_run(priced_model):
    assay = AssayFactory()
    _save_assay_cost(assay.id, "1:TESTMODEL", 1000, 10, cache_read_tokens=600)
    _save_assay_cost(
        assay.id, "1:TESTMODEL", 500, 5, cache_read_tokens=400, add_to_existing=True
    )
    row = AssayCost.objects.get(assay=assay)
    assert (row.input_tokens, row.cache_read_tokens) == (1500, 1000)
