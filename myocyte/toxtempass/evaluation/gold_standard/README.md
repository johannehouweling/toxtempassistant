# gold_standard — production scientist-accepted answers as ground truth

This workstream turns the answers scientists reviewed and **accepted** in production (drafted
by gpt-4o-mini) into a reusable ground-truth gold set, and quantifies *how* scientists changed
the model's drafts. It is the right reference for evaluation — far better than comparing models
to gpt-4o-mini, which is a weak baseline.

## What "gold" means here
A gold answer = the live `Answer.answer_text` where `accepted=True`, on a **non-demo** assay
(demo assays are auto-seeded per user and excluded). These are expert-approved, so they are the
reference any candidate model (or gpt-4o-mini itself) should be scored against.

## The draft / edit-typing — and the era caveat (read this)
We can often recover gpt-4o-mini's **original draft** and measure what the scientist changed —
but **only for assays generated before 2025-09-13** (git-verified):

| assay generated | draft write path | draft in history? |
|---|---|---|
| before 2025-05-12 (`3797e35`) | `answer.save()` **inside the request** | **yes** — but the row carries the *uploader's* user id, not NULL |
| 2025-05-12 → 2025-09-13 | `answer.save()` in the django-q worker | **yes** — the row has `history_user_id` NULL |
| on/after 2025-09-13 | queryset `.update()` (commit `5f12fd7`) | **no** — bypasses simple-history |

`edit_analysis` only accepts a NULL-user row as a draft, so drafts from the first era are
mislabelled `first_human_save` (conservative: it under-counts exact deltas, never invents
them). **Only one assay in the 2026-06-16 extract predates the cutoff**, so post-cutoff
recovery — the rule in *Raw dump* below, plus re-running the model where the files survive
— is what matters in practice, not this table.

## Edit-typing is semantic
The draft→final change is typed using **embedding cosine similarity** (the primary, *meaning*
signal) plus a lexical/length check (surface), via the project's SHA-cached embeddings
(`post_processing/embeddings.py`) so it is reproducible and cheap on re-run:

`none` (accepted verbatim) · `cosmetic` (typo/unit) · `expand` / `trim` (content added/removed,
meaning kept) · `edit` (reword/moderate) · `rewrite` (meaning changed) · `abstain_to_answer`
(model abstained, scientist answered — a confirmed recall gap) · `answer_to_abstain` (scientist
rejected a likely hallucination). Thresholds live in one place: `edit_analysis.py`.

The `edit_type` distribution is the scientific payoff — e.g. the share accepted verbatim
measures gpt-4o-mini's draft quality against human ground truth.

## Safety
Strictly read-only: DB reads run in a `SET TRANSACTION READ ONLY` transaction with
`pre_save`/`pre_delete`/`m2m_changed` write-tripwires; embeddings run *after* the transaction
closes (no DB snapshot held during API calls); no MinIO/object access. With `--no-cosine`
(the production default below) **no embeddings run at all** — prod does a pure DB read and
the cosine typing is computed locally.

## Run — local / dev
```bash
# read-only; --limit for a quick pass; default output is output/_analysis/gold_answers_<ts>.csv
python manage.py extract_gold_answers --limit 3
```
`--out` accepts a file (used verbatim) or a directory (gets a timestamped name); with no
`--out` it writes `output/_analysis/gold_answers_<YYYYMMDD_HHMM>.csv`. `output/` is gitignored
— the CSV (answer text + reviewer emails) never reaches git. Add `--no-cosine` to skip the
embedding pass (then type it with `enrich_gold_cosines`, as in production below).

## Run — production (the split: read on prod, type locally)

Prod has **no OpenAI embeddings credential** — the chat models are Azure Foundry, whose key
401s against `api.openai.com`, and Azure doesn't serve `text-embedding-3-large`. So the prod
step runs **`--no-cosine`**: a pure read-only DB dump, **no key needed**. The cosine
edit-typing is then computed on your machine, where the OpenAI key + the SHA embedding cache
live. The typed result is identical to an inline run — it's just split in two.

Code reaches prod by **merging to `main`** (auto-release → `Publish image` → `Deploy`,
~5–15 min on the Actions tab). Then, on the prod host (not in the `docker` group, so `sudo`;
explicit container name `djangoapp`, not `docker compose`):

