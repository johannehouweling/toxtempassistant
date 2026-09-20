"""Tests for context-window guard in process_llm_async and the
token-estimation / truncation utilities in filehandling.
"""

import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from toxtempass import model_metadata
from toxtempass.filehandling import (
    estimate_token_count,
    truncate_context_to_token_limit,
)
from toxtempass.models import (
    Answer,
    Question,
    QuestionSet,
    Section,
    Subsection,
)
from toxtempass.tests.fixtures.factories import AssayFactory
from toxtempass.views import process_llm_async

# ---------------------------------------------------------------------------
# Unit tests for the utility functions
# ---------------------------------------------------------------------------


def test_estimate_token_count_empty():
    assert estimate_token_count("") == 0


def test_estimate_token_count_short():
    count = estimate_token_count("Hello world")
    assert count > 0


def test_truncate_context_no_truncation_needed():
    text = "short text"
    result, was_truncated = truncate_context_to_token_limit(text, max_tokens=10_000)
    assert result == text
    assert was_truncated is False


def test_truncate_context_truncates_long_text():
    # Build a text that is definitely over 10 tokens
    long_text = "word " * 1000  # ~1000+ tokens
    result, was_truncated = truncate_context_to_token_limit(long_text, max_tokens=50)
    assert was_truncated is True
    assert len(result) < len(long_text)
    assert "truncated" in result.lower()


def test_truncate_context_empty_string():
    result, was_truncated = truncate_context_to_token_limit("", max_tokens=100)
    assert result == ""
    assert was_truncated is False


def test_truncate_context_result_within_limit():
    long_text = "token " * 5000  # well over any small limit
    result, was_truncated = truncate_context_to_token_limit(long_text, max_tokens=200)
    assert was_truncated is True
    actual_tokens = estimate_token_count(result)
    # Result (body + marker) must fit strictly under the configured limit.
    assert actual_tokens <= 200


def test_truncate_context_zero_max_tokens():
    result, was_truncated = truncate_context_to_token_limit("hello", max_tokens=0)
    assert result == ""
    assert was_truncated is True


def test_truncate_context_negative_max_tokens():
    result, was_truncated = truncate_context_to_token_limit("hello", max_tokens=-5)
    assert result == ""
    assert was_truncated is True


def test_truncate_context_marker_does_not_fit():
    """When the marker alone exceeds the budget, return empty."""
    long_text = "token " * 1000
    # The marker is roughly 20-30 tokens; pick a budget below it.
    result, was_truncated = truncate_context_to_token_limit(long_text, max_tokens=5)
    assert was_truncated is True
    actual_tokens = estimate_token_count(result)
    assert actual_tokens <= 5


# ---------------------------------------------------------------------------
# Integration test: process_llm_async adds warning when context is truncated
# ---------------------------------------------------------------------------


