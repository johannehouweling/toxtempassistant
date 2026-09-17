r"""Per-assay uptake and answer quality, from the raw dump (``extract_raw_answers``).

Answers two questions the gold CSV cannot: what the model **originally** drafted (a
substantive answer or the standardised abstention), and what became of it once a
scientist reviewed it.

    poetry run python toxtempass/evaluation/gold_standard/uptake_quality.py \\
        [--until 2026-07-10] [--all] [--full]

``--until`` keeps assays *created* on or before that date (a manuscript data freeze);
``--all`` also keeps assays nobody has reviewed, which the default drops to match the
gold set; ``--full`` adds the draft-recovery columns (see ``FULL_COLUMNS``). Reads
``output/_analysis/raw_{answers,history}.csv``, writes markdown + an HTML/PNG figure
to ``output/_plotting/``.

## How the original draft is recovered

``answer_documents`` is written only by a drafting run, so for each answer the first
history row carrying it (**F**) is the first save after the model ran:

| case | draft | confidence |
|---|---|---|
| no F | the live answer text — nobody saved the row since the run | exact |
| F by the worker, or before the 2025-09-13 cutoff | F's text **is** the draft | exact |
| F only flipped ``accepted`` | F's text is the untouched draft | exact |
| F changed the text | not preserved — the save replaced it | — |

## Why the record preserves some drafts and not others

The answer form writes a version only when the posted text differs from the stored text.
Browsers submit textarea newlines as CRLF and Django stores them unchanged, so the first
save of a **multi-line** draft replaces it even when the scientist typed nothing. Two
independent checks: of 5,598 saves between two people, **none** differ only in line
endings, while of the 55 first-saves after a worker-written draft, 39 differ only in line
endings and 16 are identical — not one changed the text in any other way. So a text change
at that first save is not evidence of editing.

The abstention is a single line, with no newline for the browser to rewrite, so an
untouched abstention keeps its draft on record. That asymmetry is why ``Originally not
found`` is a sound floor: an abstention a scientist answered instead leaves the record,
which can only lower the count, never raise it.

For the same reason a per-answer "was this edited" flag is not derivable. ``Edited before
acceptance`` is therefore a range: the floor counts only what the record proves (the draft
is on record and differs; a person's own consecutive saves changed the text with the same
documents; the first save was single-line, which the newline rewrite cannot explain), and
the ceiling additionally assumes every accepted answer whose draft is not preserved was
edited.

Remaining bias: the earmark re-draft and the whole-assay overwrite re-draft through the
same history-bypassing update, so a draft that was neither accepted nor edited can be
replaced without trace. Earmarking is what a scientist does to an unanswered question,
which pushes the recovered abstention count **down**.
"""

from __future__ import annotations

import sys
import textwrap
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from status_table import (  # noqa: E402  (needs sys.path first)
    ASSAY_INSTITUTE,
    PLOTTING_DIR,
    QUESTIONNAIRE,
    _green,
    _institute,
)

ANALYSIS_DIR = HERE / "output" / "_analysis"
# Mirrors ``toxtempass.config.not_found_string``; copied so this stays pure pandas. The
# test is EXACT equality: the fuzzy variant also matches human paraphrases, and the
# sentence is quoted in the FAQ and substituted into exports, so people do type it back.
SENTINEL = "Answer not found in documents."
# Commit 5f12fd7: drafts moved to a queryset .update(), which writes no history row.
CUTOFF = pd.Timestamp("2025-09-13T11:22:00Z")
_TRI = {"True": True, "False": False, "": None}
DASH = "—"
TITLE_CHARS = 52  # landscape figure, so titles can breathe

NUM, ASSAY, INST, DOCS = "#", "Assay", "Institute", "Documents (n)"
# Both columns count drafts, so neither is "the drafts": they are the two things the
# assistant did with a question, named after the paper's vocabulary.
SUB = "Substantive (N<sub>non-trivial</sub>)"
NF = "Not found (N<sub>trivial</sub>)"
# The model's own wording, read from the revision history. A "≥" because an abstention
# the scientist answered instead is no longer in the record — never an over-count.
ONF = "Originally not found (≥)"
# What the record preserves of the model's text, and so the denominator of ONF.
KNOWN = "Draft on record (n)"
ACC, ACCNF = "Expert-accepted (n)", "…of which not found"
# A range, not a count: for the drafts the record does not preserve, editing can be
# proven but not excluded, which sets the two ends.
EDIT = "Edited before acceptance"
PCT = "Expert-accepted (%)"
# (html header, plain-markdown header, relative width) in render order.
COLUMNS: list[tuple[str, str, float]] = [
    (NUM, NUM, 0.4),
    (ASSAY, ASSAY, 4.3),
    (INST, INST, 1.8),
    (DOCS, DOCS, 1.3),
    (SUB, "Substantive (N_non-trivial)", 1.5),
    (NF, "Not found (N_trivial)", 1.5),
    (ACC, ACC, 1.8),
    (PCT, PCT, 1.7),
]
# --full adds what the recovery buys, for a supplement or a reviewer's question. It is
# NOT the default: per assay, "originally not found" without "draft on record" beside it
# reads as "the model never abstained here" when the truth is "we hold 3 of its 77
# drafts", and the pair costs four columns to state honestly.
FULL_COLUMNS: list[tuple[str, str, float]] = [
    *COLUMNS[:6],
    (ONF, ONF, 1.5),
    (KNOWN, KNOWN, 1.4),
    (ACC, ACC, 1.8),
    (ACCNF, ACCNF, 1.3),
    (EDIT, EDIT, 1.8),
    (PCT, PCT, 1.7),
]


