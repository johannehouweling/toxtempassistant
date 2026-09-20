"""Tests for LLM cost tracking (AssayCost model and _save_assay_cost helper)."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from toxtempass.models import (
    Answer,
    AssayCost,
    LLMRun,
    Question,
    QuestionSet,
    Section,
    Subsection,
)
from toxtempass.tests.fixtures.factories import AssayFactory
from toxtempass.views import process_llm_async, _save_assay_cost


class FakeLLMWithUsage:
    """Fake LLM that returns a response with usage_metadata."""

    def __init__(self, content="Test answer", input_tokens=100, output_tokens=50):
        self._content = content
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    def invoke(self, messages):
        return SimpleNamespace(
            content=self._content,
            usage_metadata={
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "total_tokens": self._input_tokens + self._output_tokens,
            },
        )


class FakeLLMNoUsage:
    """Fake LLM that returns a response without usage_metadata (simulates older providers)."""

    def invoke(self, messages):
        return SimpleNamespace(content="Answer without usage")


@pytest.fixture
def assay_with_questions():
    """Return an assay with 2 questions and seeded answer rows."""
    assay = AssayFactory()
    qs = QuestionSet.objects.create(
        display_name="cost-test-qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=qs, title="Sec Cost")
    subsection = Subsection.objects.create(section=section, title="Subsec Cost")
    q1 = Question.objects.create(subsection=subsection, question_text="Cost Q1?")
    q2 = Question.objects.create(subsection=subsection, question_text="Cost Q2?")
    Answer.objects.create(assay=assay, question=q1)
    Answer.objects.create(assay=assay, question=q2)
    return assay


@pytest.mark.django_db
def test_process_llm_async_saves_assaycost_when_llm_model_set(assay_with_questions):
    """When llm_model is provided and the LLM returns usage_metadata, AssayCost is created."""
    assay = assay_with_questions
    fake = FakeLLMWithUsage(input_tokens=200, output_tokens=80)

    # This test is about cost accounting, not model resolution. Whether the
    # ambient environment happens to define AZURE_E1_* decides whether the
    # budget guard resolves a model and demands a catalogue entry for it, so
    # pin resolution to "unknown" and let the conservative fallback apply.
    with patch("toxtempass.views.get_azure_model", return_value=None):
        process_llm_async(
            assay.id,
            doc_dict={},
            extract_images=False,
            chatopenai=fake,
            llm_model="1:GPT4O",
        )

    cost_rows = AssayCost.objects.filter(assay=assay)
    assert cost_rows.count() == 1
    row = cost_rows.first()
    assert row.model_key == "1:GPT4O"
    # 2 questions → 2 * 200 input, 2 * 80 output
    assert row.input_tokens == 400
    assert row.output_tokens == 160
    # The fake has no temperature attribute, i.e. none was sent.
    assert row.temperature == "provider default"


@pytest.mark.django_db
def test_run_temperature_is_recorded_and_exported(assay_with_questions):
    """The temperature sent is stored on AssayCost and shown in the export metadata."""
    from toxtempass.export import generate_json_from_assay

    assay = assay_with_questions
    _save_assay_cost(
        assay_id=assay.id,
        model_key="1:GPT4O",
        input_tokens=10,
        output_tokens=5,
        temperature="1",
    )

    assert AssayCost.objects.get(assay=assay).temperature == "1"
    meta = generate_json_from_assay(assay)["metadata"]
    assert meta["models_used"][0]["temperature"] == "1"
    assert "temperature 1" in meta["config"]["model"]


@pytest.mark.django_db
def test_rerun_tokens_add_to_the_full_runs_row(assay_with_questions):
    """A re-run of selected questions adds its tokens instead of erasing the full run."""
    assay = assay_with_questions

    with patch("toxtempass.azure_registry.get_model", return_value=None):
        _save_assay_cost(
            assay_id=assay.id, model_key="1:RERUN", input_tokens=100, output_tokens=50
        )
        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:RERUN",
            input_tokens=30,
            output_tokens=10,
            add_to_existing=True,
        )

    row = AssayCost.objects.get(assay=assay, model_key="1:RERUN")
    assert (row.input_tokens, row.output_tokens) == (130, 60)
    # The append-only run log keeps them apart: one row per run, not the total.
    runs = LLMRun.objects.filter(assay=assay).order_by("id")
    assert [r.input_tokens for r in runs] == [100, 30]


@pytest.mark.django_db
def test_process_llm_async_no_assaycost_when_no_model_key(assay_with_questions):
    """When llm_model is not provided, AssayCost is not created."""
    assay = assay_with_questions
    fake = FakeLLMWithUsage(input_tokens=100, output_tokens=50)

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=fake,
        # no llm_model
    )

    assert not AssayCost.objects.filter(assay=assay).exists()


@pytest.mark.django_db
def test_process_llm_async_no_assaycost_when_zero_tokens(assay_with_questions):
    """When usage_metadata is absent (tokens = 0), AssayCost is NOT created."""
    assay = assay_with_questions
    fake = FakeLLMNoUsage()

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=fake,
        llm_model="1:GPT4O",
    )

    assert not AssayCost.objects.filter(assay=assay).exists()


@pytest.mark.django_db
def test_save_assay_cost_prices_from_the_catalogue_and_records_the_rate(
    assay_with_questions,
):
    """Prices come from the catalogue in USD, converted with the stored rate.

    The rate is stored alongside so the figure stays reproducible after the
    monthly rate moves.
    """
    from datetime import date

    from toxtempass import model_metadata
    from toxtempass.azure_registry import ModelEntry
    from toxtempass.models import AzureFxRate, LLMCatalogue

    assay = assay_with_questions

    catalogue = LLMCatalogue.load()
    catalogue.models_json = {
        "azure/test-model": {
            "input_cost_per_token": 2e-06,
            "output_cost_per_token": 8e-06,
        }
    }
    catalogue.save()
    model_metadata.invalidate()
    # A round rate keeps the arithmetic obvious.
    AzureFxRate.objects.create(rate=Decimal("0.5"), observed_on=date(2026, 9, 1))

    fake_model = ModelEntry(
        tag="TESTMODEL",
        deployment_name="test-deployment",
        model_id="test-model",
        tags={"tier": "global", "residency": "eu"},
    )
    with patch("toxtempass.azure_registry.get_model") as mock_get_model:
        mock_ep = SimpleNamespace(endpoint="https://test.example.com", api_key="key")
        mock_get_model.return_value = (mock_ep, fake_model)
        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:TESTMODEL",
            input_tokens=1_000_000,
            output_tokens=500_000,
        )

    row = AssayCost.objects.get(assay=assay, model_key="1:TESTMODEL")
    # USD 2.00/1M and 8.00/1M at 0.5 → EUR 1.00 and 4.00 per 1M.
    assert row.cost_input_per_1m == Decimal("1.00")
    assert row.cost_output_per_1m == Decimal("4.00")
    assert row.cost_input == Decimal("1.000000")
    assert row.cost_output == Decimal("2.000000")
    assert row.cost_unit == "Eur"
    assert row.fx_rate == Decimal("0.5")


@pytest.mark.django_db
def test_save_assay_cost_falls_back_to_usd_when_no_rate_is_stored(
    assay_with_questions,
):
    """Dollars must not be presented as euros just because a rate is missing."""
    from toxtempass import model_metadata
    from toxtempass.azure_registry import ModelEntry
    from toxtempass.models import LLMCatalogue

    assay = assay_with_questions
    catalogue = LLMCatalogue.load()
    catalogue.models_json = {"azure/test-model": {"input_cost_per_token": 2e-06}}
    catalogue.save()
    model_metadata.invalidate()

    fake_model = ModelEntry(
        tag="TESTMODEL",
        deployment_name="test-deployment",
        model_id="test-model",
        tags={"tier": "global"},
    )
    with patch("toxtempass.azure_registry.get_model") as mock_get_model:
        mock_get_model.return_value = (SimpleNamespace(), fake_model)
        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:TESTMODEL",
            input_tokens=1_000_000,
            output_tokens=0,
        )

    row = AssayCost.objects.get(assay=assay, model_key="1:TESTMODEL")
    assert row.cost_unit == "Usd"
    assert row.cost_input_per_1m == Decimal("2.00")
    assert row.fx_rate is None


@pytest.mark.django_db
def test_save_assay_cost_no_pricing_when_tags_absent(assay_with_questions):
    """_save_assay_cost leaves cost fields None when no pricing tags are configured."""
    assay = assay_with_questions

    from toxtempass.azure_registry import ModelEntry

    fake_model = ModelEntry(
        tag="NOPRICE",
        deployment_name="no-price-deployment",
        model_id="no-price-model",
        tags={},  # no cost tags
    )

    with patch("toxtempass.azure_registry.get_model") as mock_get_model:
        mock_ep = SimpleNamespace(endpoint="https://test.example.com", api_key="key")
        mock_get_model.return_value = (mock_ep, fake_model)

        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:NOPRICE",
            input_tokens=500,
            output_tokens=200,
        )

    row = AssayCost.objects.get(assay=assay, model_key="1:NOPRICE")
    assert row.input_tokens == 500
    assert row.output_tokens == 200
    assert row.cost_input_per_1m is None
    assert row.cost_output_per_1m is None
    assert row.cost_input is None
    assert row.cost_output is None
    assert row.total_cost is None


@pytest.mark.django_db
def test_save_assay_cost_updates_existing_row(assay_with_questions):
    """Running _save_assay_cost twice for the same assay/model updates the existing row."""
    assay = assay_with_questions

    with patch("toxtempass.azure_registry.get_model") as mock_get_model:
        mock_get_model.return_value = None  # no registry entry → no pricing

        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:UPDATEME",
            input_tokens=100,
            output_tokens=50,
        )
        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:UPDATEME",
            input_tokens=300,
            output_tokens=150,
        )

    rows = AssayCost.objects.filter(assay=assay, model_key="1:UPDATEME")
    assert rows.count() == 1
    row = rows.first()
    assert row.input_tokens == 300
    assert row.output_tokens == 150
