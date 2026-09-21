"""The per-assay counts the gold extract adds: LLM drafts + context documents.

`n_drafted_answers` counts rows the LLM wrote, detected by `answer_documents` being
non-NULL — `process_llm_async` is its only writer, and it survives the 2025-09-13 switch
to queryset `.update()` that stopped the drafts reaching simple-history.
`n_context_documents` is the union of cited filenames over ALL the assay's answers, not
only the accepted ones.
"""

from unittest.mock import patch

import pytest

from toxtempass.evaluation.gold_standard import audit
from toxtempass.evaluation.gold_standard.audit import NOT_FOUND
from toxtempass.tests.fixtures.factories import (
    AnswerFactory,
    AssayFactory,
    QuestionFactory,
    SubsectionFactory,
)


@pytest.fixture(autouse=True)
def _ignore_production_exclusions():
    """Neutralise EXCLUDED_ASSAY_IDS, which names rows in the production database.

    The audit drops a hardcoded set of real scratch assays. Test databases number
    their rows from 1, and Postgres does not roll sequences back between tests, so
    once enough assays have been created earlier in a run one of this module's
    assays lands on an excluded id and is silently dropped -- turning an assertion
    about this test's own data into an assertion about how many assays every
    preceding test happened to create. Adding any test anywhere that creates an
    Assay can move it.
    """
    with patch.object(audit, "EXCLUDED_ASSAY_IDS", frozenset()):
        yield


@pytest.mark.django_db
def test_draft_and_document_counts_cover_unaccepted_answers():
    assay = AssayFactory.create()
    subsection = SubsectionFactory.create(section__question_set__label="gsaudit")

    def answer(**kwargs):
        return AnswerFactory.create(
            assay=assay,
            question=QuestionFactory.create(subsection=subsection),
            **kwargs,
        )

    # Accepted, drafted by the LLM from two documents.
    answer(accepted=True, answer_text="gold", answer_documents=["a.pdf", "b.pdf"])
    # Drafted but never accepted: still an LLM draft, and "c.pdf" is still context the
    # drafting run saw — the accepted-only view would miss both.
    answer(accepted=False, answer_text="draft", answer_documents=["b.pdf", "c.pdf"])
    # Drafted with no readable document: collect_source_documents returns [], so the
    # column is an empty list, not NULL — the LLM still ran.
    answer(accepted=False, answer_text="draft", answer_documents=[])
    # Human-typed, never drafted: answer_documents stays NULL.
    answer(accepted=True, answer_text="typed by hand", answer_documents=None)

    records = audit._collect({"min_accepted": 1})
    rows = [r for r in records if r["assay_id"] == assay.id]

    assert len(rows) == 2, "only accepted answers become gold rows"
    assert {r["n_drafted_answers"] for r in rows} == {3}
    assert {r["n_context_documents"] for r in rows} == {3}  # a.pdf, b.pdf, c.pdf
    assert all(r["extracted_at"] for r in rows)


@pytest.mark.django_db
def test_draft_split_separates_abstentions_from_substantive_answers():
    """The split is the informative form: the draft total itself is 77 or 0."""
    assay = AssayFactory.create()
    subsection = SubsectionFactory.create(section__question_set__label="gssplit")

    def answer(**kwargs):
        return AnswerFactory.create(
            assay=assay,
            question=QuestionFactory.create(subsection=subsection),
            **kwargs,
        )

    answer(accepted=True, answer_text="a real answer", answer_documents=["a.pdf"])
    answer(accepted=False, answer_text="another real answer", answer_documents=["a.pdf"])
    # The standardised abstention, and a whitespace-mangled variant of it: both are the
    # model declining, and a plain iexact test would miss the second.
    answer(accepted=True, answer_text=NOT_FOUND, answer_documents=["a.pdf"])
    answer(accepted=False, answer_text=f"  {NOT_FOUND} ", answer_documents=["a.pdf"])
    # Drafted but empty: neither substantive nor an abstention.
    answer(accepted=False, answer_text="", answer_documents=["a.pdf"])
    # Never drafted — excluded from every draft count.
    answer(accepted=True, answer_text="typed by hand", answer_documents=None)

    records = audit._collect({"min_accepted": 1})
    row = next(r for r in records if r["assay_id"] == assay.id)

    assert row["n_drafted_answers"] == 5
    assert row["n_drafted_non_trivial"] == 2
    assert row["n_drafted_not_found"] == 2


@pytest.mark.django_db
def test_new_columns_reach_the_csv(tmp_path):
    assay = AssayFactory.create()
    subsection = SubsectionFactory.create(section__question_set__label="gscsv")
    AnswerFactory.create(
        assay=assay,
        question=QuestionFactory.create(subsection=subsection),
        accepted=True,
        answer_text="gold",
        answer_documents=["only.pdf"],
    )

    out = tmp_path / "gold.csv"
    audit.run({"min_accepted": 1, "no_cosine": True, "out": str(out)})

    header, *body = out.read_text(encoding="utf-8").splitlines()
    assert "n_drafted_answers" in header and "n_context_documents" in header
    assert body, "the accepted answer should be exported"
