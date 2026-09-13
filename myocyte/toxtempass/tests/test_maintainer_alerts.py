"""Tests for the LLM run log and the maintainers' cost and failure alerts."""

import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.utils import timezone
from django_q.models import Task

from toxtempass import notifications
from toxtempass.models import (
    Answer,
    AssayCost,
    EmailLog,
    LLMRun,
    LLMStatus,
    Question,
    QuestionSet,
    Section,
    Subsection,
)
from toxtempass.tests.fixtures.factories import AssayFactory, PersonFactory
from toxtempass.views import _save_assay_cost, process_llm_async

pytestmark = pytest.mark.django_db

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def _email_settings(settings):
    settings.SITE_URL = "https://toxtemp.example"
    settings.ADMINS = ["maintainer@example.com"]


def _tomorrow_noon():
    day = timezone.now().astimezone(AMSTERDAM).date() + timedelta(days=1)
    return datetime.combine(day, time(12, 0), tzinfo=AMSTERDAM)


def _run(cost, created_at, unit="Eur", status=LLMRun.Status.DONE, **kwargs):
    run = LLMRun.objects.create(
        status=status,
        cost=Decimal(cost) if cost is not None else None,
        cost_unit=unit,
        model_key="1:GPT4O",
        model_id="gpt-4o",
        **kwargs,
    )
    LLMRun.objects.filter(pk=run.pk).update(created_at=created_at)
    return run


def _emails_about(text):
    return [message for message in mail.outbox if text in message.subject]


@pytest.fixture
def run_jobs(django_capture_on_commit_callbacks):
    """Run the email jobs; their emails go out when the transaction commits."""

    def run(now):
        with django_capture_on_commit_callbacks(execute=True):
            notifications.run_email_jobs(now=now)

    return run


def test_cost_alert_is_sent_once_when_the_day_passes_the_limit(run_jobs):
    noon = _tomorrow_noon()
    heavy_user = PersonFactory(email="heavy.user@example.org")
    _run("6.00", noon - timedelta(hours=2), user=heavy_user)
    _run("3.00", noon - timedelta(hours=1), user=heavy_user)

    run_jobs(noon)
    assert _emails_about("LLM spend") == []

    _run("1.50", noon - timedelta(minutes=5))
    run_jobs(noon)
    alerts = _emails_about("LLM spend")
    assert len(alerts) == 1
    assert alerts[0].to == ["maintainer@example.com"]
    assert "EUR 10.50" in alerts[0].body
    assert "heavy.user@example.org: EUR 9.00 (2 runs)" in alerts[0].body

    _run("5.00", noon + timedelta(minutes=5))
    run_jobs(noon + timedelta(hours=1))
    assert len(_emails_about("LLM spend")) == 1


def test_cost_alert_ignores_other_days_currencies_and_unpriced_runs(run_jobs):
    noon = _tomorrow_noon()
    _run("20.00", noon - timedelta(days=1))
    _run("20.00", noon - timedelta(hours=1), unit="USD")
    _run(None, noon - timedelta(hours=1))

    run_jobs(noon)

    assert _emails_about("LLM spend") == []


def test_failure_alert_batches_failures_at_most_hourly(run_jobs):
    noon = _tomorrow_noon()
    user = PersonFactory()
    failed_email = EmailLog.objects.create(
        kind=notifications.PASSWORD_CHANGED,
        user=user,
        recipient=user.email,
        status=EmailLog.Status.FAILED,
        error="550 mailbox unavailable",
    )
    EmailLog.objects.filter(pk=failed_email.pk).update(
        updated_at=noon - timedelta(minutes=20)
    )
    _run(
        None,
        noon - timedelta(minutes=10),
        status=LLMRun.Status.ERROR,
        error="RateLimitError: slow down",
    )
    Task.objects.create(
        id=uuid.uuid4().hex,
        name="ror-lookup",
        func="toxtempass.ror.resolve_person",
        started=noon - timedelta(minutes=11),
        stopped=noon - timedelta(minutes=10),
        success=False,
        result="ConnectionError: ror.org unreachable",
    )

    run_jobs(noon)

    alerts = _emails_about("background failure")
    assert len(alerts) == 1
    assert alerts[0].subject.startswith("[ToxTempAssistant] 3 background failures since")
    for detail in ("550 mailbox unavailable", "slow down", "ror.org unreachable"):
        assert detail in alerts[0].body

    _run(None, noon + timedelta(minutes=10), status=LLMRun.Status.ERROR, error="later")
    run_jobs(noon + timedelta(minutes=30))
    assert len(_emails_about("background failure")) == 1

    run_jobs(noon + timedelta(minutes=61))
    alerts = _emails_about("background failure")
    assert len(alerts) == 2
    assert alerts[1].subject.startswith("[ToxTempAssistant] 1 background failure since")


def test_no_failure_alert_without_failures(run_jobs):
    run_jobs(_tomorrow_noon())
    assert mail.outbox == []


class _FakeLLM:
    def invoke(self, messages):
        return SimpleNamespace(
            content="An answer",
            usage_metadata={
                "input_tokens": 200,
                "output_tokens": 80,
                "total_tokens": 280,
            },
        )


@pytest.fixture
def assay_with_questions():
    assay = AssayFactory()
    question_set = QuestionSet.objects.create(
        display_name="run-log-qs", created_by=assay.study.investigation.owner
    )
    section = Section.objects.create(question_set=question_set, title="Section")
    subsection = Subsection.objects.create(section=section, title="Subsection")
    for text in ("Q1?", "Q2?"):
        question = Question.objects.create(subsection=subsection, question_text=text)
        Answer.objects.create(assay=assay, question=question)
    return assay


def test_every_run_is_logged_although_the_assay_cost_is_overwritten(
    assay_with_questions,
):
    user = PersonFactory()
    for input_tokens in (400, 300):
        _save_assay_cost(
            assay_with_questions.id, "1:GPT4O", input_tokens, 100, user_id=user.pk
        )

    assert AssayCost.objects.get(assay=assay_with_questions).input_tokens == 300
    runs = LLMRun.objects.filter(
        assay=assay_with_questions, user=user, status=LLMRun.Status.DONE
    )
    assert sorted(runs.values_list("input_tokens", flat=True)) == [300, 400]


def test_failed_run_is_logged_as_an_error(assay_with_questions):
    with patch(
        "toxtempass.views.collect_source_documents",
        side_effect=RuntimeError("context exploded"),
    ):
        process_llm_async(
            assay_with_questions.id,
            doc_dict={},
            extract_images=False,
            chatopenai=_FakeLLM(),
            llm_model="1:GPT4O",
        )

    run = LLMRun.objects.get()
    assert run.status == LLMRun.Status.ERROR
    assert run.assay_id == assay_with_questions.id
    assert "context exploded" in run.error
    assay_with_questions.refresh_from_db()
    assert assay_with_questions.status == LLMStatus.ERROR
