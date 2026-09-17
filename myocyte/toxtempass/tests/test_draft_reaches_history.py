"""The LLM draft must land in the answer's version history.

A queryset ``.update()`` writes no history row, which is what made every draft between
2025-09-13 and this change unrecoverable: once a scientist edited an answer, what the
model had written was gone. The drafting loop saves through the model instead, so the
draft is recorded — see ``evaluation/gold_standard/README.md``.
"""

from types import SimpleNamespace

import pytest

from toxtempass.models import Answer, Question, QuestionSet, Section, Subsection
from toxtempass.tests.fixtures.factories import AssayFactory
from toxtempass.views import process_llm_async


class FakeChat:
    """Returns one fixed draft for every question."""

    def __init__(self, text: str):
        self.text = text

    def invoke(self, messages):
        return SimpleNamespace(content=self.text)


@pytest.mark.django_db(transaction=True)
def test_draft_is_recorded_in_history():
    assay = AssayFactory()
    qs = QuestionSet.objects.create(
        display_name="draft-history", created_by=assay.study.investigation.owner
    )
    subsection = Subsection.objects.create(
        section=Section.objects.create(question_set=qs, title="S"), title="Sub"
    )
    question = Question.objects.create(subsection=subsection, question_text="Q?")
    answer = Answer.objects.create(assay=assay, question=question)

    process_llm_async(
        assay.id,
        doc_dict={},
        extract_images=False,
        chatopenai=FakeChat("Answer not found in documents."),
        max_workers=1,
    )

    answer.refresh_from_db()
    assert answer.answer_text == "Answer not found in documents."

    # The draft row: written by the worker, so no history user, and carrying the
    # drafting run's document list — the two fields the recovery rule keys on.
    drafts = [
        h
        for h in answer.history.all()
        if h.answer_documents is not None and h.history_user_id is None
    ]
    assert drafts, "the LLM draft left no history row — it is unrecoverable once edited"
    assert drafts[-1].answer_text == "Answer not found in documents."
