# Kestra Spike — platform-docs ETL

A local, self-contained Kestra proof-of-concept that runs the platform-docs ETL
(download → split → upload → verify → promote) with retries, a **promotion gate**
that blocks a bad alias swap, Postgres-backed run telemetry, and optional
**Google Drive** run-report upload. It writes to **POC collections/aliases only**
— production `*-v2` collections and their aliases are never touched.

## Components

| File | Role |
|---|---|
| `docker-compose.yml` | Dedicated local Postgres (`platform_docs`, host 5433) + Kestra (`v1.3-full`, port 8080) |
| `sql/001_orchestration_schema.sql` | `orchestration` (telemetry) + `kestra_system` (Kestra internals) schemas |
| `flows/platform_docs_poc.yaml` | The Kestra flow (`platform_docs.poc`) |
| `poc_config.py` | Production-safety constants + `expected_doc_count` |
| `verify_counts.py` | Promotion gate (exit 1 on shortfall) |
| `alias_swap.py` | Guarded sandbox alias swap (refuses production aliases) |
| `report.py` | Renders the run-summary markdown uploaded to Drive |

## Prerequisites

- Docker running.
- Repo `.env`-style keys: `QDRANT_API_URL`, `QDRANT_API_KEY`, `OPENAI_API_KEY`.
- For Drive reporting **only**: a Google **service account** (see below).

## Run it

```bash
cd spikes/kestra
cp .env.example .env          # then fill in the values

# 1. Start Postgres and apply the schema (creates both schemas before Kestra migrates)
docker compose up -d postgres
sleep 5
psql "$PLATFORM_DOCS_DB_URL" -f sql/001_orchestration_schema.sql

# 2. Start Kestra (confirm the pinned image tag first — see Version pin)
docker compose up -d
sleep 20
curl -sf http://localhost:8080/api/v1/version && echo " <- kestra up"

# 3. Ensure uv is available inside the container
docker compose exec kestra bash -lc 'which uv || (curl -LsSf https://astral.sh/uv/install.sh | sh)'

# 4. Load the flow
docker compose exec kestra kestra flow validate /app/spikes/kestra/flows/platform_docs_poc.yaml
docker compose exec kestra kestra flow namespace update platform_docs /app/spikes/kestra/flows

# 5. Run it (happy path, no Drive)
docker compose exec kestra kestra flow execute platform_docs poc
```

Reset everything (Postgres uses a named volume, so it survives `down`):
`docker compose down -v`.

## Version pin

Do **not** use `kestra/kestra:latest`. Pin an explicit tag; use the **`-full`**
variant so the `io.kestra.plugin.jdbc.postgresql` and `googleworkspace` plugins
are bundled. Kestra 1.3+ bundles Java 25 inside the image (no host JDK needed for
this Docker setup). `v1.3-full` is a placeholder — confirm the current 1.3-line
patch tag on Docker Hub (`kestra/kestra` tags) and record it here.

## Google Drive reporting (optional)

The flow uploads a run-summary markdown to a test Drive folder when run with
`upload_to_drive=true`. This needs a Google **service account** (Kestra's
Workspace plugin does not accept user OAuth/ADC creds):

1. In Google Cloud, create a service account and **enable the Google Drive API**.
2. Download its JSON key.
3. **Share** the Drive folder `1WSgQQCMT9tgnM-108HtyXfIUZtgliBwR` with the service
   account's email (e.g. `name@project.iam.gserviceaccount.com`) as **Editor**.
4. Put `SECRET_GOOGLE_SERVICE_ACCOUNT=<base64 of the JSON>` in `.env`
   (`base64 -w0 < service-account.json`).
5. Run with the flag:
   `docker compose exec kestra kestra flow execute platform_docs poc --inputs '{"upload_to_drive": true}'`

The upload task is `allowFailure: true`, so a Drive misconfiguration never fails
an otherwise-successful pipeline run — the report just won't appear.
