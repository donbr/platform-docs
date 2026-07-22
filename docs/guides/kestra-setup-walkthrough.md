# Kestra Setup Walkthrough (lessons learned)

A field guide to standing up the platform-docs Kestra stack, written from a
hands-on spike that hit **7 real snags**. If you follow this in order you should
avoid all of them. For the reasoning behind the tool choice, see the
[orchestration comparison](../research/2026-07-22-orchestration-comparison.md) and
[retrospective](../retrospectives/2026-07-22-kestra-spike-retrospective.md).

> **TL;DR:** Kestra is a good fit but **not** turnkey — budget ~a day for a first
> production stand-up. The rough edges are all one-time and captured below.

---

## 1. What you get

A local, self-contained stack (`spikes/kestra/`) that runs the ETL
(download → split → upload → **verify gate** → alias promote) with retries, run
telemetry in Postgres, a scheduled/unattended option, and — the reason Kestra was
chosen — **native Google Sheets/Drive** output.

## 2. Prerequisites

- Docker (running).
- `QDRANT_API_URL`, `QDRANT_API_KEY`, `OPENAI_API_KEY` (in the repo `.env`).
- For Google Sheets/Drive output only: a Google **service account** (§6).

## 3. Bring the stack up (the happy path)

```bash
cd spikes/kestra
cp .env.example .env      # fill Qdrant/OpenAI keys + KESTRA_BASIC_AUTH_USERNAME/PASSWORD

# 1) Postgres first, then apply the schema (both schemas must exist before Kestra migrates)
docker compose up -d postgres
sleep 5
psql "$PLATFORM_DOCS_DB_URL" -f sql/001_orchestration_schema.sql

# 2) Kestra
docker compose up -d
#    → dashboard at http://localhost:8080/ui/  (localhost-only by design)

# 3) FIRST RUN ONLY: open the dashboard and complete the admin-account setup (see Gotcha #3)

# 4) Load the flow (mounting the YAML does NOT register it)
docker compose exec kestra kestra flow validate /app/spikes/kestra/flows/platform_docs_poc.yaml
docker compose exec kestra kestra flow namespace update platform_docs /app/spikes/kestra/flows

# 5) Run it (from the UI, or:)
docker compose exec kestra kestra flow execute platform_docs poc
```

Lifecycle: `docker compose stop|up -d` (pause/resume; state kept) · `down` (remove
containers, volumes kept) · `down -v` (wipe Postgres + Kestra storage).

## 4. The 7 gotchas (lessons learned)

| # | Symptom | Cause & fix |
|---|---|---|
| 1 | `exec: /app/kestra: not found`, container won't start | The repo bind-mount was at `/app`, which **holds the Kestra binary**. Mount at **`/repo`** instead; never set `working_dir` over `/app`. |
| 2 | `Server configuration requires 'kestra.storage.type'` | Kestra 1.x **requires** an internal-storage type. Set `kestra.storage.type: local` (we persist it on a named volume, not `/tmp`). |
| 3 | API returns `401` even with correct creds; `isBasicAuthInitialized: false` | Kestra 1.x **enforces basic auth** and initializes the admin account via the **UI on first launch** — config-seeding it did not take. Open `http://localhost:8080/ui/`, create the admin account, then put the same creds in `.env` (`KESTRA_BASIC_AUTH_*`). *(This is a security plus vs Prefect, which ships no auth.)* |
| 4 | Flow `422 Invalid entity: Unrecognized field ...` | Plugin **property/type drift** between versions. Don't guess — query the live registry: `GET /api/v1/plugins/<taskType>`. E.g. Drive upload is `googleworkspace.drive.Upload` (has `from`), **not** `Create` (folders/metadata). Sheets write is `sheets.Load`. |
| 5 | `invalid input syntax for type uuid` on the first state write | Kestra `execution.id` is a **base62** id, not a UUID. `pipeline_runs.run_id` must be **`text`**. |
| 6 | `scripts/... not found` / wrong CWD in shell tasks | The **Process task-runner runs in its own temp dir**, not the repo. Shell tasks must `cd /repo`, use an isolated `UV_PROJECT_ENVIRONMENT`, and write `outputFiles` to the task workingDir (`OUT="$(pwd)/x"; cd /repo && … --out "$OUT"`), **not** under `/repo`. |
| 7 | `PermissionError` deleting `data/...` from a later host-side run | Kestra runs as **root** and its bind-mount writes **root-owned files** into `data/`. Fix: `docker exec kestra-kestra-1 chown -R $(id -u):$(id -g) /repo/data`. (Another reason to bake an image instead of bind-mounting for production.) |

