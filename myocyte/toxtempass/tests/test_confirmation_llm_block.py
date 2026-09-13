"""Unconfirmed users can look around, but cannot start LLM drafts."""

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils.datastructures import MultiValueDict

from toxtempass import Config, utilities
from toxtempass.forms import AssayAnswerForm
from toxtempass.models import Answer, Question, QuestionSet, Section, Subsection
from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    StudyFactory,
)

pytestmark = pytest.mark.django_db


def _assay_with_question(user):
    investigation = InvestigationFactory(owner=user)
    assay = AssayFactory(study=StudyFactory(investigation=investigation))
    question_set = QuestionSet.objects.create(display_name="block-qs", created_by=user)
    section = Section.objects.create(question_set=question_set, title="Section")
    subsection = Subsection.objects.create(section=section, title="Subsection")
    question = Question.objects.create(subsection=subsection, question_text="Q1?")
    Answer.objects.create(assay=assay, question=question)
    assay.question_set = question_set
    assay.save()
    return assay, question


@pytest.mark.parametrize("confirmed", [False, True])
def test_answer_form_queues_llm_updates_only_for_confirmed_users(confirmed):
    user = PersonFactory() if confirmed else PersonFactory(email_confirmed_at=None)
    assay, question = _assay_with_question(user)
    document = {
        "a.txt": {"text": "alpha", "source_document": "a.txt", "origin": "document"}
    }
    upload = SimpleUploadedFile("a.txt", b"alpha", content_type="text/plain")

    with (
        patch(
            "toxtempass.forms.get_text_or_imagebytes_from_django_uploaded_file",
            return_value=(document, []),
        ),
        patch("toxtempass.forms.async_task") as mock_async,
    ):
        form = AssayAnswerForm(
            data={f"earmarked_{question.id}": True},
            files=MultiValueDict({"file_upload": [upload]}),
            assay=assay,
            user=user,
        )
        assert form.is_valid(), form.errors
        queued = form.save()

    assert queued is confirmed
    assert mock_async.called is confirmed
    if confirmed:
        assert mock_async.call_args.kwargs["user_id"] == user.pk
    else:
        assert form.errors["file_upload"] == [Config._email_confirmation_required_message]


def test_new_draft_with_files_is_refused_for_unconfirmed_users(client):
    user = PersonFactory(email_confirmed_at=None)
    utilities.update_prefs_atomic(
        user, lambda prefs: prefs.update(beta_admitted=True) or True
    )
    assay, _question = _assay_with_question(user)
    client.force_login(user)

    with patch("toxtempass.views.async_task") as mock_async:
        response = client.post(
            reverse("add_new"),
            {
                "question_set": assay.question_set.pk,
                "investigation": assay.study.investigation.pk,
                "study": assay.study.pk,
                "assay": assay.pk,
                "overwrite": "on",
                "files": SimpleUploadedFile(
                    "protocol.txt", b"protocol text", content_type="text/plain"
                ),
            },
        )

    assert response.status_code == 403, response.content
    assert response.json()["errors"]["__all__"] == [
        Config._email_confirmation_required_message
    ]
    mock_async.assert_not_called()
