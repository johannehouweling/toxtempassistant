# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

ToxTempAssistant is a Django 6 web application that uses LLMs to help users draft annotated toxicity test method templates ("ToxTemp", per ALTEX 36.4 (2019), GD211). Users upload assay-related documents (PDFs, images, DOCX, etc.); the LLM extracts answers to a structured questionnaire (Section → Subsection → Question → Answer), which the user then edits, accepts, and exports.

Dependencies are managed with Poetry; the project ships as a Docker Compose stack (Django + Postgres + MinIO + nginx + a backup scheduler).

## Repository layout

```
myocyte/                  ← Django project root (`manage.py` lives here)
  myocyte/                ← Django settings package (settings.py, urls.py, wsgi.py)
  toxtempass/             ← The single Django app — most application code
    models.py             ← Person/Investigation/Study/Assay/Question/Answer/Workspace/LLMConfig
    views.py              ← All HTTP endpoints (function- + class-based)
    llm.py                ← LLM client resolution (Azure / OpenAI / Anthropic)
    azure_registry.py     ← Auto-discovery of AZURE_E*_* env vars at startup
    filehandling.py       ← Upload validation + text/image extraction (pypdf, unstructured, OCR)
    export.py             ← Pandoc-based export to PDF/DOCX/HTML/MD/XML/JSON
    tasks.py              ← django-q2 async task wrappers (emails, etc.)
    signals.py            ← post_save (seed demo assay), post_delete (S3 object cleanup)
    demo.py               ← Seeds a read-only demo assay for new users
    evaluation/           ← Evaluation harness (positive_control, negative_control, real_world_files)
    management/commands/  ← `init_db`, `run_evals`, `test_llm_endpoints`
    tests/                ← Pytest test suite
    prompts/blocks/       ← LLM prompt fragments
    templates/toxtempass/ ← Django templates
  dockerfiles/            ← Per-service Dockerfiles (djangoapp, nginx, minio, backup-scheduler)
  django_startup.sh       ← Container entrypoint: waits for postgres, migrates, collectstatic, qcluster
docker-compose.yml        ← Profiles: `prod` and `test`
.env.dummy                ← Template; copy to `.env` and fill in
ToxTemp_v1.json           ← Question hierarchy seed (loaded via `/init/<label>` route or `init_db` cmd)
```

## Common commands

All commands assume Poetry is installed (`pipx install poetry`).

**Run tests** (must set `DJANGO_SETTINGS_MODULE` and run from `myocyte/`):
```bash
cd myocyte
DJANGO_SETTINGS_MODULE=myocyte.settings poetry run pytest
```

Run a single test file or test:
```bash
DJANGO_SETTINGS_MODULE=myocyte.settings PYTHONPATH=myocyte poetry run pytest myocyte/toxtempass/tests/test_workspace.py -v
DJANGO_SETTINGS_MODULE=myocyte.settings PYTHONPATH=myocyte poetry run pytest myocyte/toxtempass/tests/test_workspace.py::TestWorkspaceMemberAccess -v
```

Note: `pyproject.toml` declares `DJANGO_SETTINGS_MODULE = "toxtempass.myocyte.settings"` for running from the repo root; running from `myocyte/` requires `myocyte.settings`. CI runs tests inside the Docker image with `myocyte.settings`.

**Lint / format**:
```bash
poetry run ruff check .
poetry run ruff check . --fix
```
Ruff config is in `pyproject.toml`: line length 90, target py310, selects `E,F,W,T,ANN,D,DJ,I,S`. Migrations and `*/validation/*` are excluded; tests skip `S/D/ANN`.

**Pre-commit hooks** (ruff, commitizen on `commit-msg`, detect-secrets against `.secrets.baseline`):
```bash
pre-commit install
```

**Run the full stack locally** (BuildKit required — Docker 23.0+ / Compose v2):
```bash
cp .env.dummy .env   # then fill in real values
docker compose -f docker-compose.yml --profile prod up
```
The `test` profile uses an ephemeral Postgres (`postgres_test_for_django`) with no volume.

**Management commands** (run inside the container or with `poetry run`):
```bash
docker compose exec djangoapp python manage.py init_db --label v1            # seed QuestionSet from ToxTemp_<label>.json
docker compose exec djangoapp python manage.py test_llm_endpoints [--save]   # ping every Azure deployment
docker compose exec djangoapp python manage.py run_evals --experiment X      # evaluation pipeline
```

**Releases**: `python-semantic-release` drives versioning from Conventional Commits on `main`; the version lives in `pyproject.toml:project.version`. Use `feat:`/`fix:`/`docs:` etc. prefixes — commitizen enforces this on `commit-msg`.

## Architecture notes

### LLM resolution chain

`toxtempass.llm.get_llm()` and `resolve_user_llm(user)` resolve which model client to instantiate at request time. The order is:

1. **User preference** — `user.preferences["llm_model"]` (a `"endpoint_index:tag"` string), if still in the admin allowlist and not retired.
2. **Admin default** — `LLMConfig.default_model` (singleton row, pk=1, managed at `/admin/toxtempass/llmconfig/`).
3. **Env-tagged default** — the Azure deployment whose `.env` tag string contains `default:true`.
4. **Legacy fallback** — `OPENAI_API_KEY` env var (`toxtempass/__init__.py` resolves it into `LLM_ENDPOINT` / `LLM_API_KEY` at import time). Deprecated; prefer Azure AI Foundry credentials.

Per-deployment clients are cached with `@lru_cache(maxsize=32)` in `get_llm_for_endpoint()`. The cache key is `(endpoint_index, model_tag, temperature)`. The function dispatches by the deployment's `api:` tag:

| `api:` tag | Class used |
|---|---|
| `openai` | `langchain_openai.ChatOpenAI` (generic OpenAI-compat) |
| `azure-openai` | `langchain_openai.AzureChatOpenAI` (requires `AZURE_E<n>_API_VERSION`) |
| `foundry` | `langchain_azure_ai.chat_models.AzureAIChatCompletionsModel` |
| `anthropic` | `langchain_anthropic.ChatAnthropic` |

Reasoning models (o1/o3/o4/o5/gpt-5*) reject custom temperature; `_is_reasoning_model()` forces temperature=1 for those.

`current_llm_key(user)` snapshots the user's resolved deployment at queue time so a worker uses the same model when the task fires later (regardless of preference changes in between).

### Azure deployment auto-discovery

`toxtempass/azure_registry.py` parses env vars at startup. Convention:

- `AZURE_E<n>_ENDPOINT`, `AZURE_E<n>_KEY`, optionally `AZURE_E<n>_API_VERSION`
- Per-model triple: `AZURE_E<n>_DEPLOY_<TAG>`, `AZURE_E<n>_MODEL_<TAG>`, `AZURE_E<n>_TAGS_<TAG>` (comma-separated `key:value` pairs).

Recognized tag keys include `tier`, `residency`, `provider`, `direct-from-azure`, `version`, `api`, `retirement-date`, `default`. The admin panel surfaces these as privacy badges and retirement countdowns. To retire a model: add `retirement-date:YYYY-MM-DD` to its tag list (soft retire) or delete the three env lines (hard remove).

### Permission model

Object-level permissions go through `django-guardian`. The abstract `AccessibleModel` (in `models.py`) implements recursive `is_accessible_by(user, perm_prefix)` — if the user lacks direct permission, it walks `get_parent()` (Assay → Study → Investigation). `Assay.is_accessible_by` additionally checks `WorkspaceInvestigation` membership: if the parent investigation is shared into a workspace the user belongs to, the assay is accessible — but `delete` is restricted to the assay creator or the investigation owner.

`Person.preferences` is a JSONField holding multiple semantically distinct keys (beta state, tour progress, llm pref). All writes must go through `utilities.update_prefs_atomic(user, mutate)`, which serialises with `SELECT FOR UPDATE` — the naive read-modify-write pattern races and clobbers other keys.

### Workspace ownership model

Workspaces are a collaboration mechanism and **do not transfer ownership**. The
ownership chain is always rooted at the `Investigation.owner` field and never
changes when an investigation is shared into a workspace.

Key rules to observe when working in this area:

* **Investigation ownership is permanent.** `Investigation.save()` always grants
  the owner `view_investigation` / `change_investigation` / `delete_investigation`
  guardian permissions. These are *baseline* permissions and must **not** be
  revoked by any workspace lifecycle operation — including workspace deletion,
  investigation removal (`remove_workspace_assay`), and member removal
  (`remove_workspace_member` / `remove_workspace_member_by_email`).

* **The owner check is universal.** Whenever any workspace operation would call
  `remove_perm("view_investigation", user, investigation)`, first check
  `user.id == investigation.owner_id`. If true, skip the revocation — the perm
  is baseline, not workspace-derived, and must be preserved regardless of which
  user's role is being acted upon (including the workspace OWNER themselves).

* **Workspace access is additive.** Adding an investigation to a workspace
  (`add_workspace_assay`) grants `view_investigation` to **all** current
  workspace members, including the workspace owner. Adding a member
  (`add_workspace_member`) grants them `view_investigation` for all
  investigations already shared into the workspace.

* **Removing the sharing link revokes the derived perm** — with two exceptions:
  1. The user is the *owner* of that investigation (baseline perm, never revoke).
  2. The user still has the same investigation shared through a *different*
     workspace they belong to (retained via cross-workspace access).

  This rule applies equally to:
  - `delete_workspace` — all members (including workspace OWNER) may lose perms
  - `remove_workspace_assay` — all members may lose perm for the removed investigation
  - `remove_workspace_member` / `remove_workspace_member_by_email` — the removed
    user may lose perms for all investigations in that workspace