Plus two behavioral facts that are **not bugs** but bite the unwary:

- **Concurrency is flow-level, not token-aware.** Kestra will not hold the OpenAI 5M-TPM ceiling for you — that stays in the upload script's `--batch-size 25 --workers 2`.
- **The upload is not idempotent.** Re-runs append duplicates (random point IDs). Reset the POC collections between runs, or key by `doc_id`, before enabling the nightly `Schedule` trigger.

## 5. Version pin

Use `kestra/kestra:v1.3.29` (= `v1.3` = `latest-lts`). The **standard** tag bundles
the plugins we need (`jdbc.postgresql`, `googleworkspace`); do **not** use
`-no-plugins`, and there is **no** `-full` tag. Java 25 is bundled in-image.

## 6. Google Service Account — browser walkthrough (required for Sheets/Drive)

Kestra's Workspace plugin authenticates with a **service-account JSON key** — user
OAuth / `gcloud` ADC (`authorized_user`) tokens will **not** work. You do **not**
need domain-wide delegation; access is granted by *sharing* the target Sheet/folder
with the SA's email.

### Fast path — `gcloud` (recommended over the console clicks below)

If you have `gcloud` authenticated, the whole SA setup is four commands (APIs are
free — no billing needed):

```bash
PROJECT=cohort9-489302   # any project you own; SA can access anything shared with it
gcloud services enable sheets.googleapis.com drive.googleapis.com --project "$PROJECT"
gcloud iam service-accounts create platform-docs-kestra \
  --project "$PROJECT" --display-name "platform-docs Kestra (Sheets/Drive)"
gcloud iam service-accounts keys create ~/platform-docs-kestra.json \
  --iam-account "platform-docs-kestra@$PROJECT.iam.gserviceaccount.com"
# then base64 the key into the Kestra env (gitignored):
echo "SECRET_GOOGLE_SERVICE_ACCOUNT=$(base64 -w0 < ~/platform-docs-kestra.json)" >> spikes/kestra/.env
docker compose -f spikes/kestra/docker-compose.yml up -d   # reload the secret
```

Then jump to step **D** (share the Sheet/folder with the SA email) and step 15's
verification. The browser walkthrough below is the equivalent via the console.

**A. Create the project & enable APIs**
1. Go to **https://console.cloud.google.com** and sign in.
2. Top bar → **project picker** → **New Project** (e.g. `platform-docs`) → **Create**, then select it.
3. Left menu → **APIs & Services → Library**.
4. Search **"Google Sheets API"** → click it → **Enable**.
5. Back to Library, search **"Google Drive API"** → **Enable** (needed for the Drive run-report too).

**B. Create the service account**
6. Left menu → **IAM & Admin → Service Accounts** → **+ Create Service Account**.
7. **Service account name:** `kestra-docs` (ID auto-fills) → **Create and Continue**.
8. **Grant roles:** skip — click **Continue**. *(No project role is needed; the SA reaches only what you explicitly share with it.)*
9. **Grant users access:** skip → **Done**.
10. On the Service Accounts list, **copy the SA email** — it looks like `kestra-docs@platform-docs.iam.gserviceaccount.com`. You'll share resources with it.