```bash
# 1) extract — READ-ONLY, no OpenAI key, no API calls (one physical line)
sudo docker exec djangoapp python manage.py extract_gold_answers --no-cosine --out /tmp/gold.csv

# 2) copy it out of the container and make it readable
sudo docker cp djangoapp:/tmp/gold.csv /home/$USER/gold.csv && sudo chmod 644 /home/$USER/gold.csv
```
```bash
# 3) on your LOCAL machine, pull it down (gitignored landing spot)
scp <user>@<prod-host>:/home/<user>/gold.csv ~/Downloads/gold_no_cosine.csv

# 4) fill the cosine edit-typing locally (uses your OpenAI key + SHA cache). No --out → it
#    lands in output/_analysis/gold_answers_typed_<ts>.csv, which the plotting scripts glob for.
cd myocyte && poetry run python manage.py enrich_gold_cosines --in ~/Downloads/gold_no_cosine.csv
```
```bash
# 5) wipe the prod copies — the CSV holds gold answers + reviewer emails (PII)
sudo docker exec djangoapp rm -f /tmp/gold.csv && sudo rm -f /home/$USER/gold.csv
```

No `--exclude-emails` by default: demo assays are already filtered in code, and `owner_email`
is a column, so drop any genuine test accounts during analysis rather than risk losing real
reviewers. Add `--exclude-emails a@x,b@y` only for known dummy accounts. Known non-gold
scratch/test/partial **assays** are dropped centrally via `audit.EXCLUDED_ASSAY_IDS`
(currently #75 `hNTP_Test_C` + #115 partial hNTP); add ids there as more are identified — it
filters per-assay, not per-person, so real reviews by the same owner are kept (e.g. that
owner's full hNTP review #103 stays).

**Inline (with-cosine) alternative:** if a valid OpenAI key is available to the container
(`-e OPENAI_API_KEY=sk-…`), drop `--no-cosine` and skip steps 3–4 — the extract types inline.
The split is preferred so no key ever touches prod.

**Fallback** if step 1 prints `Unknown command` / `unrecognized arguments: --no-cosine`, the
container is still the old image — from the repo dir on prod: `sudo docker compose --profile
prod pull djangoapp && sudo docker compose --profile prod up -d djangoapp`, then retry.

**Tip (paste mangling):** keep each command on ONE physical line.

The companion **sufficiency** check (how much gold exists, by whom, with what docs) is
`python manage.py assess_ground_truth` — same read-only / prod-ops pattern.

### Per-assay columns (status table)

Besides the per-answer rows, the extract stamps per-assay values onto every row:

| column | meaning |
|---|---|
| `extracted_at` | when the DB was read. The filename can't say — the prod→local hop names the CSV by hand and the local cosine pass re-stamps it. |
| `n_context_documents` | distinct source filenames over **all** the assay's answers; same definition as `assess_ground_truth`'s `n_docs`. |
| `n_drafted_answers` | answers the LLM wrote: `answer_documents IS NOT NULL`. Era-independent, unlike history snapshots — the post-2025-09-13 queryset `.update()` writes none. |
| `n_drafted_non_trivial` | of those drafts, the substantive ones. |
| `n_drafted_not_found` | of those drafts, the standardised abstention. |

`n_drafted_answers` is **77 or 0** for a run that completed — the LLM drafts the whole
questionnaire once documents are supplied, and never runs without them — but a failed
future is skipped (`views.py` `continue`) and a deleted assay or a dead worker truncates
the run, leaving those rows NULL. Use the per-assay count as the denominator, never a
hard-coded 77.

The split reads the *current* `answer_text`, so a scientist who replaced an abstention
moves that row from trivial to non-trivial: `n_drafted_not_found` is a **lower bound** on
the model's abstentions and `n_drafted_non_trivial` an **upper bound** on its answers —
exact for rows nobody has reviewed, which dominate a partial review. It is a lower bound
in the other direction too: `is_not_found` is fuzzy, and the sentence is quoted in the FAQ,
the onboarding tooltip and the about page (and `export.py` substitutes it for blank
answers), so a human **can** paste it back. For what the model actually drafted, use the
raw dump below, not this column. A CSV extracted before these columns existed renders em
dashes for the split (it deliberately does *not* substitute accepted-abstentions, a
different quantity) and falls back to documents cited by *accepted* answers only, so
re-extract to fill them properly.

## Raw dump — uptake & what happened to each draft

The gold CSV holds accepted answers only, already reduced to one baseline→final
comparison, and skips assays nobody reviewed — so it cannot answer "how many answers did
the model originally abstain on, and what happened to them?". `extract_raw_answers` dumps
the inputs unreduced; every classification then runs **locally**, so a changed definition
costs a local re-run instead of another read on production.

```bash
# on prod — READ-ONLY, no API key (one physical line)
sudo docker exec djangoapp python manage.py extract_raw_answers --out /tmp/raw
sudo docker cp djangoapp:/tmp/raw_answers.csv /home/$USER/ && sudo docker cp djangoapp:/tmp/raw_history.csv /home/$USER/ && sudo docker cp djangoapp:/tmp/raw_costs.csv /home/$USER/
sudo chmod 644 /home/$USER/raw_*.csv
# then, locally: scp them down, and afterwards wipe BOTH copies (answer text + emails)
sudo docker exec djangoapp rm -f /tmp/raw_*.csv && sudo rm -f /home/$USER/raw_*.csv
```

| file | rows |
|---|---|
| `raw_answers.csv` | every answer of every non-demo assay (`--min-accepted 0`), with `drafted`, `accepted` as a tri-state, both abstention flags and the live text. |
| `raw_history.csv` | every saved version, oldest-first, with `documents_set`, `history_user_id`, `accepted` and the text at that moment. |
| `raw_costs.csv` | `AssayCost` rows — which deployment actually drafted an assay, so "gpt-4o-mini" is checked per assay rather than assumed. |

**Draft recovery rule** (per answer, over its history oldest-first). Let `F` = the first
row with `documents_set` true. Only a drafting run writes `answer_documents`, and seeding
leaves it NULL, so `F` is the first save *after* a drafting run.

| case | what the draft is | confidence |
|---|---|---|
| no `F` | the live `answer_text` — nobody saved the row since the run | exact for that run |
| `F` before the 2025-09-13 cutoff, or `history_user_id` empty | `F` **is** the draft row (the era that still saved through the model) | exact |
| `F.accepted` true and the row before it isn't | accept-only submit: `F.answer_text` is the untouched draft | exact for that run |
| otherwise (`F` is a text change) | **gone** — a text row exists only because the posted text differed, so the stored draft was provably not the exact sentinel | lost |

Two biases to state whenever these counts are published:

* "Exact for that run" is not "exact for the first run". The earmark re-draft
  (`forms.py`) and the whole-assay overwrite (`views.py`) both re-draft through the same
  history-less `.update()`, so a sentinel draft that was neither accepted nor edited is
  overwritten without trace. Earmarking is exactly what a scientist does to a not-found
  answer after uploading more documents, so this removes **abstentions specifically** and
  pushes the recovered count **down**. Per-assay flag: answers whose `answer_documents`
  differs from the assay's modal list were re-drafted.
* Use exact equality with `config.not_found_string` for "the model abstained", and keep
  the fuzzy `is_not_found` as a separate, labelled column — it also matches human
  paraphrases such as "Not found in documents.".

The **forward fix**, so this stops being archaeology: write drafts through
`simple_history.utils.bulk_update_with_history(...)` instead of the bare queryset
`.update()`, which makes every future draft a labelled history row.

## Layout
Mirrors `real_world/output/` — outputs bucketed by purpose; all scripts stay tracked in the
package root (`output/` is gitignored except `.gitkeep`, so they live here, not under it).
```
gold_standard/
  edit_analysis.py   # pure logic (draft detection + cosine edit-typing); unit-tested
  audit.py           # read-only orchestrator; exposes run() (--no-cosine skips embeddings)
  enrich.py          # local pass: fill cosine + edit type for a --no-cosine CSV
  status_table.py    # per-assay coverage table (md/html/png) from the latest typed gold
  bakeoff.py         # score cross-provider models vs gold (cosine + abstention agreement)
  edit_report.py     # edit analysis: # + type of scientist edits (md + bar chart)
  freeze_benchmark.py # freeze the file-backed iterable core + manifest + DATASHEET
  DATASHEET.md       # PII-free dataset card for the iterable core (tracked)
  README.md
  output/            # gitignored except .gitkeep (gold answers + reviewer emails = PII)
    _analysis/       #   data CSVs: gold_answers_typed_*, ground_truth_assessment_*,
                     #   benchmark_core, benchmark_manifest, bakeoff_*
    _plotting/       #   figures: gold_status_table.{md,html,png}, bakeoff.{html,png}, edit_types
    _embeddings/     #   SHA-cached vectors (reproducible, cheap re-runs)
# commands: extract_gold_answers (prod, read-only) · enrich_gold_cosines (local, typing)
#           · assess_ground_truth (sufficiency). status_table/bakeoff run as scripts.
# scripts glob the newest output/_analysis/gold_answers_typed_*.csv as the gold.
# tests: toxtempass/tests/test_gold_standard_edit_analysis.py
```
