# Prefect Setup Walkthrough (+ limitations vs Kestra)

A companion to the [Kestra walkthrough](kestra-setup-walkthrough.md), from a
hands-on Prefect spike of the *same* pipeline. Prefect is the **pre-vetted
fallback**; Kestra was chosen mainly for native Google Sheets/Drive. This guide
covers standing Prefect up and — importantly — **where it falls short for this
project**. Full analysis: the [comparison](../research/2026-07-22-orchestration-comparison.md).

> **TL;DR:** Prefect is **noticeably easier to stand up** than Kestra and nicer to
> write, with **run history for free**. But it has **no native Google Sheets/Drive**,
> **no auth out of the box**, and its headline token-aware rate limiting **doesn't
> apply** unless you rewrite the upload in-process.

---

## 1. What you get

`spikes/prefect/flow.py` — the same ETL (download → split → upload → verify gate →
promote) as a Prefect 3 flow, against the same POC collections and the same
Qdrant-side helpers. Only the orchestrator differs.

## 2. Bring it up (this was genuinely light — no Kestra-style snags)

```bash
# deps (isolated group)
uv sync --group prefect-spike

# reuse the local Postgres from the Kestra spike; create a prefect DB once
docker exec kestra-postgres-1 psql -U kestra -d platform_docs -c "create database prefect;"

# point Prefect's backend at it, start the server (API + UI on :4200)
export PREFECT_HOME=$(pwd)/spikes/prefect/.prefect
uv run --group prefect-spike prefect config set \
  PREFECT_API_DATABASE_CONNECTION_URL="postgresql+asyncpg://kestra:kestra@localhost:5433/prefect"
uv run --group prefect-spike prefect server start --host 127.0.0.1 --port 4200 &

# run the pipeline against the server
export PREFECT_API_URL=http://127.0.0.1:4200/api
uv run --group prefect-spike python spikes/prefect/flow.py --expected 0        # happy path
uv run --group prefect-spike python spikes/prefect/flow.py --expected 99999    # failure test (gate blocks promote)
```

Dashboard: **http://127.0.0.1:4200**. Shut down: `pkill -f "prefect server start"`
(and `drop database prefect` to fully clean up).

### The only friction encountered

- Needs `asyncpg` for the Postgres backend (added in the `prefect-spike` group).
- The OSS server has **no auth** — bind to `127.0.0.1` (as above); never expose it.
- Cross-tool artifact: the Kestra container (running as root) left **root-owned
  files** in `data/`, which broke Prefect's `split` with a `PermissionError`. Fix:
  `docker exec kestra-kestra-1 chown -R $(id -u):$(id -g) /repo/data`. (Not Prefect's fault.)

That's it — no mount-clobbering, storage-config, auth-init, or plugin-drift saga
like Kestra. **Setup friction clearly favors Prefect.**

## 3. Where Prefect is better

- **Python-native ergonomics.** `@flow`/`@task` decorators; the promotion gate is a
  one-line `raise`; the `verify`/`alias_swap` helpers are imported and called
  **in-process** (no subprocess/YAML plumbing).
- **Run history for free.** The Prefect server auto-records every flow/task run to
  Postgres — **no hand-rolled telemetry table**. Kestra needed an
  `orchestration.pipeline_runs` table plus JDBC-Query tasks to match this.
- **Lighter, faster stand-up** (see §2).

## 4. Comparative limitations (why Kestra still won here)

| Limitation | Detail | Impact for platform-docs |
|---|---|---|
| **No native Google Sheets / Drive** | `prefect-gcp` covers BigQuery/GCS/Cloud Run/Vertex — **not** Workspace. Community `prefect-google-sheets` is Prefect-2.0-era. | **The deciding factor.** The docs-stats Sheet + Drive report would be **DIY** (`gspread`/`google-api-python-client` you write and maintain). Kestra ships `plugin-googleworkspace` (native Sheets `Load`/`Read`, Drive `Upload`). |
| **No auth out of the box** | OSS server is open on `:4200`; you must add a proxy/EE for auth. | Kestra 1.x enforces basic auth by default — safer default. |
| **Token-aware rate limiting doesn't apply by wrapping** | Prefect's `slot_decay`/`rate_limit` only throttle calls made **in-process**. Subprocessing the existing `upload_to_qdrant*.py` (the near-verbatim wrap) bypasses it entirely — you get the same in-script batching as Kestra. | Realizing Prefect's headline TPM advantage means **reimplementing the embed/upload loop in-process** — real work, not a free win. |
| **Idempotency** | Not solved (same upload script) — re-runs duplicate. | Identical to Kestra; orchestrator-independent. |

## 5. When to choose Prefect over Kestra

Flip to Prefect if any of these dominate:

- You **don't** need native Google Sheets/Drive (Kestra's main edge disappears).
- You want **in-process Python** orchestration with typed data flow rather than YAML + subprocess.
- You're willing to **rewrite the upload in-process** to get genuine token-aware TPM limiting (Prefect gives you the primitive; Kestra doesn't).
- You value the lighter setup and free observability above the Workspace integration.

For platform-docs specifically, the confirmed Google Sheets requirement tips it to
Kestra — but the margin is narrow, and Prefect remains the ready fallback.
