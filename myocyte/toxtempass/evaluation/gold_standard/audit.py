"""Gold-standard extraction: scientist-accepted answers + draft/edit-typing analysis.

Strictly READ-ONLY against the DB. For each non-demo assay's scientist-accepted
(``accepted=True``) answers it emits the gold answer plus — where the gpt-4o-mini draft
survives in history (pre-2025-09-13, see ``edit_analysis``) — the draft, the semantic
cosine of draft→final, and the edit type. Result: a reusable gold dataset and a quantified
picture of how often / how scientists changed the model.

Design (LLM-app best practices): central constants/sentinel, no DB writes, embeddings are
SHA-cached (reproducible + cheap on re-run), and DB reads happen in a short read-only
transaction *before* the slow embedding pass so no DB snapshot is held during API calls.

    cd myocyte && poetry run python manage.py extract_gold_answers --out output.csv
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from django.db import connection, transaction
from django.db.models import Count, Q
from django.db.models.signals import m2m_changed, pre_delete, pre_save

from toxtempass import config
from toxtempass.evaluation.gold_standard.edit_analysis import (
    CosineFn,
    analyze_answer_history,
    is_not_found,
)
from toxtempass.evaluation.post_processing import embeddings as emb
from toxtempass.evaluation.post_processing.similarity import cosine
from toxtempass.models import Answer, Assay

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "output"
EMB_DIR = OUTPUT_DIR / "_embeddings"          # SHA-cached vectors → reproducible re-runs
ANALYSIS_DIR = OUTPUT_DIR / "_analysis"       # data CSVs (gold, assessment, scores)
PLOTTING_DIR = OUTPUT_DIR / "_plotting"       # figures (bake-off, status table)
NOT_FOUND = config.not_found_string
_RO_UID = "gold_standard_read_only"

# Curation: assays that are NOT real scientist gold and must never enter the set
# (all christophe.vissers@rivm.nl). 75 = "hNTP_Test_C" scratch/test assay; 115 = a partial
# 9-answer "hNTP" duplicate. His real full hNTP review (#103, 77 answers) is kept. Edit
# this set as more non-gold assays are identified.
EXCLUDED_ASSAY_IDS = frozenset({75, 115})

# One place defining the output schema. Every name maps to a populated record key below.
CSV_COLUMNS = [
    # identity
    "assay_id", "assay_title", "assay_description", "owner_email", "submission_date",
    "question_set_label", "question_id", "section", "subsection", "question_text",
    # accepted (gold) answer
    "gold_answer", "is_not_found", "answer_documents",
    # baseline → accepted edit delta (computed for ALL answers; delta_exact = confidence).
    # baseline_kind: model_draft (true delta) | first_human_save (lower-bound delta).
    "baseline_kind", "delta_exact", "baseline_answer", "change_type",
    "cosine_baseline_final", "lexical_ratio_baseline_final",
    "chars_added", "chars_removed",
    # history provenance
    "n_history", "n_nonblank_snapshots", "n_human_edits", "n_reviewers",
]


def _block_write(*_args: object, **_kwargs: object) -> None:
    """Vendor-agnostic tripwire: any ORM write during the audit is a bug — fail loudly."""
    raise RuntimeError("gold_standard audit is read-only; a DB write was attempted")


def _guard(connect: bool) -> None:
    """Connect/disconnect the write tripwires on pre_save/pre_delete/m2m_changed."""
    for sig in (pre_save, pre_delete, m2m_changed):
        if connect:
            sig.connect(_block_write, dispatch_uid=_RO_UID)
        else:
            sig.disconnect(dispatch_uid=_RO_UID)


def _make_cosine_fn() -> tuple[CosineFn, emb.EmbeddingCache]:
    """Build a SHA-cached semantic cosine; return (fn, cache) for the caller to save."""
    EMB_DIR.mkdir(parents=True, exist_ok=True)
    cache = emb.EmbeddingCache(EMB_DIR)
    emb.set_persistent_cache(cache)

    def cosine_fn(a: str, b: str) -> float:
        if (a or "").strip() == (b or "").strip():
            return 1.0
        va = emb.embed_texts([a])[0]
        vb = emb.embed_texts([b])[0]
        return float(cosine(va, vb))

    return cosine_fn, cache


def _collect(opts: dict) -> list[dict]:
    """READ-ONLY: gather accepted answers + their question/assay meta + history snapshots.

    Returns plain dicts (DB detached) so the slow embedding pass runs outside any DB
    transaction. History dates are reduced to ``date`` for the era comparison.
    """
    exclude = {
        e.strip().lower()
        for e in str(opts.get("exclude_emails") or "").split(",")
        if e.strip()
    }
    min_accepted = int(opts.get("min_accepted") or 1)
    limit = opts.get("limit")

    assays = (
        Assay.objects.filter(
            demo_lock=False, demo_template=False, demo_source__isnull=True
        )
        .exclude(id__in=EXCLUDED_ASSAY_IDS)   # known non-gold (scratch/test) assays
        .select_related("study__investigation__owner", "question_set")
        .annotate(n_acc=Count("answers", filter=Q(answers__accepted=True)))
        .filter(n_acc__gte=min_accepted)
        .order_by("id")
    )
    assays = [
        a
        for a in assays
        if (getattr(a.study.investigation.owner, "email", "") or "").lower()
        not in exclude
    ]
    if limit:
        assays = assays[: int(limit)]
    assay_by_id = {a.id: a for a in assays}

    answers = list(
        Answer.objects.filter(assay_id__in=assay_by_id, accepted=True)
        .select_related("question__subsection__section")
        .order_by("assay_id", "question_id")
    )

    # One bulk history query, grouped in Python — no N+1.
    hist: dict[int, list[dict]] = {}
    for h in Answer.history.model.objects.filter(
        id__in=[a.id for a in answers]
    ).values("id", "answer_text", "history_type", "history_user_id", "history_date"):
        # ``id`` on HistoricalAnswer is the preserved original Answer pk (indexed).
        hist.setdefault(h["id"], []).append(
            {
                "answer_text": h["answer_text"],
                "history_type": h["history_type"],
                "history_user_id": h["history_user_id"],
                "history_date": h["history_date"].date(),
            }
        )

    records = []
    for a in answers:
        assay = assay_by_id[a.assay_id]
        sub = a.question.subsection
        # answer_documents is a free JSONField; guard against legacy non-list shapes.
        docs = a.answer_documents if isinstance(a.answer_documents, list) else []
        records.append(
            {
                "assay_id": assay.id,
                "assay_title": assay.title or "",
                "assay_description": (assay.description or "").replace("\n", " ").strip(),
                "owner_email": getattr(
                    assay.study.investigation.owner, "email", ""
                ) or "",
                "submission_date": assay.submission_date.strftime("%Y-%m-%d"),
                "question_set_label": getattr(assay.question_set, "label", "") or "",
                "question_id": a.question_id,
                "section": getattr(sub.section, "title", "") if sub else "",
                "subsection": getattr(sub, "title", "") if sub else "",
                "question_text": a.question.question_text or "",
                "gold_answer": a.answer_text or "",
                "answer_documents": "; ".join(str(d) for d in docs),
                "history_rows": hist.get(a.id, []),
            }
        )
    return records


def run(opts: dict | None = None) -> dict:
    """Extract gold set + edit analysis; return summary, write CSV when --out is set."""
    opts = opts or {}
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)  # parents=True also makes OUTPUT_DIR

    # Phase 1 — short READ-ONLY transaction: pull everything into memory. Only issue
    # SET TRANSACTION READ ONLY only when our atomic() is the OUTERMOST block (a nested
    # caller — e.g. a TestCase — makes it a savepoint, where the SET raises); the signal
    # tripwire still guards writes either way, and SQLite has no SET so relies on it too.
    _guard(True)
    outermost = not connection.in_atomic_block
    try:
        with transaction.atomic():
            if outermost and connection.vendor == "postgresql":
                with connection.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
            records = _collect(opts)
    finally:
        _guard(False)

    # Phase 2 — embeddings (outside the DB txn; SHA-cached, deterministic). The cache is
    # saved in `finally` so a partial run still persists computed vectors (free re-runs).
    # --no-cosine skips embeddings entirely (pure DB read, no OpenAI key): the baseline is
    # still recovered, and cosine + edit type are filled later by ``enrich_gold_cosines``.
    cosine_fn, cache = (None, None) if opts.get("no_cosine") else _make_cosine_fn()
    try:
        for r in records:
            gold = r["gold_answer"]
            a = analyze_answer_history(r.pop("history_rows"), gold, NOT_FOUND, cosine_fn)
            r.update(
                {
                    "is_not_found": is_not_found(gold, NOT_FOUND),
                    "baseline_kind": a["baseline_kind"],
                    "delta_exact": a["delta_exact"],
                    "baseline_answer": a["baseline_answer"],
                    "change_type": a["change_type"],
                    "cosine_baseline_final": a["cosine_baseline_final"],
                    "lexical_ratio_baseline_final": a["lexical_ratio_baseline_final"],
                    "chars_added": a["chars_added"],
                    "chars_removed": a["chars_removed"],
                    "n_history": a["n_history"],
                    "n_nonblank_snapshots": a["n_nonblank_snapshots"],
                    "n_human_edits": a["n_human_edits"],
                    "n_reviewers": len(a["reviewer_ids"]),
                }
            )
    finally:
        if cache is not None:
            cache.save()

    # Phase 3 — write + summarise.
    if opts.get("out"):
        _write_csv(records, str(opts["out"]))
    return _summary(records)


def _write_csv(records: list[dict], path: str) -> None:
    """Write one row per gold answer with the full draft/edit-typing analysis."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def _summary(records: list[dict]) -> dict:
    """Aggregate headline stats: delta confidence split + change-type distributions."""
    total = len(records)
    exact = [r for r in records if r.get("delta_exact")]
    return {
        "n_gold_answers": total,
        "n_assays": len({r["assay_id"] for r in records}),
        "n_delta_exact": len(exact),           # baseline = recovered model draft
        "n_delta_lower_bound": total - len(exact),  # baseline = first human save
        "change_type_counts_exact": dict(Counter(r["change_type"] for r in exact)),
        "change_type_counts_all": dict(Counter(r["change_type"] for r in records)),
    }
