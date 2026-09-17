"""What the records must preserve so the drafting can be measured afterwards.

Two properties, both learned the hard way (evaluation/gold_standard/README.md):

* resubmitting an answer nobody touched must not look like an edit — browsers send
  textarea newlines as CRLF, and a byte comparison against an LF draft made every
  multi-line answer appear rewritten on its first review;
* what the model decided must be recorded when it decides it, because the wording is
  model-specific and a scientist's edit replaces the text.
"""

from types import SimpleNamespace

import pytest
from django.test import Client, TestCase
from django.utils.datastructures import MultiValueDict

from toxtempass import config
from toxtempass.forms import AssayAnswerForm
from toxtempass.models import Answer, Question, QuestionSet, Section, Subsection
from toxtempass.tests.fixtures.factories import (
    AssayFactory,
    InvestigationFactory,
    PersonFactory,
    StudyFactory,
)
from toxtempass.utilities import is_standard_abstention
from toxtempass.views import process_llm_async


def test_paraphrased_abstention_still_counts():
    """A model that does not copy the sentence verbatim has still abstained."""
    assert is_standard_abstention(config.not_found_string)
    assert is_standard_abstention("answer not found in documents")
    assert is_standard_abstention("Answer not found in the documents.")
    # An answer that merely ends with the sentence is a real answer, not an abstention.
    long_answer = "The assay uses SH-SY5Y cells. " * 5 + config.not_found_string
    assert not is_standard_abstention(long_answer)
    assert not is_standard_abstention("Cells were seeded at 20,000 per well.")
    assert not is_standard_abstention("")


class ResubmitTests(TestCase):
    """The form must not record a version when nothing changed."""

    def setUp(self):
        self.client = Client()
        self.user = PersonFactory()
        self.assay = AssayFactory(
            study=StudyFactory(investigation=InvestigationFactory(owner=self.user))
        )
        qs = QuestionSet.objects.create(display_name="crlf-qs", created_by=self.user)
        subsection = Subsection.objects.create(
            section=Section.objects.create(question_set=qs, title="Sec"), title="Sub"
        )
        self.question = Question.objects.create(
            subsection=subsection, question_text="Q1?"
        )
        self.assay.question_set = qs
        self.assay.save()
        # A multi-line draft, as the LLM writes it: Unix line endings.
        self.answer = Answer.objects.create(
            assay=self.assay, question=self.question, answer_text="line one\nline two"
        )

    def _submit(self, text):
        form = AssayAnswerForm(
            data={f"question_{self.question.id}": text},
            files=MultiValueDict(),
            assay=self.assay,
            user=self.user,
        )
        assert form.is_valid(), form.errors
        form.save()

    def _recorded_texts(self):
        """Every distinct answer text the version history holds for this answer."""
        return set(self.answer.history.values_list("answer_text", flat=True))

    def test_resubmitting_the_draft_unchanged_records_no_other_text(self):
        # What a browser posts for an untouched textarea: the same text, CRLF endings.
        self._submit("line one\r\nline two")
        self.answer.refresh_from_db()
        assert self.answer.answer_text == "line one\nline two"
        # A version IS written (the accepted flag goes None -> False on first save),
        # but no version may carry a DIFFERENT text: that is what a later analysis
        # reads as "the scientist rewrote the draft".
        assert self._recorded_texts() == {"line one\nline two"}, (
            "an untouched answer recorded a changed text, so a later analysis cannot "
            "tell it apart from one the scientist rewrote"
        )

    def test_a_real_edit_is_recorded(self):
        self._submit("line one\r\nline two, corrected")
        self.answer.refresh_from_db()
        assert self.answer.answer_text == "line one\nline two, corrected"
        assert self._recorded_texts() == {
            "line one\nline two",
            "line one\nline two, corrected",
        }


class FakeChat:
    """Returns one fixed draft for every question."""

    def __init__(self, text: str):
        self.text = text

    def invoke(self, messages):
        return SimpleNamespace(content=self.text)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("draft", "abstained"),
    [("Answer not found in the documents.", True), ("Cells were seeded.", False)],
)
def test_drafting_records_what_the_model_decided(draft, abstained):
    assay = AssayFactory()
    qs = QuestionSet.objects.create(
        display_name=f"abstain-{abstained}", created_by=assay.study.investigation.owner
    )
    subsection = Subsection.objects.create(
        section=Section.objects.create(question_set=qs, title="S"), title="Sub"
    )
    question = Question.objects.create(subsection=subsection, question_text="Q?")
    answer = Answer.objects.create(assay=assay, question=question)

    process_llm_async(
        assay.id, doc_dict={}, chatopenai=FakeChat(draft), max_workers=1
    )

    answer.refresh_from_db()
    # Note the draft paraphrases the standardised sentence: the flag must not depend
    # on the exact wording, which differs between models.
    assert answer.llm_abstained is abstained