def _tri(series: pd.Series) -> pd.Series:
    """Read a CSV column of True/False/blank as real booleans and None."""
    return series.map(lambda v: _TRI.get(str(v), None))


def _norm(text: str) -> str:
    """Compare texts without line-ending or trailing-whitespace noise."""
    lines = str(text).replace("\r\n", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _fully_kept(draft: str, final: str) -> bool:
    """Report whether every character of the draft survives in the final text."""
    d, f = _norm(draft), _norm(final)
    if not d:
        return False
    kept = sum(b.size for b in SequenceMatcher(None, d, f).get_matching_blocks())
    return kept == len(d)


def _later_human_edit(rows: list[dict]) -> bool:
    """Report a text change between two consecutive human saves of the same documents.

    Both sides written by a person and the document list unchanged, so neither a
    drafting run nor the browser's newline rewrite (which only happens on the first
    save after drafting) can explain it: this is a person editing the answer.
    """
    prev = None
    for r in rows:
        if (
            prev is not None
            and prev["history_user_id"] != ""
            and r["history_user_id"] != ""
            and prev["answer_documents"] == r["answer_documents"]
            and _norm(prev["answer_text"]) != _norm(r["answer_text"])
        ):
            return True
        prev = r
    return False


def recover_drafts(answers: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Add ``source`` (how the draft was recovered), ``draft`` and derived flags."""
    history = history.copy()
    history["history_date"] = pd.to_datetime(
        history["history_date"], utc=True, format="mixed"
    )
    for col in ("accepted", "documents_set"):
        history[col] = _tri(history[col])
    history = history.sort_values(["answer_id", "history_date", "history_id"])
    by_answer = {aid: g.to_dict("records") for aid, g in history.groupby("answer_id")}

    sources, drafts, proven = [], [], []
    for row in answers.itertuples():
        rows = by_answer.get(row.answer_id, [])
        k = next((i for i, r in enumerate(rows) if r["documents_set"]), None)
        edited = False
        if not row.drafted:
            sources.append("never_drafted")
            drafts.append("")
        elif k is None:
            sources.append("untouched")  # never saved since the run: the live text is it
            drafts.append(row.answer_text)
        else:
            f = rows[k]
            if f["history_user_id"] == "" or f["history_date"] < CUTOFF:
                sources.append("saved_draft")  # the era that saved through the model
                drafts.append(f["answer_text"])
            elif f["accepted"] is True and not (k and rows[k - 1]["accepted"] is True):
                sources.append("accept_only")  # accepted without touching the text
                drafts.append(f["answer_text"])
            else:
                # The draft is gone. Two things still PROVE a person changed the text:
                # a single-line first save (no newline for the browser to rewrite, so
                # the form wrote that row because the text really differed), and any
                # later change between two human saves with the same document list
                # (no re-draft to explain it). Everything else stays undecidable.
                sources.append("lost")
                drafts.append("")
                edited = "\n" not in f["answer_text"]
        if not edited:
            edited = _later_human_edit(rows)
        proven.append(edited)
    answers["source"] = sources
    answers["draft"] = drafts
    answers["edit_proven"] = proven
    answers["draft_known"] = answers.source.isin(
        ["untouched", "saved_draft", "accept_only"]
    )
    answers["draft_is_nf"] = answers.draft.map(lambda t: _norm(t) == SENTINEL)
    answers["final_is_nf"] = answers.answer_text.map(lambda t: _norm(t) == SENTINEL)
    answers["final_blank"] = answers.answer_text.map(lambda t: _norm(t) == "")
    return answers


def per_assay(answers: pd.DataFrame) -> pd.DataFrame:
    """One row per assay: uptake, what the model drafted, and what became of it."""
    rows = []
    for aid, g in answers.groupby("assay_id"):
        first = g.iloc[0]
        docs: set[str] = set()
        for cell in g.answer_documents:
            docs.update(n.strip() for n in str(cell).split(";") if n.strip())
        drafted = g[g.drafted == True]  # noqa: E712 — a pandas mask, not a bool test
        acc = g[g.accepted == True]  # noqa: E712
        known_acc = acc[acc.draft_known]
        rows.append(
            {
                "assay_id": aid,
                ASSAY: textwrap.shorten(
                    str(first.assay_title), TITLE_CHARS, placeholder="…"
                ),
                INST: ASSAY_INSTITUTE.get(aid, _institute(str(first.owner_email))),
                "submission_date": str(first.submission_date),
                DOCS: len(docs),
                # Today's text, the quantity Table 1 has always reported.
                SUB: int((~drafted.final_is_nf & ~drafted.final_blank).sum()),
                NF: int(drafted.final_is_nf.sum()),
                # What the model itself wrote, where that survives: a lower bound.
                ONF: int((drafted.draft_known & drafted.draft_is_nf).sum()),
                KNOWN: int(drafted.draft_known.sum()),
                ACC: len(acc),
                ACCNF: int(acc.final_is_nf.sum()),
                # Editing before acceptance, bounded over the accepted answers the model
                # drafted. FLOOR: the draft is on record and differs, or a person's own
                # saves changed the text (see _later_human_edit), or the first save was
                # single-line — none of which the newline rewrite can explain. CEILING:
                # the floor plus every accepted answer whose draft the record does not
                # preserve, i.e. assuming each of those was edited.
                "edit_floor": int(
                    sum(
                        _norm(r.draft) != _norm(r.answer_text)
                        for r in known_acc.itertuples()
                    )
                    + ((~acc.draft_known) & acc.edit_proven & acc.drafted).sum()
                ),
                "edit_ceiling": int(
                    sum(
                        _norm(r.draft) != _norm(r.answer_text)
                        for r in known_acc.itertuples()
                    )
                    + ((~acc.draft_known) & acc.drafted).sum()
                ),
                "acc_drafted": int(acc.drafted.sum()),
                PCT: 100 * len(acc) / QUESTIONNAIRE,
            }
        )
    tbl = pd.DataFrame(rows)
    # "floor–ceiling of n": a range and the accepted-and-drafted answers it ranges over,
    # collapsed to one number when the record settles every one of them.
    tbl[EDIT] = [
        DASH
        if not n
        else (f"{lo} of {n}" if lo == hi else f"{lo}–{hi} of {n}")
        for lo, hi, n in zip(
            tbl["edit_floor"], tbl["edit_ceiling"], tbl["acc_drafted"], strict=True
        )
    ]
    return tbl


def _cell(col: str, value: object) -> str:
    """Render one cell; the accepted column holds a number and gains its % sign here."""
    if col == PCT and isinstance(value, (int, float)):
        return f"{value:.0f}%"
    return str(value)


def make_figure(tbl: pd.DataFrame, summary: str, title: str) -> go.Figure:
    """Render the table landscape, wide enough for the assay titles."""
    n = len(tbl)
    zebra = ["#f7f9fa" if i % 2 else "white" for i in range(n)]
    shaded = [_green(v * QUESTIONNAIRE / 100) for v in tbl[PCT]]
    widths = {html: w for html, _, w in FULL_COLUMNS}
    fill = [shaded if c == PCT else zebra for c in tbl.columns]
    sub = textwrap.wrap(summary.replace("**", ""), 150)
    sub_px = 34 * len(sub)
    fig = go.Figure(
        go.Table(
            columnwidth=[widths[c] for c in tbl.columns],
            header=dict(
                values=[f"<b>{c}</b>" for c in tbl.columns],
                fill_color="#1f4e5f", font=dict(color="white", size=13),
                align="left", height=40,
            ),
            cells=dict(
                values=[[_cell(c, v) for v in tbl[c]] for c in tbl.columns],
                fill_color=fill, align="left", height=25, font=dict(size=12),
            ),
        )
    )
    fig.update_layout(
        title=dict(
            text=(
                f"<b>{title}</b>"
                f"<br><span style='font-size:13px'>{'<br>'.join(sub)}</span>"
            ),
            x=0.01, xanchor="left", font=dict(size=18),
        ),
        width=1600, height=120 + sub_px + 26 * n,
        margin=dict(l=12, r=12, t=70 + sub_px, b=12),
        template="plotly_white",
    )
    return fig


def main() -> None:
    """Build the table, print it, and write markdown + HTML/PNG."""
    args = sys.argv[1:]
    until = ""
    if "--until" in args:
        i = args.index("--until")
        if i + 1 >= len(args):
            raise SystemExit("--until needs a date, e.g. --until 2026-07-10")
        until = args[i + 1]
    keep_all = "--all" in args
    columns = FULL_COLUMNS if "--full" in args else COLUMNS

    answers = pd.read_csv(ANALYSIS_DIR / "raw_answers.csv", keep_default_na=False)
    history = pd.read_csv(ANALYSIS_DIR / "raw_history.csv", keep_default_na=False)
    for col in ("drafted", "accepted"):
        answers[col] = _tri(answers[col])
    answers = recover_drafts(answers, history)

    if until:
        # Filters on assay CREATION, not on when answers were accepted: the counts stay
        # current, which is what makes the freeze reproducible from these files alone.
        answers = answers[answers.submission_date.str[:10] <= until]
    reviewed = set(answers[answers.accepted == True].assay_id)  # noqa: E712
    if not keep_all:
        answers = answers[answers.assay_id.isin(reviewed)]

    tbl = per_assay(answers)
    tbl = tbl.sort_values([PCT, ONF], ascending=[False, True]).reset_index(drop=True)
    tbl.insert(0, NUM, range(1, len(tbl) + 1))
    view = tbl[[c for c, _, _ in columns]].copy()
    # A total row, so every column can be checked against the text that cites it.
    total = {c: "" for c in view.columns}
    total[ASSAY] = f"All {len(tbl)} assays"
    for col in (DOCS, SUB, NF, ONF, KNOWN, ACC, ACCNF):
        if col in total:
            total[col] = int(tbl[col].sum())
    lo, hi, n = (int(tbl[c].sum()) for c in ("edit_floor", "edit_ceiling", "acc_drafted"))
    if EDIT in total:
        total[EDIT] = f"{lo}–{hi} of {n}"
    total[PCT] = 100 * int(tbl[ACC].sum()) / (QUESTIONNAIRE * len(tbl))
    view.loc[len(view)] = total

    plain = {html: text for html, text, _ in columns}
    header = "| " + " | ".join(plain[c] for c in view.columns) + " |"
    sep = "| " + " | ".join("---" for _ in view.columns) + " |"
    body = "\n".join(
        "| "
        + " | ".join(_cell(c, v) for c, v in zip(view.columns, r, strict=True))
        + " |"
        for r in view.itertuples(index=False)
    )
    md = "\n".join([header, sep, body])

    d = answers[answers.drafted == True]  # noqa: E712
    acc = answers[answers.accepted == True]  # noqa: E712
    known = int(d.draft_known.sum())
    orig_nf = int((d.draft_known & d.draft_is_nf).sum())
    summary = (
        f"{answers.assay_id.nunique()} assays · {tbl[INST].nunique()} institutes · "
        f"{len(d)} of {len(answers)} questions drafted by the model"
        + (f" · assays created on or before {until}" if until else "")
        + f". Scientists have accepted {len(acc)} answers "
        f"({len(acc) / max(len(answers), 1):.0%} of the questionnaire), of which "
        f"{int(acc.final_is_nf.sum())} state the information was absent. "
        "A draft counts as substantive when the assistant answered the question from "
        "the documents instead of stating that the information was absent — it says "
        "nothing about whether the answer is correct, which is what the reviewing "
        "scientist judges."
    )
    if columns is FULL_COLUMNS:
        # The recovery story belongs with the columns it explains, not above the plain
        # table, where it would raise questions the plain table cannot answer.
        summary += (
            f" The model's own wording is on record for {known} drafts "
            f"({known / max(len(d), 1):.0%}); in {orig_nf} of them it stated the "
            "information was absent, so it did so for at least "
            f"{orig_nf / max(len(d), 1):.0%} of the questions it drafted. Of the {n} "
            f"accepted answers the model drafted, between {lo} and {hi} were edited "
            f"({lo / max(n, 1):.0%}–{hi / max(n, 1):.0%}) before acceptance."
        )

    sys.stdout.write(f"{summary}\n\n{md}\n\n")
    PLOTTING_DIR.mkdir(parents=True, exist_ok=True)
    stem = "uptake_quality"
    if "--full" in args:
        stem += "_full"
    if until:
        stem += "_until_" + until.replace("-", "")
    (PLOTTING_DIR / f"{stem}.md").write_text(f"{summary}\n\n{md}\n", encoding="utf-8")
    fig = make_figure(
        view,
        summary,
        "ToxTempAssistant — assays created and expert-accepted answers"
        + (" · what became of the model's drafts" if columns is FULL_COLUMNS else ""),
    )
    fig.write_html(PLOTTING_DIR / f"{stem}.html")
    sys.stdout.write(f"Wrote {PLOTTING_DIR / f'{stem}.md'} and .html\n")
    try:
        fig.write_image(PLOTTING_DIR / f"{stem}.png", scale=2)
        sys.stdout.write(f"Wrote {PLOTTING_DIR / f'{stem}.png'}\n")
    except Exception as exc:  # pragma: no cover - kaleido/Chrome optional
        sys.stdout.write(f"PNG export skipped ({type(exc).__name__}).\n")


if __name__ == "__main__":
    main()