* **Studies and Assays created inside a shared investigation are owned by their
  creator** (`created_by` FK), not by the investigation owner. However, **access
  to those objects is gated entirely by the investigation's sharing status.** If
  a workspace is dissolved and a member no longer has access to the parent
  investigation, they also lose access to every Study/Assay they created inside
  it. This is by design — the investigation is the root that defines the access
  boundary.

* **`delete_workspace` cleanup order (runs inside `transaction.atomic()`):**
  1. Snapshot all `WorkspaceMember` + `WorkspaceInvestigation` rows.
  2. For each member × shared investigation: skip if the member owns
     the investigation *or* still has access via another workspace; otherwise
     revoke `view_investigation`.
  3. Call `workspace.delete()` (CASCADE removes members + investigations rows).

  Note: the workspace OWNER is included in step 2 because they can also hold
  workspace-derived perms (e.g. another member shared their own investigation
  into the workspace). Their baseline perm on *their own* investigations is
  protected by the owner check in step 2.

### Async tasks

`django-q2` runs in-process via `manage.py qcluster` (started by `django_startup.sh` unless `TESTING=true`). The cluster uses the Django ORM as its broker (`Q_CLUSTER["orm"] = "default"`). When `DEBUG` or `TESTING` is true, `Q_CLUSTER["sync"] = True` so tasks execute inline. Email is the primary task type today (`tasks.queue_email`).

### File storage

All user-uploaded files are stored in **MinIO** (S3-compatible object storage) via `django-storages` with the S3 backend (env: `AWS_S3_ENDPOINT_URL`, `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — required at import time in `settings.py`). There is **no local media folder**; do not use `MEDIA_ROOT`/`MEDIA_URL` or Django's default file upload storage — all uploads must go through the `FileAsset` model and `store_files_to_storage()` in `filehandling.py`. The `FileAsset` model owns the object lifecycle; a `post_delete` signal (`signals.delete_object_from_storage`) removes the underlying object from MinIO when the DB row is deleted. Allowed MIME types and file extensions are gated centrally in `Config.ALLOWED_MIME_TYPES`, `IMAGE_ACCEPT_FILES`, `TEXT_ACCEPT_FILES` (immutable; runtime mutation is blocked).

Export artefacts (PDF, DOCX, etc.) are generated on-the-fly using a `tempfile.TemporaryDirectory` and served directly from memory — they are never written to permanent disk storage.

### Export pipeline

`export.py` uses Pandoc (installed in the djangoapp Dockerfile) for PDF/DOCX/HTML/MD/XML; JSON is serialized inline. The trusted Pandoc options live in `Config.EXPORT_MAPPING` (read-only `MappingProxyType`) — adding a new export type means adding entries to both `EXPORT_MAPPING` and `EXPORT_MIME_SUFFIX` and nowhere else.

### User onboarding tour

Selectors and help text for the in-app tour live in `Config.user_onboarding_help`, keyed by URL name (`overview`, `add_new`, `create_assay`, `answer_assay_questions`).

### Error/status surfacing

`Assay.status_context` accumulates user-visible error/info messages; `utilities.add_status_context()` truncates to `Config.status_error_max_len` while preserving the most recent entry. Full tracebacks go to the rotating file handler at `myocyte/logs/django-errors.log` (volume-mounted; survives container rebuilds). Correlation IDs in `status_context` point back to entries in that log.

## Environment variables

Required for any run (see `.env.dummy` for the full list): `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `AWS_*` (MinIO), and the `SMTP_*` settings used by Django email. `USE_POSTGRES=true` switches from SQLite to Postgres; if `USE_POSTGRES=true` and `TESTING=true`, `POSTGRES_HOST` must equal `postgres_test_for_django` (settings.py raises otherwise). Azure AI Foundry credentials (`AZURE_E<n>_ENDPOINT`, `AZURE_E<n>_KEY`) are required for non-test runs.

## Conventions to honour

- **Conventional Commits** are enforced on `commit-msg` by commitizen — releases depend on this.
- The `.secrets.baseline` is checked by `detect-secrets`; if you add an intentional fixture-like secret, regenerate the baseline rather than disabling the hook.
- Don't run `git add .` blindly — `.env`, `db.sqlite3`, and `backups/` live in the tree and are gitignored, but new untracked artifacts can slip in.
- the central location for app specific constants are registered in /myocyte/toxtempass/__init__.py Config function.
- Avoid using custom CSS defintions. Use bootstrap5 boiler plate classes whenever possible. For example use `fs-1` to `fs-7` instead of changing font-size as CSS. 
