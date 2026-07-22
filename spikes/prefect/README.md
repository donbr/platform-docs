# Prefect Spike — head-to-head with Kestra

A Prefect 3 implementation of the **same** platform-docs ETL as the Kestra spike
(download → split → upload×2 → verify gate → promote), against the **same** POC
collections/aliases and the **same** Qdrant-side helpers (`spikes/kestra/verify_counts.py`,
`alias_swap.py`, `poc_config.py`). Only the orchestrator differs — so the
comparison is apples-to-apples. See the head-to-head in
[`docs/research/2026-07-22-orchestration-comparison.md`](../../docs/research/2026-07-22-orchestration-comparison.md).

Production safety: writes to POC collections only; `alias_swap` refuses prod aliases.

## Run it

```bash
# deps (isolated group)
uv sync --group prefect-spike

# reuse the local Postgres from the Kestra spike; create a prefect DB once
docker exec kestra-postgres-1 psql -U kestra -d platform_docs -c "create database prefect;"

# point Prefect's backend at it + start the server (UI/API on :4200)
export PREFECT_HOME=$(pwd)/spikes/prefect/.prefect
uv run --group prefect-spike prefect config set \
  PREFECT_API_DATABASE_CONNECTION_URL="postgresql+asyncpg://kestra:kestra@localhost:5433/prefect"
uv run --group prefect-spike prefect server start --host 127.0.0.1 --port 4200 &

# run the pipeline (connected to the server so runs show in the UI + Postgres)
export PREFECT_API_URL=http://127.0.0.1:4200/api
uv run --group prefect-spike python spikes/prefect/flow.py --expected 0        # happy path
uv run --group prefect-spike python spikes/prefect/flow.py --expected 99999    # failure test (gate blocks promote)
```

Dashboard: http://127.0.0.1:4200

## What the spike established

- **Parity:** runs the full pipeline; the promotion gate (`raise` on shortfall)
  blocks `promote` and fails the flow — proven with `--expected 99999`.
- **Prefect wins:** lighter setup, nicer Python (gate is one `raise`, helpers
  imported in-process), and **run history for free** (auto-recorded in Postgres;
  no `pipeline_runs` table or JDBC tasks like Kestra needed).
- **Prefect's TPM edge is NOT free:** subprocessing the existing upload script
  (the near-verbatim wrap) never routes embedding through Prefect's `slot_decay`
  rate limit — you get the same in-script batching as Kestra. Realizing it means
  reimplementing the embed/upload loop in-process around `rate_limit(...)`.
- **Kestra wins:** native Sheets/Drive plugin, and auth enforced by default
  (Prefect OSS server is open on :4200).
- **Idempotency** is unsolved in both (same upload script → re-run duplication).

## Caveats

- The Prefect OSS server has **no auth** — bind to localhost only (as here).
- Running Kestra as root left root-owned files in `data/`; if split fails with a
  `PermissionError`, run `docker exec kestra-kestra-1 chown -R $(id -u):$(id -g) /repo/data`.
