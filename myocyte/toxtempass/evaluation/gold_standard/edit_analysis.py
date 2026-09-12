"""Pure (Django-free) logic for the gold-standard baseline→accepted edit delta.

Scientist-reviewed answers were drafted by gpt-4o-mini, then accepted/edited by a human.
For every accepted answer we measure the change from a **baseline** (the "before" text) to
the **accepted** (final) text. The baseline depends on what the version history actually
recorded — and we label its confidence honestly:

* ``model_draft`` (EXACT delta): a non-blank ``~`` history snapshot written WITHOUT a
  request user (``history_user_id IS NULL``) — the gpt-4o-mini draft saved by an async
  worker / script. The delta is then the true draft→accepted change.
* ``first_human_save`` (LOWER-BOUND delta): no such draft snapshot exists (the draft was
  written via queryset ``.update()``, which bypasses django-simple-history), so the first
  recorded non-blank text is the human's first save. The delta then captures only edits
  made AFTER that first save — a lower bound on the true draft→accepted change (it misses
  any editing the human did before saving). The true delta there needs the draft to be
  reconstructed by re-running gpt-4o-mini.

So a delta is reported for ALL accepted answers; ``delta_exact`` distinguishes the two.

The change is typed with **semantic cosine similarity** (embeddings) as the primary signal
for *meaning* change, plus a lexical (difflib) ratio + length to separate cosmetic tweaks
from expansions/trims. The cosine is **injected** (``cosine_fn``) so this module stays
Django-/API-free and deterministic; the caller wires in the project's SHA-cached embedding
similarity, keeping it reproducible. Tests:
``toxtempass/tests/test_gold_standard_edit_analysis.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

# Edit-type thresholds. Cosine is the embedding cosine in [-1, 1] (text-embedding-3-large)
# and lexical_ratio is a difflib ratio in [0, 1]. Tune here, nowhere else.
REWRITE_MAX_COSINE = 0.75   # semantic cosine below this ⇒ meaning changed ⇒ rewrite
COSMETIC_MIN_LEXICAL = 0.9  # surface (char) ratio at/above this ⇒ cosmetic tweak
GROWTH_FACTOR = 1.2         # length multiple for expand (final) / trim (baseline)

# An abstention that is *almost* the sentinel (a mangled paste, transposed characters) is
# still an abstention. Only short texts are fuzzy-matched: prose that merely ends with the
# sentinel is a real answer, not an abstention.
NOT_FOUND_MIN_RATIO = 0.85   # difflib ratio at/above this ⇒ a mangled sentinel
NOT_FOUND_MAX_LEN_FACTOR = 2  # only texts up to this multiple of the sentinel are fuzzed

CosineFn = Callable[[str, str], float]


def _norm(text: str) -> str:
    """Lowercase, strip surrounding whitespace and trailing punctuation."""
    return str(text).strip().lower().rstrip(".,;:! ")


def is_not_found(text: str, sentinel: str) -> bool:
    """Report whether ``text`` is the standardised abstention (N_trivial in the paper).

    Equality after normalisation, plus a fuzzy fallback for short mangled variants.
    Deliberately NOT a substring test: an answer that contains substantive prose and
    happens to end with the sentinel is a real expert answer, and counting it as an
    abstention understates the gold set.
    """
    t, ref = _norm(text), _norm(sentinel)
    if not ref:
        return False
    if t == ref:
        return True
    if len(t) <= NOT_FOUND_MAX_LEN_FACTOR * len(ref):
        return SequenceMatcher(None, t, ref).ratio() >= NOT_FOUND_MIN_RATIO
    return False


def _is_abstain(text: str, not_found_str: str) -> bool:
    """Return True if ``text`` is empty or the abstention sentinel ('not found')."""
    t = (text or "").strip()
    return not t or not_found_str.strip().lower() in t.lower()


def classify_edit(
    draft: str, final: str, cosine: float, not_found_str: str
) -> dict[str, Any]:
    """Classify the draft→final change into an edit type, using semantic cosine + surface.

    Types: ``none`` (verbatim), ``abstain_to_answer`` (model abstained, human answered — a
    confirmed recall gap), ``answer_to_abstain`` (human rejected a likely hallucination),
    ``rewrite`` (meaning changed — low cosine), ``cosmetic`` (tiny surface change),
    ``expand`` / ``trim`` (meaning kept, content added/removed), ``edit`` (reword/other).
    """
    d, f = (draft or "").strip(), (final or "").strip()
    lexical_ratio = SequenceMatcher(None, d, f).ratio()
    chars_added = max(0, len(f) - len(d))
    chars_removed = max(0, len(d) - len(f))
    d_abstain, f_abstain = _is_abstain(d, not_found_str), _is_abstain(f, not_found_str)

    # Cosine (meaning) decides rewrite; lexical ratio (surface) decides cosmetic; length
    # decides expand/trim; everything else is a moderate edit (e.g. a reword).
    if d == f:
        etype = "none"
    elif d_abstain and not f_abstain:
        etype = "abstain_to_answer"
    elif f_abstain and not d_abstain:
        etype = "answer_to_abstain"
    elif cosine < REWRITE_MAX_COSINE:
        etype = "rewrite"
    elif lexical_ratio >= COSMETIC_MIN_LEXICAL:
        etype = "cosmetic"
    elif len(f) >= len(d) * GROWTH_FACTOR:
        etype = "expand"
    elif len(d) >= len(f) * GROWTH_FACTOR:
        etype = "trim"
    else:
        etype = "edit"

    return {
        "edit_type": etype,
        "cosine": round(float(cosine), 4),
        "lexical_ratio": round(lexical_ratio, 4),
        "chars_added": chars_added,
        "chars_removed": chars_removed,
    }


def analyze_answer_history(
    rows: list[dict],
    final_text: str,
    not_found_str: str,
    cosine_fn: CosineFn | None,
) -> dict[str, Any]:
    """Compute the baseline→accepted edit delta for one answer, for ANY history shape.

    ``rows`` are the answer's ``HistoricalAnswer`` dicts with keys ``answer_text``,
    ``history_type`` ('+'/'~'/'-'), ``history_user_id``, ``history_date``. ``final_text``
    is the live accepted answer. ``cosine_fn(a, b)`` returns the semantic cosine (inject
    the project's cached embedding similarity). Pass ``cosine_fn=None`` to skip the
    embedding call (the ``--no-cosine`` prod path): the baseline is still recovered, but
    ``change_type`` / ``cosine_baseline_final`` are left blank for a later local pass.

    Baseline (see module docstring): the gpt-4o-mini draft if a non-blank ``~`` snapshot
    with ``history_user_id is None`` exists (``baseline_kind='model_draft'``,
    ``delta_exact=True`` — true draft→accepted); else the first recorded non-blank text
    (``baseline_kind='first_human_save'``, ``delta_exact=False`` — a lower bound missing
    pre-first-save editing). A delta is returned for every answer that has any text.
    """
    ordered = sorted(rows, key=lambda r: r.get("history_date"))
    nonblank = [r for r in ordered if (r.get("answer_text") or "").strip()]

    out: dict[str, Any] = {
        "n_history": len(ordered),
        "n_nonblank_snapshots": len(nonblank),
        "n_human_edits": 0,
        "reviewer_ids": [],
        "baseline_kind": "none",
        "delta_exact": False,
        "baseline_answer": "",
        "change_type": "n/a",
        "cosine_baseline_final": "",
        "lexical_ratio_baseline_final": "",
        "chars_added": "",
        "chars_removed": "",
    }

    # Baseline = the model draft (non-blank ~ saved with NO request user) when present,
    # else the first recorded non-blank text (the human's first save).
    draft_row = next(
        (
            r for r in nonblank
            if r.get("history_type") == "~" and r.get("history_user_id") is None
        ),
        None,
    )
    if draft_row is not None:
        baseline_row, kind, exact = draft_row, "model_draft", True
    elif nonblank:
        baseline_row, kind, exact = nonblank[0], "first_human_save", False
    else:
        baseline_row, kind, exact = None, "none", False

    # Human-edit evidence: '~' rows by a logged-in user (the model draft has user=None, so
    # it is naturally excluded from this count).
    human_rows = [
        r
        for r in ordered
        if r.get("history_type") == "~" and r.get("history_user_id") is not None
    ]
    out["n_human_edits"] = len(human_rows)
    out["reviewer_ids"] = sorted({r["history_user_id"] for r in human_rows})

    if baseline_row is not None:
        baseline = baseline_row.get("answer_text") or ""
        out.update(baseline_kind=kind, delta_exact=exact, baseline_answer=baseline)
        if cosine_fn is None:
            # --no-cosine: recover the baseline only; cosine + edit type are filled later
            # by the local pass. Blank (not "n/a", which signals "no baseline at all").
            out["change_type"] = ""
        else:
            c = classify_edit(
                baseline, final_text, cosine_fn(baseline, final_text), not_found_str
            )
            out.update(
                change_type=c["edit_type"],
                cosine_baseline_final=c["cosine"],
                lexical_ratio_baseline_final=c["lexical_ratio"],
                chars_added=c["chars_added"],
                chars_removed=c["chars_removed"],
            )
    return out
