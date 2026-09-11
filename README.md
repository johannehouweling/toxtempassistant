# ToxTempAssistant 
LLM based web-app to assist users in drafting an annotated toxicity test method template (ToxTemp).

ToxTemp "was developed (i) to fulfill all requirements of GD211, (ii) to guide the user concerning the types of answers and detail of information required, (iii) >to include acceptance criteria for test elements, and (iv) to define the cells sufficiently and transparently." [1]

## TOC
- [ToxTempAssistant](#toxtempassistant)
  - [TOC](#toc)
  - [Spin up server with docker](#spin-up-server-with-docker)
    - [Azure AI Foundry — add a model](#azure-ai-foundry--add-a-model)
    - [Get ORCID iD credentials](#get-orcid-id-credentials)
    - [Create Certificate](#create-certificate)
    - [MinIO setup](#minio-setup)
  - [Backup architecture](#backup-architecture)
  - [Production deployment](#production-deployment)
  - [Workspaces and data ownership](#workspaces-and-data-ownership)
  - [License](#license)
  - [Maintainer](#maintainer)
  - [How to cite](#how-to-cite)
  - [References](#references)
  - [Contribute](#contribute)
    - [Poetry for Dependency Management](#poetry-for-dependency-management)
    - [Running Tests with Pytest](#running-tests-with-pytest)
    - [Ruff for Linting](#ruff-for-linting)
    - [Conventional Commits](#conventional-commits)
    - [Git Pre-Commit Hooks](#git-pre-commit-hooks)
    - [Pull Requests (PRs)](#pull-requests-prs)

## Spin up server with docker
We work with a `.env` file to store mission critical information and setups. These need to be set to match your local environment. In addition, please revise `myocyte/dockerfiles/nginx/nginx.conf` to the settings needed for your specific setup.

Modify and rename the '.env.dummy'-file to `.env` in same path as the `docker-compose.yml` with configuration data on server

- `DEBUG` settitng for django should be False for production
- `SECRET_KEY` for django, salt for pw hashes
- `ORCID_CLIENT_ID` and `ORCID_CLIENT_SECRECT` to facilitate login via ORCID (see below for details)
- `ALLOWED_HOSTS` URI of the app, and IP address of server, potentaially also furhter aliases
- `POSTGRES_HOST` IP address of dedicated Postgres server if available, otherwise postgres_for_django to use postgres included in docker compose (obviously, the postgres server can be taken out of the docker compose if an external server is used)
- `POSTGRES_PORT` Port of Postgres Server, usually 5432, also use 5432 for docker compose postgres
- `POSTGRES_USER` Postgres User, default 'postgres'
- `POSTGRES_PASSWORD` Password for user, needs to be set using psql (see below)
- `POSTGRES_DB` Database name for django to use, also postgres user needs to be granted permissions to said db (see below)
- `MINIO_ROOT_USER` MinIO *admin console* username (root/admin account).
- `MINIO_ROOT_PASSWORD` MinIO *admin console* password (root/admin account

The easier way to spin up the server is by using our docker compose file, if you are using an external PostGres Server, it is best to remove the postgres portion and its network from the docker-file. 
```bash
docker compose -f docker-compose.yml up
```

> **Note — BuildKit required.** The `djangoapp` image uses BuildKit-only
> features (cache mounts, `# syntax=` frontend pin) for faster rebuilds.
> BuildKit is the default in Docker 23.0+ / Compose v2. On older engines,
> enable it explicitly before running `docker compose build/up`:
>
> ```bash
> export DOCKER_BUILDKIT=1
> export COMPOSE_DOCKER_CLI_BUILD=1
> ```

### Azure AI Foundry — add a model

ToxTempAssistant serves models from Azure AI Foundry (Azure OpenAI, Mistral,
Anthropic, Moonshot/Kimi, etc.). Models are auto-discovered from the `.env` file at startup —
no code changes are required to add, remove, or retire a deployment.

#### Env-var convention

Each endpoint is numbered `E1`, `E2`, ... and carries three parts:

1. **Endpoint address + key** (once per endpoint):
   ```
   AZURE_E<n>_ENDPOINT=<base URL of the Azure resource>
   AZURE_E<n>_KEY=<API key>
   AZURE_E<n>_API_VERSION=<only for Azure OpenAI resources>
   ```
2. **Model deployment** (one triple per model on the endpoint):
   ```
   AZURE_E<n>_DEPLOY_<TAG>=<Azure deployment name>
   AZURE_E<n>_MODEL_<TAG>=<underlying model id>
   AZURE_E<n>_TAGS_<TAG>=<comma-separated key:value metadata>
   ```
3. **Metadata tags** are written as `key:value` pairs separated by commas. The
   following keys are recognised:

   | key | purpose | example |
   |---|---|---|
   | `tier` | Azure deployment tier | `regional` · `datazone` · `global` · `batch` |
   | `residency` | where data is actually processed | `eu` · `us` · `global` |
   | `provider` | who built the model | `openai` · `anthropic` · `mistral` · `moonshot` |
   | `direct-from-azure` | `true` = Microsoft-operated; `false` = third-party MaaS | `true` · `false` |
   | `version` | model version string | `2024-07-18` |
   | `api` | wire protocol | `openai` · `azure-openai` · `anthropic` · `foundry` |
   | `retirement-date` | ISO date when Azure deprecates the deployment | `2026-10-01` |
   | `default` | marks the bootstrap default used when no admin choice exists | `true` |

   The admin UI at `/admin/toxtempass/llmconfig/` uses these tags to render
   privacy badges, retirement warnings, and health-check results.

#### Picking the right `api:` tag

Azure Foundry serves different model families at different URL shapes:

| URL shape you see in the Azure portal | `api:` value |
|---|---|
| `…services.ai.azure.com/openai/v1/` | `openai` |
| `…cognitiveservices.azure.com/openai/deployments/<dep>/…?api-version=…` | `azure-openai` (needs `AZURE_E<n>_API_VERSION`) |
| `…services.ai.azure.com/models/chat/completions?api-version=…` | `openai` (OpenAI-compatible passthrough; the `?api-version=…` goes in the endpoint URL) |
| `…services.ai.azure.com/anthropic/…` | `anthropic` (strip the trailing `/v1/messages`) |

#### Example: add Azure OpenAI `gpt-4o-mini`

From Azure OpenAI Studio → Deployments → your deployment, copy the endpoint URL:

```
https://toxtempass-foundry.cognitiveservices.azure.com/openai/deployments/gpt-4o-mini/chat/completions?api-version=2025-01-01-preview
```

Then in `.env`:

```
AZURE_E1_ENDPOINT=https://toxtempass-foundry.cognitiveservices.azure.com/
AZURE_E1_API_VERSION=2025-01-01-preview
AZURE_E1_KEY=<your-key>

AZURE_E1_DEPLOY_GPT4OMINI=gpt-4o-mini
AZURE_E1_MODEL_GPT4OMINI=gpt-4o-mini
AZURE_E1_TAGS_GPT4OMINI=tier:datazone,residency:eu,provider:openai,direct-from-azure:true,version:2024-07-18,api:azure-openai,retirement-date:2026-10-01,default:true
```

`default:true` makes this deployment the fallback the app boots with when no
`LLMConfig` DB row exists yet (e.g. fresh deploys and CI). If more than one
model carries `default:true`, the first one wins and a warning is logged.

#### Verifying the wiring

The `test_llm_endpoints` management command pings every discovered deployment
with a trivial prompt and reports latency / errors. Add `--save` to persist the
results to `LLMConfig.last_health_check` so they show up in the admin panel.

```bash
docker compose exec djangoapp python manage.py test_llm_endpoints
docker compose exec djangoapp python manage.py test_llm_endpoints --only GPT4OMINI
docker compose exec djangoapp python manage.py test_llm_endpoints --save
```

The admin panel at `/admin/toxtempass/llmconfig/` also has a **Run health check
now** button that triggers the same checks synchronously and renders results in
a table alongside the data-handling (privacy) badges.

#### Removing / retiring a model

- **Soft retire**: add `retirement-date:YYYY-MM-DD` to the tag list. The admin
  table shows a countdown (⏳) when within 30 days, then marks the row greyed
  out and unselectable (☠️) once the date has passed. The model is also hidden
  from the user-facing picker automatically.
- **Hard remove**: delete the three `AZURE_E<n>_DEPLOY_<TAG>` /
  `AZURE_E<n>_MODEL_<TAG>` / `AZURE_E<n>_TAGS_<TAG>` lines from `.env` and
  restart the container.

### Get ORCID iD credentials
To obtain ORCID iD and secret perform the following steps:
- login to personal or institutional orcid
- then click on user-settings -> Developper Tools 
- Confirm Terms of Servicen and click 'Register for your ORCID Public API credentials'
- Fill in Application Name, Application URL, Application Description and Redirect URIs
- Application Name: ToxTempAssistant
- Application URL: URL e.g. https://toxtempass.mainlvldomain.nl
- Application Description (suggestion): ToxTemp, "an annotated toxicity test method template was developed (i) to fulfill all requirements of GD211, (ii) to guide the user concerning the types of answers and detail of information required, (iii) >to include acceptance criteria for test elements, and (iv) to define the cells sufficiently and transparently." (doi.org/10.14573/altex.1909271)
- Redirect URI: URL/orcid/callback/ e.g. https://toxtempass.mailvldomain.nl/orcid/callback/
   
### Create Certificate
Required for orcid login and general privacy considerations, it is advised to setup https. To this end a certificate is required. Create a Certificate Signing Request and send it to Certifying Authority, your institution should have someone. 
See this article, which also has some details on making the certificiate work with nginx: https://www.digitalocean.com/community/tutorials/how-to-create-a-self-signed-ssl-certificate-for-nginx-in-ubuntu-20-04-1

### Storage architecture

The app uses two storage tiers:

| Tier | What goes here | Backed up? |
|------|---------------|------------|
| **MinIO (S3)** | User-uploaded files (PDFs, images, DOCX, etc.) stored as `FileAsset` objects — **persistent** | Yes — via `backup.sh` MinIO mirror |
| **Temp directory** | Export artefacts generated on-the-fly (PDF, DOCX, JSON, …) — **ephemeral**, served from memory and discarded after each request | No — nothing to back up |

There is **no Django media folder**. `MEDIA_ROOT`/`MEDIA_URL` are not defined. All persistent uploads must go through `FileAsset` and `store_files_to_storage()` in `filehandling.py`.

### MinIO setup
MinIO provides local S3-compatible object storage for the app.

- Set `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` in `.env` for the MinIO admin account.
- Set `MINIO_DJANGO_USER` and `MINIO_DJANGO_PASSWORD` in `.env` for the application access keys.
- Start the stack with `docker compose -f docker-compose.yml up` and open the MinIO console at `http://127.0.0.1:9001`.
- Log in with the root credentials, create a user for Django with the access keys above, and create the bucket(s) needed by your deployment.
- The MinIO API is available inside the Docker network on port `9000`; only the console is exposed to the host.

## Backup architecture

ToxTempAssistant ships a `backup` Docker Compose service that automatically backs up both the Postgres database and MinIO object storage on a configurable cron schedule. Backups are written to a directory on the host machine and older ones are pruned automatically.

### How it works

```
┌──────────────────────────────────────────────┐
│  backup service (alpine + supercronic)        │
│                                               │
│  entrypoint.sh                                │
│    └─ installs cron job (BACKUP_SCHEDULE)     │
│         └─ runs backup.sh on schedule         │
│              ├─ pg_dump → gzip → .sql.gz      │
│              └─ mc mirror → MinIO objects     │
└──────────────────────────────────────────────┘
         │ bind-mounted
         ▼
   ./backups/<YYYY-MM-DD_HHMMSS>/
     ├── postgres_<db>.sql.gz   (logical Postgres dump)
     ├── minio/                 (object-level MinIO mirror)
     │     └── <bucket>/…
     ├── manifest.txt           (run metadata)
     └── files.txt              (directory listing)
```

The `backup` service:

1. Reads your `.env` at startup (mounted read-only at `/work/.env`).
2. Installs a `supercronic` cron job using `BACKUP_SCHEDULE`.
3. On each run, `backup.sh`:
   - Executes `pg_dump` inside the running Postgres container and pipes the output through `gzip`.
   - Spawns a short-lived `minio/mc` container on the same Docker network to mirror all buckets (or a specific bucket) into the backup directory.
   - Writes a `manifest.txt` (timestamp, services, config values) and a `files.txt` (directory listing).
   - Deletes timestamped backup directories older than `BACKUP_RETENTION_DAYS`.

### Enabling the backup service

The `backup` service is part of the `prod` profile and starts automatically when you bring up the production stack:

```bash
docker compose -f docker-compose.yml --profile prod up -d
```

The `backup` service requires the Docker socket and the host `.env` to be mounted (see `docker-compose.yml`). No additional setup is needed — the service discovers all Postgres and MinIO settings from the same `.env` file used by the rest of the stack.

### `.env` configuration reference

The backup system re-uses existing Postgres and MinIO keys from `.env` — no additional entries are required for a basic setup. The optional keys below let you tune the behaviour.

#### Required (already set for the main stack)

| Variable | Description |
|---|---|
| `POSTGRES_USER` | Postgres superuser used by `pg_dump` |
| `POSTGRES_PASSWORD` | Password for that user |
| `POSTGRES_DB` | Database name to dump |
| `POSTGRES_HOST` | Docker Compose service name for Postgres (e.g. `postgres_for_django`) |
| `AWS_S3_ENDPOINT_URL` | MinIO endpoint **using the service name as host** (e.g. `http://minio:9000`) |
| `MINIO_ROOT_USER` | MinIO root / admin username |
| `MINIO_ROOT_PASSWORD` | MinIO root / admin password |

#### Optional backup-specific keys

| Variable | Default | Description |
|---|---|---|
| `BACKUP_ROOT` | `backups` (next to `backup.sh`) | Host-side directory where timestamped backup folders are written. A relative path is resolved relative to the directory that contains `backup.sh` (i.e. the repo root). An absolute path is used as-is. |
| `BACKUP_RETENTION_DAYS` | `14` | Timestamped backup directories older than this many days are deleted at the end of each run. |
| `MINIO_BUCKET` | *(empty — all buckets)* | Set to a single bucket name to limit MinIO mirroring to that bucket only. Leave empty to mirror all buckets. |

#### Setting the backup schedule (`BACKUP_SCHEDULE`)

`BACKUP_SCHEDULE` is a standard cron expression consumed by the `backup` container's `entrypoint.sh`. The default is `0 2 * * *` (every day at 02:00 local time).

Set it via the `environment` section in `docker-compose.yml` or by exporting it before bringing up the stack:

```yaml
# docker-compose.yml  (backup service, environment block)
environment:
  TZ: Europe/Amsterdam       # affects cron schedule interpretation
  BACKUP_SCHEDULE: "0 2 * * *"   # daily at 02:00 Amsterdam time
  BACKUP_CMD: "/work/backup.sh"
  CRON_LOG: "/logs/backup-cron.log"
```

Common schedule expressions:

| Expression | Meaning |
|---|---|
| `0 2 * * *` | Every day at 02:00 *(default)* |
| `0 */6 * * *` | Every 6 hours |
| `0 2 * * 0` | Every Sunday at 02:00 |
| `0 2 1 * *` | First day of every month at 02:00 |

> **Note — timezone:** `supercronic` uses the container's system clock. Set the `TZ` environment variable on the `backup` service to ensure the schedule fires at the intended wall-clock time (e.g. `TZ: Europe/Amsterdam` as shown in the example `docker-compose.yml` above).

### Backup directory layout

Each run creates a new subdirectory under `BACKUP_ROOT` named with a local-time timestamp (influenced by the container's `TZ` setting):

```
backups/
└── 2026-05-01_020001/
    ├── postgres_toxtempass.sql.gz   ← gzip-compressed logical Postgres dump
    ├── minio/
    │   └── toxtemp/                 ← mirrored MinIO bucket contents
    │       └── <object-key> …
    ├── manifest.txt                 ← run metadata (timestamp, services, config)
    └── files.txt                    ← directory listing of this backup
```

Backup directories that are older than `BACKUP_RETENTION_DAYS` days and whose names match the `YYYY-MM-DD_HHMMSS` pattern are deleted automatically at the end of each run. Directories with other names are never touched.

### Running a manual backup

To trigger a backup immediately without waiting for the next scheduled run:

```bash
# From the repo root (requires a running prod stack)
bash backup.sh
```

Or, if you want to run it inside the backup container:

```bash
docker compose exec backup /work/backup.sh
```

### Restore procedures

#### Restore Postgres

```bash
# Replace <STAMP>, <POSTGRES_DB>, <POSTGRES_USER>, and <POSTGRES_HOST> with your values
gunzip -c backups/<STAMP>/postgres_<POSTGRES_DB>.sql.gz \
  | docker compose exec -T <POSTGRES_HOST> psql -U <POSTGRES_USER> <POSTGRES_DB>
```

For example:

```bash
gunzip -c backups/2026-05-01_020001/postgres_toxtempass.sql.gz \
  | docker compose exec -T postgres_for_django psql -U postgres toxtempass
```

> **Warning:** This replaces the contents of the live database. Stop or fence off the Django app before restoring to avoid write conflicts.

#### Restore MinIO

Mirror the backup back to MinIO by setting the `mc` alias and performing the mirror in a single container invocation. Replace `<MINIO_ROOT_USER>`, `<MINIO_ROOT_PASSWORD>`, and `<STAMP>` with your actual values:

```bash
# Restore all buckets
docker run --rm \
  --network toxtempass_data_network \
  -v "$(pwd)/backups/<STAMP>/minio":/backup:ro \
  -e MINIO_USER=<MINIO_ROOT_USER> \
  -e MINIO_PASS=<MINIO_ROOT_PASSWORD> \
  --entrypoint /bin/sh \
  quay.io/minio/mc:latest \
  -lc '
    mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null
    mc mirror --overwrite /backup local/
  '

# Restore a single bucket
docker run --rm \
  --network toxtempass_data_network \
  -v "$(pwd)/backups/<STAMP>/minio/<bucket>":/backup:ro \
  -e MINIO_USER=<MINIO_ROOT_USER> \
  -e MINIO_PASS=<MINIO_ROOT_PASSWORD> \
  --entrypoint /bin/sh \
  quay.io/minio/mc:latest \
  -lc '
    mc alias set local http://minio:9000 "$MINIO_USER" "$MINIO_PASS" >/dev/null
    mc mirror --overwrite /backup local/<bucket>
  '
```

#### Verify a backup (without restoring)

```bash
# List Postgres dump tables
gunzip -c backups/<STAMP>/postgres_<DB>.sql.gz | grep -E '^(CREATE TABLE|COPY )'

# List MinIO backup objects
ls -lR backups/<STAMP>/minio/
```

## Production deployment

The app currently runs on **two production targets in parallel** during a migration:

- **Legacy** — single-host `docker compose` on `vhp-1` (`81.169.225.178`), with an external nginx serving HTTPS + `/static/*`. This is the canonical `https://toxtempassistant.vhp4safety.nl` until DNS cuts over.
- **Swarm** — Docker Swarm on the VHP4Safety Strato cluster (`tgx1`/`tgx2`), with Traefik for ingress + Let's Encrypt and GlusterFS for shared storage. Reachable today only via `/etc/hosts` override; cutover is a DNS-only flip.

For everything generic about the cluster — connecting via `ssh tgx1`/`ssh tgx2`, the `/mnt/gluster/docker/<service>/{data,config}/` convention, Traefik label patterns, the HiDrive backup-mirror cron, etc. — see the cluster docs at `/mnt/gluster/documentation/` (read on any swarm node):

- `README.md` — cluster topology, server inventory, quick links
- `operations/developer-workflow.md` — connecting, storage tiers, deploy patterns
- `operations/publishing-container-images.md` — registry conventions
- `operations/backup-strategy.md` + `operations/cronjobs.md` — `/mnt/gluster/backups/` → HiDrive sync
- `architecture/storage-strategy.md` and `architecture/tls-ha-architecture.md`
- `services/toxtempassistant.md` — service-registry entry for this app

Outstanding cutover work lives in `docs/cutover.md`.

### Service layout (stack `toxtempass`)

Five services across `docker-compose.yml` (single-host base) and `docker-stack.yml` (Swarm overlay):

| Service | Image | Replicas | Notes |
| --- | --- | --- | --- |
| `djangoapp` | `ghcr.io/johannehouweling/toxtempassistant:vX.Y.Z` | 1 | Gunicorn :8000, Traefik-exposed |
| `postgres_for_django` | `postgres:17-alpine` | 1 | Stateful — single replica with gluster failover |
| `minio` | `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | 1 | Single-node FS mode |
| `minio_init` | `ghcr.io/johannehouweling/toxtempassistant-minio-init:vX.Y.Z` | 1 one-shot | Creates bucket / user / policy on each deploy, then exits |
| `backup` | `ghcr.io/johannehouweling/toxtempassistant-backup:vX.Y.Z` | 1 | Supercronic; writes to `/mnt/gluster/backups/toxtempassistant/` (cluster cron mirrors that to HiDrive) |

### App-specific paths on gluster

```
/mnt/gluster/docker/toxtempassistant/
├── data/
│   ├── db_data/              # postgres /var/lib/postgresql/data
│   ├── minio_blob_data/      # minio /data
│   └── myocyte/
│       ├── logs/             # gunicorn-*.log + backup-cron.log
│       └── static/           # collected static
└── config/
    └── .env                  # mode 600, owned by jhouweling

/mnt/gluster/backups/toxtempassistant/   # postgres dumps + minio mirrors
```

### CI/CD chain

A push to `main` self-drives the full release chain:

```
git push (feat:/fix:)
  └─ CI                       → pytest in djangoapp container
       └─ release.yml          → semantic-release tags vX.Y.Z (PAT-credentialed push)
            └─ tag push triggers publish-image.yml
                 ├─ matrix builds 3 images (djangoapp + backup + minio_init)
                 └─ release job creates GitHub Release (gates deploy)
                      └─ release-published triggers deploy.yml
                           ├─ deploy-legacy → SSH to vhp-1, `compose pull && up -d`
                           └─ deploy-swarm  → SSH to tgx1, render with `compose config |
                                              python-strip-profiles | docker stack deploy`
```

Workflow files:

- `.github/workflows/ci.yml` — tests; skips on bot-commits and on workflow-only/markdown-only pushes
- `.github/workflows/release.yml` — `semantic-release version`; uses PAT so tag pushes trigger downstream workflows
- `.github/workflows/publish-image.yml` — matrix builds + GH Release (gates deploy)
- `.github/workflows/deploy.yml` — `deploy-legacy` (release events only) + `deploy-swarm` (release **and** workflow_dispatch). Both jobs wait for the djangoapp container to report `healthy` (via the in-container `HEALTHCHECK` defined in `docker-compose.yml`, which curls gunicorn end-to-end) before being marked successful. Swarm runs remain non-blocking on release events during cutover, while `workflow_dispatch` runs fail loudly.

### `workflow_dispatch` for ad-hoc swarm deploys

Repo → Actions → **Deploy** → Run workflow → branch `main` → tag input = an existing release tag. Use this to:

- Re-deploy after editing `.env` on gluster (env edits don't auto-trigger redeploy)
- Smoke-test deploy.yml changes without cutting a real release
- Recover from a transient failure

`deploy-legacy` is skipped on dispatch — no risk of accidentally re-deploying legacy.

### App-specific configuration values

- **GitHub `legacy-server` environment**: vars `REMOTE_SERVER_ADDRESS`/`REMOTE_SERVER_USERNAME`/`REMOTE_SERVER_PATH`, secrets `PA_TOKEN`/`GIT_ACTIONS`.
- **GitHub `tgx1-server` environment**: vars `TGX1_IP_ADDRESS`/`TGX1_USERNAME`, secret `TGX1_DEPLOY_KEY` (private key whose pubkey is in `/mnt/gluster/ssh-keys/jhouweling/authorized_keys`).
- **Repo-level `PA_TOKEN`** secret — Personal Access Token with `repo` scope. Used by `release.yml` to push tags so downstream workflows fire.

### Cluster prep (one-time)

```bash
mkdir -p /mnt/gluster/docker/toxtempassistant/data/{db_data,minio_blob_data}
mkdir -p /mnt/gluster/docker/toxtempassistant/data/myocyte/{logs,static}
mkdir -p /mnt/gluster/docker/toxtempassistant/config
mkdir -p /mnt/gluster/backups/toxtempassistant
```

`.env` lives at `/mnt/gluster/docker/toxtempassistant/config/.env` (mode 600). Pipe legacy's `.env` straight onto gluster (legacy can't reach tgx1 directly — laptop relay):

```bash
ssh vhp-1 'cat ~/ToxTempAssistant/.env' | \
  ssh tgx1 'cat > /mnt/gluster/docker/toxtempassistant/config/.env && chmod 600 /mnt/gluster/docker/toxtempassistant/config/.env'
```

### Data hydration (legacy → swarm)

Run on a fresh swarm where postgres is empty. Three steps.

**1. Pull the most recent legacy backup tarball through your laptop to tgx1:**

```bash
TS=$(ssh vhp-1 'ls -t ~/ToxTempAssistant/backups | head -1')
ssh vhp-1 "cd ~/ToxTempAssistant/backups/$TS && tar c ." | \
  ssh tgx1 "mkdir -p /tmp/restore/$TS && cd /tmp/restore/$TS && tar x"
```

**2. Restore minio objects via mc mirror.** On tgx1 write the helper, then run it:

```bash
cat > /tmp/mirror.sh << 'EOF'
set -e
mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mirror --overwrite --preserve /source/"$BUCKET"/ local/"$BUCKET"/
mc ls local/"$BUCKET" | head
EOF

TS=$(ls -t /tmp/restore | head -1)
set -a; source /mnt/gluster/docker/toxtempassistant/config/.env; set +a
docker run --rm --network toxtempass_data_network -v /tmp/restore/$TS/minio:/source:ro -v /tmp/mirror.sh:/mirror.sh:ro -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD -e BUCKET=$AWS_STORAGE_BUCKET_NAME --entrypoint sh quay.io/minio/mc:latest /mirror.sh
```

**3. Restore postgres dump:**

```bash
TS=$(ls -t /tmp/restore | head -1)
set -a; source /mnt/gluster/docker/toxtempassistant/config/.env; set +a
gunzip -c /tmp/restore/$TS/postgres_*.sql.gz | docker run --rm -i --network toxtempass_db_network -e PGPASSWORD="$POSTGRES_PASSWORD" postgres:17-alpine psql -h postgres_for_django -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

**Resetting state when creds drift:** postgres / minio only read root credentials on first init. If `.env` changes after the data dirs are populated, auth fails. Wipe and reinit:

```bash
docker service scale toxtempass_postgres_for_django=0
docker run --rm -v /mnt/gluster/docker/toxtempassistant/data/db_data:/dest busybox sh -c 'rm -rf /dest/* /dest/.[!.]*'
docker service scale toxtempass_postgres_for_django=1
# Same pattern for toxtempass_minio + minio_blob_data; force-update minio_init after.
```

### Operations

**Bouncing for env changes** — `.env` edits don't auto-redeploy. Trigger via **GitHub UI** → Actions → Deploy → Run workflow → tag input → Run. (Avoid `docker service update --force` for env changes — it doesn't re-render the spec from `.env`.)

**Smoke-testing without DNS** — from your laptop:

```bash
sudo sh -c 'echo "81.169.246.233 toxtempassistant.vhp4safety.nl" >> /etc/hosts'
# Visit https://toxtempassistant.vhp4safety.nl, accept the self-signed cert.
# Don't forget to remove the override afterwards.
```

To bypass Traefik and hit djangoapp directly over the overlay (debugging):

```bash
docker run --rm --network core busybox wget -qO- http://djangoapp:8000/ | head
```

**Manually triggering a backup** (cron default is `0 2 * * *` Europe/Amsterdam):

```bash
docker exec $(docker ps -q -f label=com.docker.swarm.service.name=toxtempass_backup | head -1) /usr/local/bin/backup.sh
```

Run on the node where backup landed — `docker service ps toxtempass_backup --no-trunc` shows which node. Output goes to `/mnt/gluster/backups/toxtempassistant/<timestamp>/`.

Cron stdout/stderr at `/mnt/gluster/docker/toxtempassistant/data/myocyte/logs/backup-cron.log`.

**Scaling** — only `djangoapp` is safe to scale beyond 1; `postgres_for_django`, `minio`, and `backup` must stay singleton.

```bash
docker service scale toxtempass_djangoapp=3
```

### Known caveats

- **`backup` uses `/var/run/docker.sock`** — single-node-coupled. If `backup` and `postgres_for_django` land on different swarm nodes, `docker exec postgres_for_django` from inside backup won't find postgres. Stopgap: pin both via `node.labels.storage == true` (commented placement blocks already in `docker-stack.yml`). Real fix (post-cutover): refactor `backup.sh` to use TCP (`pg_dump -h postgres_for_django`) and run minio mirror as a sibling task.
- **GlusterFS POSIX locking quirks** — postgres uses file locks; rare network partitions can produce lock-contention errors. Recovery: scale postgres to 0, wait, scale back to 1.
- **Compose-vs-stack-deploy compatibility layer** — the `compose config | python-strip-profiles | stack deploy` pipeline papers over schema differences. Intentionally temporary; remove after cutover (see `docs/cutover.md`).

## Workspaces and data ownership

Workspaces let you collaborate with other users by sharing Investigations with
them. It is important to understand how ownership and access work before using
workspaces, so you do not inadvertently lose access to work you have created.

### Investigations define the ownership boundary

Every piece of work in ToxTempAssistant lives inside an **Investigation**.
Ownership of an Investigation never changes — the user who created it remains
the owner regardless of whether the Investigation is shared into a workspace.
When you add your own Investigation to a workspace, ownership stays with you —
no other user can share your Investigation into a workspace on your behalf.

### Studies and Assays: creator ≠ owner of the Investigation

When you are a workspace member and you create a Study or Assay inside
**someone else's** Investigation, you are the *creator* of that Study/Assay
(recorded in its `created_by` field), but the Investigation continues to
belong to its original owner. Your access to that work is granted **through
the workspace** — it is not a direct ownership right.

> [!IMPORTANT]
> If the workspace is dissolved (deleted), you will lose access
> to any Studies and Assays you created inside an Investigation you do not own.
> The Investigation is the root that defines who may access the data inside it.
> Before accepting an invitation to a shared workspace, make sure you understand
> that work you create inside another user's Investigation may become
> inaccessible to you if the workspace is later removed.

### What happens when a workspace is deleted

When a workspace is deleted:

1. All `WorkspaceMember` and shared-Investigation links for that workspace are
   removed.
2. Every workspace member (including the workspace owner) loses their
   `view_investigation` permission on every Investigation that was shared
   exclusively through this workspace.
   - Exception: if you **own** the Investigation (you created it), your access
     is never removed — it is a baseline permission attached to ownership.
   - Exception: if the same Investigation is also shared through a **different
     workspace** you belong to, your access is preserved.
3. Once a member loses access to an Investigation, they also lose access to all
   Studies and Assays inside it — including Studies/Assays they created
   themselves.

### Summary

| Scenario | After workspace deletion |
|---|---|
| You own the Investigation | ✅ Full access retained |
| You are a member, but the same Investigation is in another workspace you belong to | ✅ Access retained via the other workspace |
| You are a member and this was the only workspace sharing that Investigation | ❌ Access lost |
| You created a Study/Assay inside someone else's Investigation | ❌ Access lost if the workspace is dissolved and you are not the Investigation owner |

## License
This project is licensed under the GNU Affero General Public License, see the LICENSE file for details.

## Maintainer
- Jente Houweling | firstname.lastname@rivm.nl
- Matthias Arras | firstname.lastname@gmail.com

## How to cite
Houweling, J. M., Arras, M. M. L., Willighagen, E. L., Jennen, D. G. J., Evelo, C. T., & Kienhuis, A. S. (2026). ToxTempAssistant: using large language models to standardise cell-based toxicological test method descriptions. Evidence-Based Toxicology, 4(1). https://doi.org/10.1080/2833373X.2026.2638036
  
## References
[1]: Krebs, Alice, et al. "Template for the description of cell-based toxicological test methods to allow evaluation and regulatory use of the data." ALTEX-Alternatives to animal experimentation 36.4 (2019): 682-699. https://dx.doi.org/10.14573/altex.1909271

## Contribute

We welcome contributions! Here is how to get started and what our expectations are for contributors.

### Poetry for Dependency Management

This project uses [Poetry](https://python-poetry.org/) as the package manager.

- To install Poetry, run:
  ```
  conda install -c conda-forge pipx
  pipx ensurepath
  pipx install poetry
  ```

- To update dependencies and the lock file inside your virtual environment, run:
  ```
  poetry update
  ```

- No need to run `pip install -r requirements.txt`; Poetry manages dependencies and the lock file automatically.

### Running Tests with Pytest

We use [pytest](https://docs.pytest.org/en/stable/) along with `factory_boy`, `Faker`, and ephemeral database settings for testing.

- To run tests locally, navigate to the django `ROOT` (where `manage.py` is located), then:

  On Unix:
  ```shell
  cd myocyte
  export DJANGO_SETTINGS_MODULE=myocyte.settings  
  poetry run pytest
  ```

  On PowerShell:
  ```powershell
  cd myocyte
  $env:DJANGO_SETTINGS_MODULE="myocyte.settings"
  poetry run pytest
  ```
  (alternatively use `DJANGO_SETTINGS_MODULE=myocyte.myocyte.settings` if you run from project root, where `pyproject.toml` is).

- Tests are automatically run in GitHub Actions CI inside Docker to mirror production conditions as closely as possible.

- Only if all tests pass will we be able to proceed to include the PR.

### Ruff for Linting

We use [Ruff](https://github.com/charliermarsh/ruff) as the linter to keep the codebase consistent and clean.

- Run Ruff locally using Poetry or directly, for example:
  ```
  poetry run ruff check .
  ```

- You can automatically fix many linting issues by running:
  ```
  poetry run ruff check . --fix
  ```

### Conventional Commits

We follow the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) specification.

- Please format your commit messages accordingly to maintain readable and automated changelog generation.

- Example commit messages:
  - `feat: add new toxicity test endpoint`
  - `fix: correct calculation error in data normalization`
  - `docs: update README with contribution instructions`

### Git Pre-Commit Hooks

To help maintain code quality, we provide Git pre-commit hooks.

- Install the hooks by running:
  ```
  pip install pre-commit
  pre-commit install
  ```

- These hooks will automatically run Ruff and check your commit messages before allowing commits.

### Pull Requests (PRs)

- We encourage you to create Pull Requests for your contributions.

- On each PR, GitHub Actions will automatically run our CI workflow (`.github/workflows/ci.yml`) which builds the Docker image and runs the test suite.

- All tests must pass before the PR can be merged.

- Please include tests with any new features or bug fixes you contribute.

Thank you for helping make this project better!