**C. Create and download the JSON key**
11. Click the service account → **Keys** tab → **Add Key → Create new key**.
12. Choose **JSON** → **Create**. A `*.json` file downloads — this is the secret. Store it safely; treat it like a password.

**D. Share the target Sheet / Drive folder with the SA**
13. Create (or open) the Google **Sheet** that will hold the stats → **Share** → paste the **SA email** → role **Editor** → **Send** (uncheck "notify" — it's a robot). Copy the Sheet's **ID** from its URL: `https://docs.google.com/spreadsheets/d/`**`<THIS>`**`/edit`.
14. For the Drive run-report, open the Drive **folder** → **Share** → add the SA email as **Editor**. (The test folder is `1WSgQQCMT9tgnM-108HtyXfIUZtgliBwR`.)

**E. Wire it into Kestra**
15. Base64-encode the key and put it in `spikes/kestra/.env`:
    ```bash
    echo "SECRET_GOOGLE_SERVICE_ACCOUNT=$(base64 -w0 < ~/Downloads/kestra-docs-XXXX.json)" >> spikes/kestra/.env
    ```
    Kestra exposes it to flows as `{{ secret('GOOGLE_SERVICE_ACCOUNT') }}`. Restart Kestra to pick up the new env: `docker compose up -d`.

> **Never commit the JSON key or the base64 secret.** `spikes/kestra/.env` is gitignored; keep it that way.

## 7. Enabling the outputs

- **Documentation-stats Sheet** (`flows/docs_stats_sheet.yaml`): after §6, load the
  flow and run it with your Sheet ID:
  ```bash
  docker compose exec kestra kestra flow namespace update platform_docs /app/spikes/kestra/flows
  docker compose exec kestra kestra flow execute platform_docs docs_stats_sheet --inputs '{"spreadsheet_id":"<YOUR_SHEET_ID>"}'
  ```
  It writes one row per source (`source, source_url, doc_count, last_downloaded, collection_version, generated_at`) into the `Stats` tab. Arm the weekly refresh by removing `disabled: true` from its `Schedule` trigger.
- **Drive run-report** (in `platform_docs_poc.yaml`): run the POC flow with `--inputs '{"upload_to_drive": true}'`.
- **Production blue-green refresh** (`flows/prod_refresh.yaml`, **collection-agnostic**): builds a fresh versioned collection from the current corpus, verifies the **full** count, then re-points the given production alias — leaving the old collection as an instant rollback. Two safety gates: the **verify gate** (a short count fails the flow before any swap) and a `confirm` input (the alias is only re-pointed when `confirm=true`; `prod_alias_swap.py` also refuses non-production aliases and re-checks the count). Bulk sources upload at 25/2, Anthropic at 10/1 (Pitfall 6). Choose the embedder via `prod_alias` + `upload_script`:
  ```bash
  # OpenAI — full refresh (build + verify + promote):
  kestra flow execute platform_docs prod_refresh --inputs '{"target_collection":"platform-docs-v4","confirm":true}'
  # FastEmbed — full refresh:
  kestra flow execute platform_docs prod_refresh --inputs '{"target_collection":"platform-docs-fastembed-v4","prod_alias":"platform-docs-fastembed","upload_script":"upload_to_qdrant_fastembed.py","confirm":true}'
  # Promote an already-built collection without re-embedding (verify + swap only):
  kestra flow execute platform_docs prod_refresh --inputs '{"target_collection":"platform-docs-v3","rebuild":false,"confirm":true}'
  ```
  Run with `confirm=false` first for a safe dry run (builds + verifies, no swap).

## 8. Known limitations (for this stack)

- No auth beyond basic-auth (OSS); keep it localhost-only. RBAC/SSO are EE-only.
- Rate limiting is not token-aware (see §4).
- The bind-mount + root pattern is a dev convenience — bake an image for production.
- Managed-tier pricing is quote-only (no public rate card).
