"""A failed LLM call must not look like a successful run.

Regression tests for a production incident: a ~275k-token document context was
rejected with ``context_length_exceeded`` on all 76 questions, but
``generate_answer`` returned an empty string for each failure. Every answer was
therefore overwritten with "", the assay was marked DONE, django-q reported
success, and nothing reached the user.
"""

import threading
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from toxtempass.models import (
    Answer,
    LLMStatus,
    Question,
    QuestionSet,
    Section,
    Subsection,
)
from toxtempass.tests.fixtures.factories import AssayFactory
from toxtempass.views import AnswerGenerationError, generate_answer, process_llm_async


class _InlineExecutor:
    """Executor stub that runs submitted work on the calling thread.

    The real pool writes answers from worker threads, which SQLite refuses
    while the test transaction holds the table ("database table is locked").
    That is an artefact of the test backend, not of the behaviour under test,
    so the work is run inline and the failure bookkeeping is exercised for
    real.
    """

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirrors pool semantics
            future.set_exception(exc)
        return future


@pytest.fixture(autouse=True)
def _inline_pool():
    """Run answer generation inline; see :class:`_InlineExecutor`."""
    with patch("toxtempass.views.ThreadPoolExecutor", _InlineExecutor):
        yield


class _FailingFakeLLM:
    """Fake LLM whose every call fails with the given message."""

    def __init__(self, message="boom"):
        self._lock = threading.Lock()
        self._calls = 0
        self._message = message

    def invoke(self, messages):
        with self._lock:
            self._calls += 1
        raise RuntimeError(self._message)


class _WorkingFakeLLM:
    """Fake LLM that always answers."""

    def invoke(self, messages):
        return SimpleNamespace(content="Generated answer.")


def _seed_one_question(assay, answer_text=""):
    """Give ``assay`` exactly one question and answer row.

    One question on purpose: SQLite locks the answer table when several pool
    threads write at once, which is unrelated to what these tests check.
    """
    question_set = QuestionSet.objects.create(
        display_name="qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=question_set, title="S1")
    subsection = Subsection.objects.create(section=section, title="Sub1")
    question = Question.objects.create(
        subsection=subsection, question_text="What is this?"
    )
    return Answer.objects.create(
        assay=assay, question=question, answer_text=answer_text
    )


@pytest.mark.django_db
def test_generate_answer_raises_rather_than_returning_an_empty_answer():
    """Returning "" would be written over the user's text as if it were a draft."""
    assay = AssayFactory()
    answer = _seed_one_question(assay)

    with pytest.raises(AnswerGenerationError) as excinfo:
        generate_answer(answer, "context", assay, _FailingFakeLLM("upstream exploded"))

    assert excinfo.value.answer_id == answer.id
    assert "upstream exploded" in excinfo.value.reason


@pytest.mark.django_db
def test_failed_run_keeps_the_existing_answer_and_reports_the_failure():
    assay = AssayFactory()
    answer = _seed_one_question(assay, answer_text="Text the user wrote earlier.")

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=_FailingFakeLLM("upstream exploded"),
    )

    answer.refresh_from_db()
    assay.refresh_from_db()

    # The user's own work survives a failed draft.
    assert answer.answer_text == "Text the user wrote earlier."
    # Every answer failed, so this is an error, not a clean DONE.
    assert assay.status == LLMStatus.ERROR
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "could not be answered" in alert_text
    # Raw error text belongs in the internal log, never in the banner.
    assert "upstream exploded" in (assay.processing_log or "")
    assert "upstream exploded" not in alert_text


@pytest.mark.django_db
def test_oversized_context_tells_the_user_the_documents_are_too_large():
    assay = AssayFactory()
    _seed_one_question(assay)

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=_FailingFakeLLM("Error code: 400 'code': 'context_length_exceeded'"),
    )

    assay.refresh_from_db()
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "too large for the selected" in alert_text


@pytest.mark.django_db
def test_successful_run_still_reports_done_with_no_alert():
    """The failure reporting must not fire on a clean run."""
    assay = AssayFactory()
    answer = _seed_one_question(assay)

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=_WorkingFakeLLM(),
    )

    answer.refresh_from_db()
    assay.refresh_from_db()

    assert answer.answer_text == "Generated answer."
    assert assay.status == LLMStatus.DONE
    alert_text = " ".join(a.get("message", "") for a in (assay.user_alerts or []))
    assert "could not be answered" not in alert_text.lower()


@pytest.mark.django_db
def test_context_budget_is_scaled_down_to_absorb_estimator_error():
    """tiktoken undercounts the model's real tokenizer, and a fixed headroom is
    too small a fraction of a large window to absorb that error; the reserve
    has to shrink the budget.
    """
    assay = AssayFactory()
    _seed_one_question(assay)
    doc_dict = {
        "doc.txt": {
            "text": "word " * 200,
            "source_document": "doc.txt",
            "origin": "document",
        }
    }

    # Before the reserve the budget is 1_000_000, which this text fits inside.
    # A reserve of 0.0001 cuts it to 100 tokens, so the same text truncates.
    with (
        patch("toxtempass.views.config.context_window_headroom_tokens", new=0),
        patch("toxtempass.views.config.context_window_fallback_tokens", new=1_000_000),
        patch("toxtempass.views.config.context_window_estimate_reserve", new=0.0001),
    ):
        process_llm_async(
            assay.id,
            doc_dict=doc_dict,
            extract_images=False,
            chatopenai=_WorkingFakeLLM(),
        )

    assay.refresh_from_db()
    alert_text = " ".join(a.get("message", "") for a in assay.user_alerts).lower()
    assert "truncated" in alert_text
