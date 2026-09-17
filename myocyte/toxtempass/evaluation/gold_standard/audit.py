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
from collections.abc import Callable
from pathlib import Path

from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Q
from django.db.models.signals import m2m_changed, pre_delete, pre_save
from django.utils import timezone

from toxtempass import config
from toxtempass.evaluation.gold_standard.edit_analysis import (
    CosineFn,
    analyze_answer_history,
    is_not_found,
)
from toxtempass.evaluation.post_processing import embeddings as emb
from toxtempass.evaluation.post_processing.similarity import cosine
from toxtempass.models import Answer, Assay, AssayCost

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
    # identity + per-assay provenance (constant within an assay; repeated per row)
    "extracted_at", "assay_id", "assay_title", "assay_description", "owner_email",
    "submission_date", "n_context_documents", "n_drafted_answers",
    "n_drafted_non_trivial", "n_drafted_not_found",
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

# ── Raw dump schema (``dump_raw``) ────────────────────────────────────────────────────
# Deliberately raw: every answer and every saved version, no derived flags beyond the
# sentinel test. Uptake/quality classification (original abstention, retained verbatim,
# edited-not-accepted) is then computed LOCALLY from these files, so changing a definition
# is a local re-run instead of another read on production.
RAW_ANSWER_COLUMNS = [
    "extracted_at", "assay_id", "assay_title", "owner_email", "submission_date",
    "question_set_label", "answer_id", "question_id", "section", "subsection",
    "drafted", "answer_documents", "accepted", "llm_abstained", "is_sentinel",
    "is_not_found", "answer_text",
]
RAW_HISTORY_COLUMNS = [
    "answer_id", "assay_id", "history_id", "history_date", "history_type",
    "history_user_id", "accepted", "is_sentinel", "is_not_found",
    "documents_set", "answer_documents", "answer_text",
]
# Which model actually drafted an assay — the "gpt-4o-mini" claim is checkable per assay
# rather than assumed. Only rows created after cost tracking landed carry it.
RAW_COST_COLUMNS = [
    "assay_id", "model_key", "model_id", "temperature",
    "input_tokens", "output_tokens", "created_at", "updated_at",
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


def _base_assays(opts: dict, min_accepted: int) -> list[Assay]:
    """Non-demo, non-excluded assays, owner-email filtered and ``--limit`` capped.

    ``min_accepted`` is the caller's floor, not a constant: the gold extract wants assays
    with at least one accepted answer, the raw dump wants every assay (0) — an assay
    nobody reviewed is still uptake, and dropping it under-counts how many were created.
    """
    exclude = {
        e.strip().lower()
        for e in str(opts.get("exclude_emails") or "").split(",")
        if e.strip()
    }
    assays = (
        Assay.objects.filter(
            demo_lock=False, demo_template=False, demo_source__isnull=True
        )
        .exclude(id__in=EXCLUDED_ASSAY_IDS)   # known non-gold (scratch/test) assays
        .select_related("study__investigation__owner", "question_set")
        .annotate(
            n_acc=Count("answers", filter=Q(answers__accepted=True), distinct=True),
        )
        .filter(n_acc__gte=min_accepted)
        .order_by("id")
    )
    assays = [
        a
        for a in assays
        if (getattr(a.study.investigation.owner, "email", "") or "").lower()
        not in exclude
    ]
    limit = opts.get("limit")
    return assays[: int(limit)] if limit else assays


def _read_only(read: Callable[[], object]) -> object:
    """Run ``read()`` inside the audit's read-only transaction + write tripwires.

    Only issue SET TRANSACTION READ ONLY when our atomic() is the OUTERMOST block (a
    nested caller — e.g. a TestCase — makes it a savepoint, where the SET raises); the
    signal tripwire still guards writes either way, and SQLite has no SET so relies on it.
    """
    _guard(True)
    outermost = not connection.in_atomic_block
    try:
        with transaction.atomic():
            if outermost and connection.vendor == "postgresql":
                with connection.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
            return read()
    finally:
        _guard(False)


def _collect(opts: dict) -> list[dict]:
    """READ-ONLY: gather accepted answers + their question/assay meta + history snapshots.

    Returns plain dicts (DB detached) so the slow embedding pass runs outside any DB
    transaction. History dates are reduced to ``date`` for the era comparison.
    """
    assays = _base_assays(opts, int(opts.get("min_accepted") or 1))
    assay_by_id = {a.id: a for a in assays}

    answers = list(
        Answer.objects.filter(assay_id__in=assay_by_id, accepted=True)
        .select_related("question__subsection__section")
        .order_by("assay_id", "question_id")
    )

    # One pass over ALL of the assay's answers — not just the accepted ones, which would
    # under-count exactly the partially reviewed assays. Yields three per-assay numbers:
    #
    #   docs      distinct source filenames the drafting runs saw (same definition as
    #             ``assess_ground_truth``'s n_docs, so the two agree).
    #   drafted   rows the LLM wrote. ``process_llm_async`` is the only writer of
    #             answer_documents and writes it in the same queryset ``.update()`` as
    #             the text, so NOT NULL is an era-independent drafting flag — unlike the
    #             history snapshots, which that ``.update()`` bypasses (README's era
    #             caveat). Checked with ``is not None``, not ``== None`` in the ORM: on a
    #             JSONField that lookup is a JSON-null match, not a SQL NULL test.
    #   split     of those drafts, abstention vs substantive. answer_text is the CURRENT
    #             text, so a scientist who replaced an abstention moves that row from
    #             trivial to non-trivial: not_found is a LOWER bound on the model's
    #             abstentions and non_trivial an UPPER bound on its answers — exact for
    #             the rows nobody has reviewed, which dominate a partial review. It is a
    #             lower bound in the other direction too, because ``is_not_found`` is
    #             fuzzy and the sentence is quoted in the FAQ, the onboarding tooltip and
    #             the about page (and ``export.py`` substitutes it for blank answers), so
    #             a human CAN paste it back. Recovering what the model actually drafted
    #             needs the version history, not this column: see ``dump_raw``.
    docs_by_assay: dict[int, set[str]] = {}
    drafted: dict[int, int] = {}
    draft_nf: dict[int, int] = {}
    draft_sub: dict[int, int] = {}
    for aid, names, text in Answer.objects.filter(assay_id__in=assay_by_id).values_list(
        "assay_id", "answer_documents", "answer_text"
    ):
        if isinstance(names, list):
            docs_by_assay.setdefault(aid, set()).update(str(n) for n in names if n)
        if names is None:  # never drafted: the answer form leaves this column NULL
            continue
        drafted[aid] = drafted.get(aid, 0) + 1
        if is_not_found(text, NOT_FOUND):
            draft_nf[aid] = draft_nf.get(aid, 0) + 1
        elif (text or "").strip():
            draft_sub[aid] = draft_sub.get(aid, 0) + 1

    # One bulk history query, grouped in Python — no N+1. The order_by is load-bearing:
    # HistoricalAnswer's Meta ordering is newest-first, and ``edit_analysis`` sorts on the
    # date-truncated value with a STABLE sort, so without it the "first" non-blank
    # snapshot of a day is really that day's LAST save — which silently hides abstentions
    # a scientist replaced later the same day.
    hist: dict[int, list[dict]] = {}
    for h in (
        Answer.history.model.objects.filter(id__in=[a.id for a in answers])
        .order_by("id", "history_date", "history_id")
        .values("id", "answer_text", "history_type", "history_user_id", "history_date")
    ):
        # ``id`` on HistoricalAnswer is the preserved original Answer pk (indexed).
        hist.setdefault(h["id"], []).append(
            {
                "answer_text": h["answer_text"],
                "history_type": h["history_type"],
                "history_user_id": h["history_user_id"],
                "history_date": h["history_date"].date(),
            }
        )

    # Stamped once per run so the CSV carries its own provenance: the filename is set by
    # hand on the prod→local hop (README step 1 passes --out /tmp/gold.csv) and the local
    # enrich pass re-stamps it, so neither name nor mtime dates the DB read.
    extracted_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")

    records = []
    for a in answers:
        assay = assay_by_id[a.assay_id]
        sub = a.question.subsection
        # answer_documents is a free JSONField; guard against legacy non-list shapes.
        docs = a.answer_documents if isinstance(a.answer_documents, list) else []
        records.append(
            {
                "extracted_at": extracted_at,
                "assay_id": assay.id,
                "assay_title": assay.title or "",
                "n_context_documents": len(docs_by_assay.get(assay.id, ())),
                "n_drafted_answers": drafted.get(assay.id, 0),
                "n_drafted_non_trivial": draft_sub.get(assay.id, 0),
                "n_drafted_not_found": draft_nf.get(assay.id, 0),
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

    # Phase 1 — short READ-ONLY transaction: pull everything into memory.
    records = _read_only(lambda: _collect(opts))

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


def _sentinel_flags(text: str) -> dict[str, bool]:
    """Both abstention tests, kept apart on purpose.

    ``is_sentinel`` is exact equality with ``config.not_found_string`` — the model writes
    that string and nothing else, so it is the test for "the model abstained".
    ``is_not_found`` is the fuzzy variant, which also matches human paraphrases ("Not
    found in documents.") — the sentence is quoted in the FAQ, the onboarding tooltip and
    the about page, and ``export.py`` substitutes it for blank answers, so it does come
    back by copy-paste. Counting the two together would attribute human text to the model.
    """
    t = text or ""
    return {
        "is_sentinel": t.strip() == NOT_FOUND.strip(),
        "is_not_found": is_not_found(t, NOT_FOUND),
    }


def _dump_rows(opts: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """READ-ONLY: every answer, every saved version of it, and the run's cost rows."""
    assays = _base_assays(opts, int(opts.get("min_accepted") or 0))
    assay_by_id = {a.id: a for a in assays}
    extracted_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")

    answer_rows = []
    for a in (
        Answer.objects.filter(assay_id__in=assay_by_id)
        .select_related("question__subsection__section")
        .order_by("assay_id", "question_id")
    ):
        assay = assay_by_id[a.assay_id]
        sub = a.question.subsection
        # answer_documents is a free JSONField; guard against legacy non-list shapes.
        docs = a.answer_documents if isinstance(a.answer_documents, list) else []
        answer_rows.append(
            {
                "extracted_at": extracted_at,
                "assay_id": assay.id,
                "assay_title": assay.title or "",
                "owner_email": getattr(assay.study.investigation.owner, "email", "")
                or "",
                "submission_date": assay.submission_date.strftime("%Y-%m-%d"),
                "question_set_label": getattr(assay.question_set, "label", "") or "",
                "answer_id": a.id,
                "question_id": a.question_id,
                "section": getattr(sub.section, "title", "") if sub else "",
                "subsection": getattr(sub, "title", "") if sub else "",
                # The LLM ran on this row: ``process_llm_async`` is the only writer of
                # answer_documents, so NOT NULL survives the 2025-09-13 switch to queryset
                # ``.update()`` that stopped drafts reaching history. ``is not None``, not
                # the ORM's ``__isnull``-free ``== None``: on a JSONField that lookup is a
                # JSON-null match, not a SQL NULL test.
                "drafted": a.answer_documents is not None,
                "answer_documents": "; ".join(str(d) for d in docs),
                # Raw tri-state (True / False / empty for NULL) — "not accepted" and
                # "never looked at" are different states and the CSV keeps them apart.
                "accepted": "" if a.accepted is None else a.accepted,
                # What the model decided at drafting time, recorded since 2026-09-17.
                # Blank for earlier drafts, where the text is the only evidence.
                "llm_abstained": (
                    "" if a.llm_abstained is None else a.llm_abstained
                ),
                **_sentinel_flags(a.answer_text),
                "answer_text": a.answer_text or "",
            }
        )

    # Every saved version, FULL timestamp, explicitly ordered oldest-first. Both matter:
    # HistoricalAnswer's Meta ordering is newest-first, so an unordered query plus a
    # date-truncated sort (what the gold path does) silently picks the LAST save of the
    # first active day — that is the defect the classification below must not inherit.
    history_rows = []
    for h in (
        Answer.history.model.objects.filter(id__in=[r["answer_id"] for r in answer_rows])
        .order_by("id", "history_date", "history_id")
        .values(
            "id", "history_id", "history_date", "history_type", "history_user_id",
            "accepted", "answer_text", "answer_documents", "assay_id",
        )
    ):
        names = h["answer_documents"]
        history_rows.append(
            {
                # ``id`` on HistoricalAnswer is the preserved original Answer pk.
                "answer_id": h["id"],
                "assay_id": h["assay_id"],
                "history_id": h["history_id"],
                "history_date": h["history_date"].isoformat(),
                "history_type": h["history_type"],
                # NULL history_user = written by a worker/script, not a person. Combined
                # with the timestamp it separates the eras: before the 2025-09-13 switch
                # to queryset .update() the draft itself is a history row (user NULL from
                # the async worker, the uploader's id in the older in-request path).
                "history_user_id": (
                    "" if h["history_user_id"] is None else h["history_user_id"]
                ),
                "accepted": "" if h["accepted"] is None else h["accepted"],
                **_sentinel_flags(h["answer_text"]),
                # NULL vs [] matters and the joined string cannot express it: a run that
                # found no readable document still writes an empty list, so this flag —
                # not the text below — is what marks "a drafting run had touched this row
                # by the time it was saved", the anchor of the whole draft recovery.
                "documents_set": names is not None,
                # The full list, not a count: a drafting run stamps every answer it wrote
                # with the same list, so an answer whose list differs from its siblings'
                # was re-drafted by a later earmark run — the only trace that leaves.
                "answer_documents": (
                    "; ".join(str(d) for d in names) if isinstance(names, list) else ""
                ),
                "answer_text": h["answer_text"] or "",
            }
        )

    # AssayCost is the youngest table here and its columns have shipped in stages, so a
    # deployment can be older than this code. Read it in its own savepoint: a missing
    # column then degrades to "no cost rows" (the caller says so) instead of aborting the
    # surrounding read-only transaction and losing the answers and history with it.
    cost_rows = []
    try:
        with transaction.atomic():
            cost_rows = [
                {
                    "assay_id": c.assay_id,
                    "model_key": c.model_key,
                    "model_id": c.model_id,
                    # getattr, not attribute access: a deployment older than the field
                    # has no such attribute at all (the savepoint below only catches the
                    # other half of that skew, a model field with no column yet).
                    "temperature": getattr(c, "temperature", ""),
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "created_at": c.created_at.isoformat(),
                    "updated_at": c.updated_at.isoformat(),
                }
                for c in AssayCost.objects.filter(assay_id__in=assay_by_id).order_by(
                    "assay_id", "model_key"
                )
            ]
    except DatabaseError as exc:
        cost_rows = [{"assay_id": "", "model_key": f"UNAVAILABLE: {exc}"}]
    return answer_rows, history_rows, cost_rows


def dump_raw(opts: dict | None = None) -> dict:
    """READ-ONLY: dump answers + their full version history + cost rows as three CSVs.

    Written for the uptake/quality table, which the gold CSV cannot feed: that one holds
    accepted answers only, already reduced to one baseline→final comparison, and skips
    assays nobody reviewed. Here nothing is reduced — the classification (what the model
    originally drafted, whether the text was kept word for word, what was edited but never
    accepted) runs locally off these files, so a changed definition never costs another
    production read.

    ``out`` is a path STEM: ``/tmp/raw`` writes ``/tmp/raw_answers.csv``,
    ``_history.csv`` and ``_costs.csv``. Returns the row counts and the paths written.
    """
    opts = opts or {}
    stem = Path(str(opts.get("out") or (ANALYSIS_DIR / "raw")))
    if stem.is_dir():  # a directory given: name the files inside it
        stem = stem / "raw"
    stem.parent.mkdir(parents=True, exist_ok=True)

    answers, history, costs = _read_only(lambda: _dump_rows(opts))

    paths = {}
    for name, rows, columns in (
        ("answers", answers, RAW_ANSWER_COLUMNS),
        ("history", history, RAW_HISTORY_COLUMNS),
        ("costs", costs, RAW_COST_COLUMNS),
    ):
        path = stem.with_name(f"{stem.name}_{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        paths[name] = str(path)

    return {
        "n_assays": len({r["assay_id"] for r in answers}),
        "n_answers": len(answers),
        "n_accepted": sum(1 for r in answers if r["accepted"] is True),
        "n_drafted": sum(1 for r in answers if r["drafted"]),
        "n_history_rows": len(history),
        # Saves that already carried a drafting run's document list. The first of these
        # per answer is what the local pass classifies (draft row / accept-only / lost).
        "n_post_draft_saves": sum(1 for r in history if r["documents_set"]),
        "n_cost_rows": len(costs),
        "paths": paths,
    }


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