class _SimpleFakeLLM:
    """Minimal fake LLM that always returns a fixed answer."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = 0

    def invoke(self, messages):
        with self._lock:
            self._calls += 1
        return SimpleNamespace(content="Generated answer.")


@pytest.mark.django_db
def test_process_llm_async_warns_when_context_truncated():
    """process_llm_async should append a user-visible truncation alert to
    user_alerts when the document context exceeds the available token budget.
    The budget is derived from the model's context_window minus headroom, or
    fallback - headroom when no context_window tag is set.
    """
    assay = AssayFactory()
    qs = QuestionSet.objects.create(
        display_name="qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=qs, title="S1")
    subsection = Subsection.objects.create(section=section, title="Sub1")
    q = Question.objects.create(subsection=subsection, question_text="What is this?")
    Answer.objects.create(assay=assay, question=q)

    # A doc_dict whose text content greatly exceeds a 10-token limit
    large_text = "This is some document content. " * 500  # ~several thousand tokens
    doc_dict = {
        "bigfile.txt": {
            "text": large_text,
            "source_document": "bigfile.txt",
            "origin": "document",
        }
    }

    fake_llm = _SimpleFakeLLM()

    # Drive the fallback budget very low (headroom=10, fallback=20 → budget=10)
    with (
        patch("toxtempass.views.config.context_window_headroom_tokens", new=10),
        patch("toxtempass.views.config.context_window_fallback_tokens", new=20),
    ):
        process_llm_async(
            assay.id,
            doc_dict=doc_dict,
            extract_images=False,
            chatopenai=fake_llm,
        )

    assay.refresh_from_db()

    assert assay.user_alerts, "user_alerts should not be empty after truncation"
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "truncated" in alert_text, (
        "user_alerts should mention truncation; got: " + repr(assay.user_alerts)
    )


@pytest.mark.django_db
def test_process_llm_async_no_warning_when_context_fits():
    """process_llm_async should NOT add a user alert when the context fits
    within the available token budget.
    """
    assay = AssayFactory()
    qs = QuestionSet.objects.create(
        display_name="qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=qs, title="S1")
    subsection = Subsection.objects.create(section=section, title="Sub1")
    q = Question.objects.create(subsection=subsection, question_text="What is this?")
    Answer.objects.create(assay=assay, question=q)

    doc_dict = {
        "small.txt": {
            "text": "Short text.",
            "source_document": "small.txt",
            "origin": "document",
        }
    }

    fake_llm = _SimpleFakeLLM()

    # Use a generous fallback so short text is never truncated
    with (
        patch("toxtempass.views.config.context_window_headroom_tokens", new=1_000),
        patch("toxtempass.views.config.context_window_fallback_tokens", new=1_000_000),
    ):
        process_llm_async(
            assay.id,
            doc_dict=doc_dict,
            extract_images=False,
            chatopenai=fake_llm,
        )

    assay.refresh_from_db()

    # user_alerts should be empty (no truncation notice)
    alerts = assay.user_alerts or []
    alert_text = " ".join(a.get("message", "") for a in alerts).lower()
    assert "truncated" not in alert_text, (
        "Unexpected truncation alert in user_alerts: " + repr(alerts)
    )


def _entry(model_id="tiny-model", tier="global", residency="eu"):
    """A ModelEntry stand-in; only model_id and tags are read for the budget."""
    from unittest.mock import MagicMock

    entry = MagicMock()
    entry.model_id = model_id
    entry.tags = {"tier": tier, "residency": residency}
    return MagicMock(), entry


def _seed_catalogue(models):
    from toxtempass.models import LLMCatalogue

    catalogue = LLMCatalogue.load()
    catalogue.models_json = models
    catalogue.save()
    model_metadata.invalidate()


def _one_question(assay):
    qs = QuestionSet.objects.create(
        display_name="qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=qs, title="S1")
    subsection = Subsection.objects.create(section=section, title="Sub1")
    q = Question.objects.create(subsection=subsection, question_text="What is this?")
    Answer.objects.create(assay=assay, question=q)


@pytest.mark.django_db
def test_budget_comes_from_the_catalogue_input_ceiling_not_a_tag():
    """The ceiling is the API's own max_input_tokens.

    A hand-maintained ``context-window`` tag naming a model's *total* window is
    what let a 275k-token request reach an endpoint accepting 272k.
    """
    assay = AssayFactory()
    _one_question(assay)
    _seed_catalogue({"azure/tiny-model": {"max_input_tokens": 150}})
    doc_dict = {
        "doc.txt": {
            "text": "word " * 500,
            "source_document": "doc.txt",
            "origin": "document",
        }
    }

    with (
        patch("toxtempass.views.config.context_window_headroom_tokens", new=50),
        patch("toxtempass.views.config.context_window_estimate_reserve", new=1.0),
        patch("toxtempass.views.get_azure_model", return_value=_entry()),
    ):
        process_llm_async(
            assay.id,
            doc_dict=doc_dict,
            extract_images=False,
            chatopenai=_SimpleFakeLLM(),
            llm_model="1:FAKE",
        )

    assay.refresh_from_db()
    # Budget was 150 - 50 = 100 tokens; the text is ~500 → truncated.
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "truncated" in alert_text


@pytest.mark.django_db
def test_a_named_model_with_no_known_ceiling_refuses_instead_of_guessing():
    """An unknown limit is not evidence that a large one is safe."""
    from toxtempass.models import LLMStatus

    assay = AssayFactory()
    _one_question(assay)
    _seed_catalogue({"azure/some-other-model": {"max_input_tokens": 272000}})
    fake = _SimpleFakeLLM()

    with patch("toxtempass.views.get_azure_model", return_value=_entry()):
        process_llm_async(
            assay.id,
            doc_dict={},
            extract_images=False,
            chatopenai=fake,
            llm_model="1:FAKE",
        )

    assay.refresh_from_db()
    assert assay.status == LLMStatus.ERROR
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "input limit could not be determined" in alert_text
    # Nothing was sent: refusing means refusing, not truncating to zero.
    assert fake._calls == 0


@pytest.mark.django_db
def test_run_aborts_when_headroom_exceeds_the_models_whole_ceiling():
    """Misconfigured headroom yields a non-positive budget; abort, don't send."""
    from toxtempass.models import LLMStatus

    assay = AssayFactory()
    _one_question(assay)
    _seed_catalogue({"azure/tiny-model": {"max_input_tokens": 100}})
    fake = _SimpleFakeLLM()

    with (
        patch("toxtempass.views.config.context_window_headroom_tokens", new=200),
        patch("toxtempass.views.config.context_window_estimate_reserve", new=1.0),
        patch("toxtempass.views.get_azure_model", return_value=_entry()),
    ):
        process_llm_async(
            assay.id,
            doc_dict={
                "doc.txt": {
                    "text": "word " * 100,
                    "source_document": "doc.txt",
                    "origin": "document",
                }
            },
            extract_images=False,
            chatopenai=fake,
            llm_model="1:TINY",
        )

    assay.refresh_from_db()
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "context window" in alert_text and "too small" in alert_text
    assert "non-positive" in (assay.processing_log or "").lower()
    assert assay.status == LLMStatus.ERROR
    assert fake._calls == 0
