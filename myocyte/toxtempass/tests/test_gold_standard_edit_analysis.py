"""Unit tests for the Django-free gold-standard edit-analysis logic.

The cosine signal is injected, so these run with no embeddings/API — semantic similarity
is supplied directly to exercise the classification thresholds.
"""

from datetime import date

from toxtempass import config
from toxtempass.evaluation.gold_standard.edit_analysis import (
    analyze_answer_history,
    classify_edit,
    is_not_found,
)

# Use the central sentinel (toxtempass/__init__.py Config) so the test never drifts.
NF = config.not_found_string


def test_classify_none():
    assert classify_edit("abc", "abc", 1.0, NF)["edit_type"] == "none"


def test_classify_abstain_to_answer():
    # Model abstained, scientist supplied a real answer = a confirmed recall gap.
    # Sentinel check precedes cosine, so even a low cosine still classifies correctly.
    assert classify_edit(NF, "a real grounded answer", 0.2, NF)[
        "edit_type"
    ] == "abstain_to_answer"


def test_classify_answer_to_abstain():
    # Scientist replaced a (likely hallucinated) answer with the abstention sentinel.
    assert classify_edit("a confident wrong answer", NF, 0.2, NF)[
        "edit_type"
    ] == "answer_to_abstain"


def test_classify_rewrite_low_cosine():
    # Low semantic cosine ⇒ meaning changed ⇒ rewrite, regardless of surface overlap.
    assert classify_edit("alpha beta gamma", "totally different now", 0.4, NF)[
        "edit_type"
    ] == "rewrite"


def test_classify_cosmetic_high_lexical():
    # High cosine + tiny surface change (unit symbol) ⇒ cosmetic.
    r = classify_edit("The value is 5 uM.", "The value is 5 µM.", 0.99, NF)
    assert r["edit_type"] == "cosmetic"


def test_classify_expand():
    # Meaning preserved (cosine high), draft kept, substantial content added ⇒ expand.
    r = classify_edit("short", "short text with much more detail added here", 0.9, NF)
    assert r["edit_type"] == "expand"
    assert r["chars_added"] > 0


def test_classify_trim():
    r = classify_edit(
        "a long original answer with extra detail", "a long original answer", 0.92, NF
    )
    assert r["edit_type"] == "trim"
    assert r["chars_removed"] > 0


def test_classify_reword_is_edit():
    # Same meaning (cosine ok) but reworded (low surface ratio) and similar length ⇒ edit.
    r = classify_edit("the cells were treated now", "cells got treatment ok", 0.85, NF)
    assert r["edit_type"] == "edit"


def _row(text, htype, uid, d):
    return {
        "answer_text": text, "history_type": htype, "history_user_id": uid,
        "history_date": d,
    }


def test_baseline_model_draft_is_exact():
    # A non-blank '~' snapshot with history_user_id=None is the gpt-4o-mini draft → the
    # baseline is that draft and the delta is EXACT (true draft→accepted).
    d = date(2025, 1, 1)
    rows = [
        _row("", "+", 1, d),
        _row("the gpt-4o-mini draft", "~", None, d),   # model draft (no request user)
        _row("scientist edited final", "~", 7, d),
    ]
    a = analyze_answer_history(rows, "scientist edited final", NF, lambda x, y: 0.4)
    assert a["baseline_kind"] == "model_draft"
    assert a["delta_exact"] is True
    assert a["baseline_answer"] == "the gpt-4o-mini draft"
    assert a["n_human_edits"] == 1 and a["reviewer_ids"] == [7]
    assert a["change_type"] == "rewrite"        # injected cosine 0.4 < REWRITE_MAX_COSINE
    assert a["cosine_baseline_final"] == 0.4


def test_baseline_first_human_save_is_lower_bound():
    # No user=None snapshot (draft written via .update(), unrecorded) → baseline is the
    # first human save and the delta is a LOWER BOUND (delta_exact False).
    d = date(2026, 1, 1)
    rows = [
        _row("", "+", 9, d),
        _row("human first save", "~", 9, d),
        _row("human final answer after more edits", "~", 9, d),
    ]
    b = analyze_answer_history(rows, "human final answer after more edits", NF,
                               lambda x, y: 0.9)
    assert b["baseline_kind"] == "first_human_save"
    assert b["delta_exact"] is False
    assert b["baseline_answer"] == "human first save"
    assert b["change_type"] != "n/a"            # a delta IS computed (lower bound)


def test_no_cosine_defers_typing():
    # --no-cosine (cosine_fn=None): the baseline is still recovered, but cosine + change
    # type are left blank for the local enrich pass (blank, NOT "n/a" = no baseline).
    d = date(2025, 1, 1)
    rows = [
        _row("", "+", 1, d),
        _row("the gpt-4o-mini draft", "~", None, d),
        _row("scientist edited final", "~", 7, d),
    ]
    a = analyze_answer_history(rows, "scientist edited final", NF, None)
    assert a["baseline_kind"] == "model_draft"
    assert a["delta_exact"] is True
    assert a["baseline_answer"] == "the gpt-4o-mini draft"
    assert a["change_type"] == ""
    assert a["cosine_baseline_final"] == ""


def test_saved_once_lower_bound_zero_delta():
    # Saved once (first non-blank == final): lower-bound delta is 0 (no post-save edits),
    # but it's still flagged lower-bound — not provably the verbatim draft.
    d = date(2026, 1, 1)
    rows = [_row("", "+", 9, d), _row("only version", "~", 9, d)]
    a = analyze_answer_history(rows, "only version", NF, lambda x, y: 1.0)
    assert a["baseline_kind"] == "first_human_save"
    assert a["delta_exact"] is False
    assert a["change_type"] == "none"


# ---------------------------------------------------------------------------
# is_not_found — the N_trivial classifier. Deliberately not a substring test:
# real cases from the 2026-06-16 prod extract are pinned below.
# ---------------------------------------------------------------------------

SENTINEL = NF


def test_exact_sentinel_is_not_found():
    assert is_not_found(SENTINEL, SENTINEL)


def test_sentinel_normalisation():
    for variant in ("  answer not found in documents  ", "ANSWER NOT FOUND IN DOCUMENTS",
                    "Answer not found in documents"):
        assert is_not_found(variant, SENTINEL), variant


def test_mangled_sentinel_is_still_not_found():
    # Real row: assay 71 / question 140 — transposed characters broke the old
    # substring test.
    assert is_not_found("Answer not found in dments.ocu", SENTINEL)


def test_prose_ending_with_sentinel_is_a_real_answer():
    # Real row: assay 5 / question 141 — substantive prose that happens to end with the
    # sentinel. The old substring test binned it as an abstention.
    prose = (
        "The original deposition date of the first version is not specified in the "
        "provided context. The current version is Version 1. " + SENTINEL
    )
    assert not is_not_found(prose, SENTINEL)


def test_placeholders_are_not_abstentions():
    # Per the workshop decision these count as real expert answers.
    for text in ("NA", "n/a", "None.", "Not applicable", "", "Unknown?"):
        assert not is_not_found(text, SENTINEL), text


def test_empty_sentinel_never_matches():
    assert not is_not_found("anything", "")

