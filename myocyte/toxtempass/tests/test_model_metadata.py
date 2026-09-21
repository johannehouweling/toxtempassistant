"""The catalogue must not reintroduce the silence it was added to remove.

Two invariants carry that weight: a lookup has to find limits even when the
most specific key has only prices (the shape that let a 275k-token request
reach a 272k endpoint), and a refresh must never turn a known limit back into
an unknown one.
"""

from unittest.mock import patch

import httpx
import pytest

from toxtempass import model_metadata as mm
from toxtempass.models import LLMCatalogue


def _seed(models):
    catalogue = LLMCatalogue.load()
    catalogue.models_json = models
    catalogue.etag = "seed-etag"
    catalogue.save()
    mm.invalidate()
    return catalogue


def test_candidate_keys_tries_residency_first_then_the_family():
    keys = mm.candidate_keys("gpt-5.4-mini", tier="datazone", residency="eu")
    assert keys[0] == "azure/eu/gpt-5.4-mini"
    assert "azure/gpt-5.4-mini" in keys
    assert keys[-1] == "gpt-5.4-mini"


def test_candidate_keys_are_deduplicated_and_empty_for_blank_input():
    assert mm.candidate_keys("   ") == []
    keys = mm.candidate_keys("m", tier="datazone", residency="eu")
    assert len(keys) == len(set(keys))


@pytest.mark.django_db
def test_lookup_takes_prices_from_the_regional_key_and_limits_from_the_family():
    """The real shape of the incident: azure/eu/* has prices but no limits."""
    _seed({
        "azure/eu/gpt-5.4-mini": {
            "input_cost_per_token": 8.25e-07,
            "output_cost_per_token": 4.95e-06,
        },
        "azure/gpt-5.4-mini": {
            "max_input_tokens": 272000,
            "max_output_tokens": 128000,
            "input_cost_per_token": 7.5e-07,
        },
    })

    found = mm.lookup("gpt-5.4-mini", tier="datazone", residency="eu")

    # Limits come from the family key, which is the only one that has them.
    assert found.max_input_tokens == 272000
    assert found.has_limits
    assert found.resolved_from["max_input_tokens"] == "azure/gpt-5.4-mini"
    # Prices come from the regional key, which is more specific.
    assert found.input_cost_per_1m_tokens == pytest.approx(0.825)
    assert found.resolved_from["input_cost_per_1m_tokens"] == "azure/eu/gpt-5.4-mini"


@pytest.mark.django_db
def test_lookup_reports_missing_limits_rather_than_inventing_them():
    _seed({"azure/eu/brand-new": {"input_cost_per_token": 1e-06}})
    found = mm.lookup("brand-new", tier="datazone", residency="eu")
    assert found.max_input_tokens is None
    assert not found.has_limits


def test_merge_keeps_a_limit_that_upstream_dropped():
    existing = {"azure/m": {"max_input_tokens": 272000, "input_cost_per_token": 1e-06}}
    incoming = {"azure/m": {"input_cost_per_token": 2e-06}}

    merged = mm.merge(existing, incoming)

    # The price is updated; the limit upstream stopped publishing survives.
    assert merged["azure/m"]["input_cost_per_token"] == 2e-06
    assert merged["azure/m"]["max_input_tokens"] == 272000


def test_merge_adds_new_models_and_corrects_existing_values():
    merged = mm.merge(
        {"azure/a": {"max_input_tokens": 100}},
        {"azure/a": {"max_input_tokens": 200}, "azure/b": {"max_input_tokens": 300}},
    )
    assert merged["azure/a"]["max_input_tokens"] == 200
    assert merged["azure/b"]["max_input_tokens"] == 300


def test_trim_keeps_only_chat_models_from_our_providers():
    trimmed = mm.trim({
        "azure/keep": {
            "litellm_provider": "azure", "mode": "chat",
            "max_input_tokens": 1, "supports_vision": True,
        },
        "cohere/drop": {"litellm_provider": "cohere", "mode": "chat"},
        "azure/embed": {"litellm_provider": "azure", "mode": "embedding"},
    })
    assert set(trimmed) == {"azure/keep"}
    # Unused fields are dropped so the cached copy stays small.
    assert "supports_vision" not in trimmed["azure/keep"]


@pytest.mark.django_db
def test_refresh_refuses_an_implausible_payload_and_keeps_the_cached_copy():
    """A truncated download must not replace good data with a stub."""
    _seed({"azure/m": {"max_input_tokens": 272000}})
    tiny = {"azure/only": {"litellm_provider": "azure", "mode": "chat",
                           "max_input_tokens": 1}}

    with patch.object(mm, "_fetch", return_value=(tiny, "new-etag", "u")):
        with pytest.raises(mm.CatalogueUnavailableError, match="only 1 usable"):
            mm.refresh()

    catalogue = LLMCatalogue.load()
    assert catalogue.models_json == {"azure/m": {"max_input_tokens": 272000}}
    assert catalogue.etag == "seed-etag"
    # The attempt is still recorded, so staleness reflects reality.
    assert catalogue.checked_at is not None


@pytest.mark.django_db
def test_refresh_on_304_records_the_check_without_touching_the_copy():
    _seed({"azure/m": {"max_input_tokens": 272000}})
    with patch.object(mm, "_fetch", return_value=(None, "seed-etag", "u")):
        result = mm.refresh()
    assert result["changed"] is False
    assert LLMCatalogue.load().models_json == {"azure/m": {"max_input_tokens": 272000}}


