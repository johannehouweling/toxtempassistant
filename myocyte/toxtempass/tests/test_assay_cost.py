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
def test_save_assay_cost_calculates_cost_from_registry(assay_with_questions):
    """_save_assay_cost calculates cost correctly when pricing tags are in registry."""
    assay = assay_with_questions

    # Patch the registry to return a model with known pricing tags
    from toxtempass.azure_registry import ModelEntry

    fake_model = ModelEntry(
        tag="TESTMODEL",
        deployment_name="test-deployment",
        model_id="test-model",
        tags={
            "cost-input-1mtoken": "2.00",
            "cost-output-1mtoken": "8.00",
            "cost-unit": "Eur",
        },
    )

    with patch("toxtempass.azure_registry.get_model") as mock_get_model:
        # Return a fake (endpoint, model) tuple
        mock_ep = SimpleNamespace(endpoint="https://test.example.com", api_key="key")
        mock_get_model.return_value = (mock_ep, fake_model)

        _save_assay_cost(
            assay_id=assay.id,
            model_key="1:TESTMODEL",
            input_tokens=1_000_000,  # 1M tokens → $2.00
            output_tokens=500_000,   # 0.5M tokens → $4.00
        )

    row = AssayCost.objects.get(assay=assay, model_key="1:TESTMODEL")
    assert row.input_tokens == 1_000_000
    assert row.output_tokens == 500_000
    assert row.cost_input_per_1m == Decimal("2.00")
    assert row.cost_output_per_1m == Decimal("8.00")
    assert row.cost_input == Decimal("2.000000")
    assert row.cost_output == Decimal("4.000000")
    assert row.total_cost == Decimal("6.000000")
    assert row.cost_unit == "Eur"
    assert row.cost_unit_symbol == "€"


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


def test_model_entry_cost_properties_parse_tags():
    """ModelEntry.cost_input_per_1m_tokens and cost_output_per_1m_tokens parse tags."""
    from toxtempass.azure_registry import ModelEntry

    m = ModelEntry(
        tag="T1",
        deployment_name="dep",
        model_id="gpt-4o",
        tags={"cost-input-1mtoken": "2.50", "cost-output-1mtoken": "10.00"},
    )
    assert m.cost_input_per_1m_tokens == 2.50
    assert m.cost_output_per_1m_tokens == 10.00


def test_model_entry_cost_properties_return_none_when_absent():
    """ModelEntry returns None when cost tags are absent."""
    from toxtempass.azure_registry import ModelEntry

    m = ModelEntry(tag="T2", deployment_name="dep", model_id="gpt-4o", tags={})
    assert m.cost_input_per_1m_tokens is None
    assert m.cost_output_per_1m_tokens is None


def test_model_entry_cost_properties_return_none_on_invalid():
    """ModelEntry returns None when cost tags contain non-numeric values."""
    from toxtempass.azure_registry import ModelEntry

    m = ModelEntry(
        tag="T3",
        deployment_name="dep",
        model_id="gpt-4o",
        tags={"cost-input-1mtoken": "notanumber", "cost-output-1mtoken": "also-bad"},
    )
    assert m.cost_input_per_1m_tokens is None
    assert m.cost_output_per_1m_tokens is None


def test_model_entry_cost_unit_property():
    """ModelEntry.cost_unit reads the cost-unit tag."""
    from toxtempass.azure_registry import ModelEntry

    m_with = ModelEntry(
        tag="T4", deployment_name="dep", model_id="gpt-4o",
        tags={"cost-unit": "Eur"},
    )
    assert m_with.cost_unit == "Eur"

    m_without = ModelEntry(tag="T5", deployment_name="dep", model_id="gpt-4o", tags={})
    assert m_without.cost_unit == ""


def test_assay_cost_cost_unit_symbol():
    """AssayCost.cost_unit_symbol maps known units to symbols and falls back gracefully."""
    from toxtempass.models import AssayCost

    for unit, expected_sym in [("Eur", "€"), ("EUR", "€"), ("USD", "$"), ("GBP", "£")]:
        obj = AssayCost.__new__(AssayCost)
        obj.cost_unit = unit
        assert obj.cost_unit_symbol == expected_sym, f"unit={unit!r}"

    # Unknown unit — returns the raw unit string
    obj2 = AssayCost.__new__(AssayCost)
    obj2.cost_unit = "JPY"
    assert obj2.cost_unit_symbol == "JPY"

    # Empty unit — falls back to €
    obj3 = AssayCost.__new__(AssayCost)
    obj3.cost_unit = ""
    assert obj3.cost_unit_symbol == "€"
