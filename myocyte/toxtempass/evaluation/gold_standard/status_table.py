"""Presentable status table of the scientist-reviewed gold ToxTemp answers.

Reads the latest typed gold CSV in ``output/_analysis/`` (or a path given as argv[1]) and
prints a per-assay Markdown table + a one-line summary, writing them to
``output/_plotting/gold_status_table.{md,html,png}`` for slides. Pure pandas — no Django.

    poetry run python toxtempass/evaluation/gold_standard/status_table.py [gold.csv]
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # import the sibling FLAT: the package needs Django
from edit_analysis import is_not_found  # noqa: E402  (needs sys.path first)

ANALYSIS_DIR = HERE / "output" / "_analysis"      # gold CSVs live here
PLOTTING_DIR = HERE / "output" / "_plotting"      # figures written here
QUESTIONNAIRE = 77  # "Each ToxTemp consists of 77 questions" (paper, Fig. 4 caption)

# Mirrors ``toxtempass.config.not_found_string``, copied because importing it would
# drag in Django settings and this module is deliberately standalone (pure pandas).
NOT_FOUND = "Answer not found in documents."

# Column labels, in the paper's vocabulary (Manuscript TEBT-2025-0014).
EXPERT = "Expert answers (N<sub>non-trivial</sub>)"
TRIVIAL = "Not found (N<sub>trivial</sub>)"
MISSING = "Unanswered (N<sub>missing</sub>)"


def _latest_gold() -> Path:
    """Newest typed gold CSV in output/_analysis (the extract→enrich product)."""
    hits = sorted(ANALYSIS_DIR.glob("gold_answers_typed_*.csv"))
    if not hits:
        raise SystemExit(
            "no gold_answers_typed_*.csv in output/_analysis (run extract + enrich first)"
        )
    return hits[-1]

# Email domain → readable institute label (for a multi-institute workshop).
INSTITUTE = {
    "rivm.nl": "RIVM", "iuf-duesseldorf.de": "IUF Düsseldorf", "tno.nl": "TNO",
    "uu.nl": "Utrecht U.", "swansea.ac.uk": "Swansea U.", "list.lu": "LIST",
    "recetox.muni.cz": "RECETOX", "kuleuven.be": "KU Leuven", "empa.ch": "Empa",
    "uniroma2.it": "Rome Tor Vergata", "nmbu.no": "NMBU", "kist-europe.de": "KIST Europe",
    "ait.ac.at": "AIT", "kist.re.kr": "KIST",
}


# Per-assay overrides for owners who signed up with a personal address, so the domain
# says nothing about their institute. Keyed by assay_id to keep the address out of git.
ASSAY_INSTITUTE = {109: "Utrecht U."}  # OATP1C1 thyroxin uptake


def _institute(email: str) -> str:
    """Map an owner email to a readable institute label (fallback: the domain)."""
    domain = str(email).split("@")[-1].lower()
    return INSTITUTE.get(domain, domain or "—")


def build(csv_path: Path) -> tuple[str, str]:
    """Return (markdown_table, summary_line) for the gold CSV."""
    # keep_default_na=False: answers written literally as "NA" / "n/a" are real expert
    # answers, and pandas' default NA parsing would silently turn them into blanks.
    df = pd.read_csv(csv_path, keep_default_na=False)
    # Re-derive the abstention flag from the text rather than trusting the CSV column: the
    # extract that produced this file used a substring test, which mislabels prose that
    # ends with the sentinel and misses mangled sentinels. audit.py now shares this test,
    # so a fresh extract agrees with what is rendered here.
    df["is_nf"] = df["gold_answer"].map(lambda t: is_not_found(t, NOT_FOUND))
    df["is_blank"] = df["gold_answer"].astype(str).str.strip() == ""
    df["institute"] = df["owner_email"].map(_institute)
    for aid, inst in ASSAY_INSTITUTE.items():
        df.loc[df["assay_id"] == aid, "institute"] = inst

    rows = []
    # Group by assay_id (NOT title): two same-titled assays at one institute are distinct
    # reviews, so merging them double-counts and pushes the counts past 77.
    for _aid, g in df.groupby("assay_id"):
        accepted = len(g)
        nf = int(g["is_nf"].sum())
        # An accepted answer with no text is a question the expert left unanswered, so it
        # belongs in N_missing — matching assess_ground_truth.py, which already subtracts
        # empty accepted answers from its gold count.
        blank = int(g["is_blank"].sum())
        first = g.iloc[0]
        rows.append(
            {
                "Assay": str(first["assay_title"])[:42],
                "Institute": first["institute"],
                # Column names follow the paper (Manuscript TEBT-2025-0014, Table 2 /
                # Fig. 4): N_non-trivial = any response other than the standardised
                # "Answer not found in documents."; N_trivial = that exact string;
                # N_missing = questions left unanswered by the expert (paper Table 1).
                # The three sum to QUESTIONNAIRE by construction, so a reader can check
                # the row. The old "Reviewed (%)" was N_non-trivial + N_trivial over 77,
                # which counted abstentions as coverage and had no paper counterpart.
                EXPERT: accepted - nf - blank,
                TRIVIAL: nf,
                MISSING: QUESTIONNAIRE - accepted + blank,
            }
        )
    tbl = pd.DataFrame(rows).sort_values(
        [EXPERT, TRIVIAL], ascending=False
    ).reset_index(drop=True)
    tbl.insert(0, "#", range(1, len(tbl) + 1))

    plain = {EXPERT: "Expert answers (N_non-trivial)",
             TRIVIAL: "Not found (N_trivial)",
             MISSING: "Unanswered (N_missing)"}
    header = "| " + " | ".join(plain.get(c, c) for c in tbl.columns) + " |"
    sep = "| " + " | ".join("---" for _ in tbl.columns) + " |"
    body = "\n".join(
        "| " + " | ".join(str(v) for v in r) + " |" for r in tbl.itertuples(index=False)
    )
    md = "\n".join([header, sep, body])

    n_assays = len(tbl)
    n_inst = df["institute"].nunique()
    n_people = df["owner_email"].nunique()
    total_acc = len(df)
    total_nf = int(df["is_nf"].sum())
    total_blank = int((df["is_blank"] & ~df["is_nf"]).sum())
    total_gold = total_acc - total_nf - total_blank
    # Every accepted answer is accounted for: gold + not-found + blank == accepted.
    blank_note = (
        f" and {total_blank} accepted but left empty" if total_blank else ""
    )
    summary = (
        f"**{total_gold} expert-validated answers** across **{n_assays} assays**, "
        f"**{n_people} scientists**, **{n_inst} institutes**. {total_acc} accepted "
        f"in total: {total_nf} are the standardised 'answer not found in "
        f"documents'{blank_note}. The three counts sum to {QUESTIONNAIRE}, the "
        f"ToxTemp question count."
    )
    return md, summary, tbl


def _green(n: float) -> str:
    """Light→dark green shade for an expert-answer count out of QUESTIONNAIRE."""
    t = max(0.0, min(1.0, n / QUESTIONNAIRE))
    return f"rgb({int(232 - 150 * t)},{int(245 - 75 * t)},{int(233 - 150 * t)})"


def make_table_figure(tbl: pd.DataFrame, summary: str) -> go.Figure:
    """Render the per-assay table as a styled Plotly figure (PNG/HTML for slides)."""
    n = len(tbl)
    zebra = ["#f4f7f8" if i % 2 else "#ffffff" for i in range(n)]
    shaded = [_green(v) for v in tbl[EXPERT]]
    # Shade the expert-answer column: that is the quantity the table is about.
    fill = [zebra, zebra, zebra, shaded, zebra, zebra]  # one entry per column
    fig = go.Figure(
        go.Table(
            columnwidth=[0.5, 5.0, 2.2, 1.9, 1.6, 1.7],
            header=dict(
                values=[f"<b>{c}</b>" for c in tbl.columns],
                fill_color="#1f4e5f", font=dict(color="white", size=13),
                align="left", height=34,
            ),
            cells=dict(
                values=[tbl[c].tolist() for c in tbl.columns],
                fill_color=fill, align="left", height=25, font=dict(size=12),
            ),
        )
    )
    # Wrap the subtitle: at 960px a single line clips silently.
    sub = textwrap.wrap(summary.replace("**", ""), 108)
    fig.update_layout(
        title=dict(
            text=(
                "Gold-standard ToxTemp answers — workshop result"
                f"<br><sub>{'<br>'.join(sub)}</sub>"
            ),
            x=0.01, font=dict(size=18),
        ),
        width=960, height=104 + 28 * len(sub) + 26 * n,
        margin=dict(l=12, r=12, t=60 + 28 * len(sub), b=12),
        template="plotly_white",
    )
    return fig


def main() -> None:
    """Print the markdown table + write the .md and a styled PNG/HTML figure."""
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_gold()
    md, summary, tbl = build(csv_path)
    title = "# Gold-standard ToxTemp answers — workshop result"
    out = f"{title}\n\n{summary}\n\n{md}\n"
    sys.stdout.write(out + "\n")
    out_dir = PLOTTING_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gold_status_table.md").write_text(out, encoding="utf-8")
    sys.stdout.write(f"Wrote {out_dir / 'gold_status_table.md'}\n")

    fig = make_table_figure(tbl, summary)
    fig.write_html(out_dir / "gold_status_table.html")
    sys.stdout.write(f"Wrote {out_dir / 'gold_status_table.html'}\n")
    try:
        fig.write_image(out_dir / "gold_status_table.png", scale=2)
        sys.stdout.write(f"Wrote {out_dir / 'gold_status_table.png'}\n")
    except Exception as exc:  # pragma: no cover - kaleido/Chrome optional
        sys.stdout.write(f"PNG export skipped ({type(exc).__name__}).\n")


if __name__ == "__main__":
    main()
