"""The raw dump feeding the uptake/quality table.

It must carry what the gold CSV cannot: every answer (not only accepted ones), every
assay (not only reviewed ones), and every saved version with the three fields the draft
recovery keys on — whether the row already carried a drafting run's document list, who
saved it, and whether it was accepted at that moment.
"""

import csv

import pytest

from toxtempass.evaluation.gold_standard import audit
from toxtempass.evaluation.gold_standard.audit import NOT_FOUND
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    QuestionFactory,
    SubsectionFactory,
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.django_db
def test_dump_covers_unaccepted_answers_and_unreviewed_assays(tmp_path):
    subsection = SubsectionFactory.create(section__question_set__label="rawdump")
    reviewed = AssayFactory.create()
    # Nobody accepted anything here: the gold extract drops this assay, which is exactly
    # how "31 assays created" came to under-count. The raw dump must keep it.
    untouched = AssayFactory.create()

    def answer(assay, **kwargs):
        return AnswerFactory.create(
            assay=assay,
            question=QuestionFactory.create(subsection=subsection),
            **kwargs,
        )

    accepted = answer(
        reviewed, accepted=True, answer_text="gold", answer_documents=["a.pdf"]
    )
    abstained = answer(
        reviewed, accepted=None, answer_text=NOT_FOUND, answer_documents=["a.pdf"]
    )
    # Never drafted: the answer form leaves answer_documents NULL.
    answer(untouched, accepted=None, answer_text="", answer_documents=None)

    summary = audit.dump_raw({"out": str(tmp_path / "raw")})

    assert summary["n_assays"] == 2, "an assay with no accepted answers is still uptake"
    assert summary["n_answers"] == 3
    assert summary["n_accepted"] == 1
    assert summary["n_drafted"] == 2

    rows = {int(r["answer_id"]): r for r in _read(summary["paths"]["answers"])}
    assert rows[accepted.id]["accepted"] == "True"
    assert rows[accepted.id]["drafted"] == "True"
    # Tri-state kept: "not accepted" and "never looked at" are different states.
    assert rows[abstained.id]["accepted"] == ""
    assert rows[abstained.id]["is_sentinel"] == "True"
    assert rows[accepted.id]["is_sentinel"] == "False"


@pytest.mark.django_db
def test_history_rows_carry_the_draft_recovery_fields(tmp_path):
    subsection = SubsectionFactory.create(section__question_set__label="rawhist")
    assay = AssayFactory.create()
    answer = AnswerFactory.create(
        assay=assay,
        question=QuestionFactory.create(subsection=subsection),
        accepted=None,
        answer_text="",
        answer_documents=None,
    )
    # A drafting run in the era that still saved through the model (so it reaches
    # simple-history), then a scientist accepting it without touching the text.
    answer.answer_text = NOT_FOUND
    answer.answer_documents = ["a.pdf"]
    answer.save()
    answer.accepted = True
    answer.save()

    summary = audit.dump_raw({"out": str(tmp_path / "raw")})
    history = [
        r for r in _read(summary["paths"]["history"]) if int(r["answer_id"]) == answer.id
    ]

    # Oldest first — an unordered query returns these newest-first, which is what made
    # the gold path read the last save of a day as if it were the first.
    assert [r["documents_set"] for r in history] == ["False", "True", "True"]
    assert summary["n_post_draft_saves"] == 2
    # The first row carrying a document list is the draft itself here, and it is the
    # exact sentinel — this is the count "originally not found" is built from.
    draft = next(r for r in history if r["documents_set"] == "True")
    assert draft["is_sentinel"] == "True"
    assert draft["answer_documents"] == "a.pdf"
    assert draft["accepted"] == ""