@pytest.mark.django_db
def test_refresh_stores_the_catalogue_and_reports_limit_changes():
    _seed({"azure/m": {"max_input_tokens": 100}})
    payload = {
        f"azure/filler{i}": {"litellm_provider": "azure", "mode": "chat",
                             "max_input_tokens": 1000}
        for i in range(mm.MIN_PLAUSIBLE_ENTRIES)
    }
    payload["azure/m"] = {"litellm_provider": "azure", "mode": "chat",
                          "max_input_tokens": 272000}

    with patch.object(mm, "_fetch", return_value=(payload, "etag-2", "url-2")):
        result = mm.refresh()

    assert result["changed"] is True
    assert "azure/m: max_input_tokens 100 -> 272000" in result["limit_changes"]
    catalogue = LLMCatalogue.load()
    assert catalogue.etag == "etag-2"
    assert catalogue.models_json["azure/m"]["max_input_tokens"] == 272000


@pytest.mark.django_db
def test_lookup_survives_every_mirror_being_unreachable():
    """No catalogue must degrade to 'unknown', never to a wrong guess."""
    LLMCatalogue.objects.all().delete()
    mm.invalidate()
    with patch.object(mm, "_fetch", side_effect=httpx.ConnectError("blocked")):
        found = mm.lookup("gpt-5.4-mini", tier="datazone", residency="eu")
    assert found.max_input_tokens is None
    assert not found.has_limits


def test_global_tier_skips_the_data_zone_row_and_its_ten_percent_premium():
    """Reading residency alone would bill a Global deployment 10% over."""
    assert mm.candidate_keys("m", tier="global", residency="eu")[0] == "azure/m"
    assert "azure/eu/m" not in mm.candidate_keys("m", tier="global", residency="eu")


@pytest.mark.django_db
def test_global_tier_gets_the_global_price_not_the_data_zone_price():
    _seed({
        "azure/eu/m": {"input_cost_per_token": 8.25e-07},
        "azure/m": {"input_cost_per_token": 7.5e-07, "max_input_tokens": 272000},
    })
    found = mm.lookup("m", tier="global", residency="eu")
    assert found.input_cost_per_1m_tokens == pytest.approx(0.75)
    assert not found.price_is_approximate


@pytest.mark.django_db
def test_regional_tier_is_flagged_because_upstream_has_no_row_for_it():
    """Regional costs more than Global; falling back must not look exact."""
    _seed({"azure/m": {"input_cost_per_token": 7.5e-07}})
    found = mm.lookup("m", tier="regional", residency="eu")
    assert found.price_is_approximate


def _fake_registry(*models):
    """Build a one-endpoint registry from (tag, model_id) pairs."""
    from types import SimpleNamespace

    entries = [
        SimpleNamespace(
            tag=tag, model_id=model_id, tags={"tier": "global"},
            retirement_status="active",
        )
        for tag, model_id in models
    ]
    return [SimpleNamespace(index=1, models=entries)]


def _allow(*keys):
    from toxtempass.models import LLMConfig

    cfg = LLMConfig.load()
    cfg.allowed_models = list(keys)
    cfg.save()


@pytest.mark.django_db
def test_suggests_the_smallest_model_that_fits():
    """Cheapest that solves the problem, not the biggest available."""
    from toxtempass.llm import model_with_room_for

    _seed({
        "azure/small": {"max_input_tokens": 100_000},
        "azure/medium": {"max_input_tokens": 300_000},
        "azure/huge": {"max_input_tokens": 1_000_000},
    })
    _allow("1:SMALL", "1:MEDIUM", "1:HUGE")
    registry = _fake_registry(("SMALL", "small"), ("MEDIUM", "medium"), ("HUGE", "huge"))

    with patch("toxtempass.azure_registry.get_registry", return_value=registry):
        assert model_with_room_for(None, 250_000) == "medium"


@pytest.mark.django_db
def test_suggests_nothing_when_the_user_has_no_choice():
    """An empty allowlist means everyone gets the default; a switch is not offered."""
    from toxtempass.llm import model_with_room_for

    _seed({"azure/huge": {"max_input_tokens": 1_000_000}})
    _allow()
    registry = _fake_registry(("HUGE", "huge"))

    with patch("toxtempass.azure_registry.get_registry", return_value=registry):
        assert model_with_room_for(None, 250_000) is None


@pytest.mark.django_db
def test_suggests_nothing_when_nothing_is_big_enough():
    """Suggesting a switch that would not help wastes the user's time."""
    from toxtempass.llm import model_with_room_for

    _seed({"azure/small": {"max_input_tokens": 100_000}})
    _allow("1:SMALL")
    registry = _fake_registry(("SMALL", "small"))

    with patch("toxtempass.azure_registry.get_registry", return_value=registry):
        assert model_with_room_for(None, 900_000) is None


@pytest.mark.django_db
def test_a_retired_model_is_never_suggested():
    from types import SimpleNamespace

    from toxtempass.llm import model_with_room_for

    _seed({"azure/huge": {"max_input_tokens": 1_000_000}})
    _allow("1:HUGE")
    retired = [SimpleNamespace(index=1, models=[SimpleNamespace(
        tag="HUGE", model_id="huge", tags={"tier": "global"},
        retirement_status="retired",
    )])]

    with patch("toxtempass.azure_registry.get_registry", return_value=retired):
        assert model_with_room_for(None, 250_000) is None
