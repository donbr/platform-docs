# Kestra Spike — Retrospective

**Date:** 2026-07-22
**Scope:** Sub-project B — the Kestra proof-of-concept ([plan](../plans/2026-07-22-kestra-spike.md))
**Basis:** hands-on. A local Kestra 1.3.29 + Postgres stack ran the real ETL end-to-end (~6 executions), including the deliberate failure test.

## Verdict

**Reliable and fit for purpose at this scale — with two caveats it does not solve for you, and a real (one-time) hardening pass before production.** For a small, scheduled, OSS-first doc-ETL that needs a promotion gate and where Google Drive/Sheets integration has standing value, Kestra is a good, defensible choice. It reliably does the orchestrator's job; it does not paper over pipeline-code responsibilities.

## What earned trust (observed, not assumed)

- **The engine was solid once configured.** Across ~6 executions it never flaked: state persisted, retries fired on transient download failures, DAG ordering held, terminal states were deterministic, and it recovered cleanly after a mid-work container restart. The Postgres backend lost nothing.
- **The promotion gate is a real circuit breaker.** The failure test proved it: `verify_counts` failed → `alias_swap` never executed → a `failed` row was written → no alias moved. This is the single behavior that kills the original "silent skip" pain — a bad upload can no longer quietly promote.
- **It closes all three original gaps:** retries + gate (reliability), cron triggers (automation), Postgres-backed run history + per-source telemetry (observability).
- **Ops footprint is light** — one container + Postgres, Apache-2.0 — and the native Google Workspace plugin behaved exactly as the plugin registry described.
- **Security posture improved:** Kestra 1.x enforces basic auth by default, which resolved the "unauthenticated RCE" review finding.

## What it does NOT do for you (boundaries, not defects)

1. **It will not hold the OpenAI TPM ceiling.** Kestra's concurrency is *flow-level*, not token-aware. The 5M-TPM limit is held entirely by the in-script `--batch-size/--workers`. Kestra retries after a limit is hit; it does not prevent it.
2. **It will not make the pipeline idempotent.** The spike empirically proved re-runs *accumulate duplicates* (628 → 1256 → 1884) because the upload assigns random point IDs, and the `actual ≥ expected` gate does not catch over-count. Kestra faithfully re-ran a non-idempotent job.

Neither is a Kestra failing — both are the boundary of what an orchestrator owns. But "adopt Kestra" ≠ "pipeline is now correct."

## Friction (setup was not turnkey)

Seven one-time snags surfaced during bring-up, all now fixed and documented:

1. Mounting the repo at `/app` clobbered the Kestra binary → mount at `/repo`.
2. Kestra 1.x requires `kestra.storage.type` (undocumented in our first config).
3. Kestra 1.x initializes its admin account via the **UI first-run** — config-seeding it did not take, which hurts headless/CI reproducibility.
4. Plugin/type drift: `googleworkspace.drive.Upload` (not `Create`); `taskRunner`/`pluginDefaults` shapes.
5. `pipeline_runs.run_id` must be `text` — Kestra `execution.id` is base62, not a UUID.
6. The Process task-runner uses its own cwd → shell tasks must `cd /repo` with an isolated `UV_PROJECT_ENVIRONMENT`.
7. `download_llms_raw.py` hard-failed on a benign `llms.txt` 404 (a file never consumed downstream) — a genuine ETL bug that would break *any* automation.

Takeaway: Kestra 1.x has sharp edges and real version drift. Budget ~a day for a first production stand-up, not an hour.

## Stability improvements applied (2026-07-22)

Low-risk hardening already committed to `spikes/kestra/`:

- **`restart: unless-stopped`** on both services — the stack now survives Docker/host restarts.
- **Persistent Kestra internal storage** — moved off ephemeral `/tmp` to the `kestra_storage` named volume, so task logs/outputs survive container recreation (run metadata was already durable in Postgres).
- **Cron trigger wired** (`Schedule`, `0 3 * * *`) but **`disabled: true`** — ready for unattended nightly runs, off until idempotency is fixed (else it would append duplicates every night).
- README documents the full **startup / shutdown / reset** lifecycle (`stop` / `up` / `down` / `down -v`).

## Production-readiness checklist (before Sub-project B cutover)

1. **Fix idempotency first** — upsert by `doc_id` so re-runs (and the nightly cron) are safe; only then enable the Schedule trigger.
2. **Replace the bind-mount + in-container `uv`** with a purpose-built image (repo + deps baked in) or a Docker/K8s task runner. The mount is a dev convenience, carries the secret-exposure surface flagged in review, and caused root-owned-file pollution of `data/`.
3. **Real secrets management** (not a repo-mounted `.env`) and **failure alerting** — a Kestra notification task (Slack/email) on the flow's `errors` path.
4. **Solve non-interactive auth init** for reproducible headless/CI deploys.
5. Point state at **managed Postgres** (trivial URL swap) and back it up (it holds both Kestra metadata and the run telemetry).
6. Pin the exact image version (done: `v1.3.29`); document the Java-25-in-image detail (done).
7. Add JVM **resource limits** (`mem_limit`) and a flow-level **timeout/SLA**.
8. **Build the documentation-stats Google Sheet** (see below) — the highest-value use of Kestra's native Workspace plugin.

## Recommended feature: a documentation-stats Google Sheet

There is currently **no** corpus-summary Sheet — the only Drive output is a per-run POC-count markdown file. A scheduled Kestra flow using the native `plugin-googleworkspace` `sheets.*` tasks should **read the manifests + live collections and upsert a Sheet** with one row per source: `source`, `source_url`, `doc_count`, `last_download_date`, `collection_version`, `last_refreshed`. This is exactly the integration Kestra was chosen for, gives non-technical stakeholders live freshness visibility, and needs only the Google service account already required for Drive reporting.

## Bugs found for the wider repo (value beyond the spike)

The spike surfaced a real bug in shared code: `download_llms_raw.py` exited non-zero on an unused-file 404. Fixed to exit on `llms-full.txt` failures only. This would have degraded any future automation, orchestrated or not.

## Postscript — Prefect comparison spike

To make the Prefect-vs-Kestra call symmetric, the same pipeline was later built and run in Prefect 3 (`spikes/prefect/`, 4 live runs; head-to-head in the [comparison doc](../research/2026-07-22-orchestration-comparison.md)). Findings that refine the decision:

- **Prefect's ergonomics + observability are genuinely better.** Lighter setup, `@flow`/`@task` with the gate as a plain `raise`, in-process helper reuse, and — the standout — **run history for free** (auto-recorded in Postgres; no `pipeline_runs` table or JDBC tasks).
- **Prefect's headline TPM edge did not materialize from wrapping the scripts** — subprocessing the existing upload never routes embedding through `slot_decay`; realizing it needs an in-process upload rewrite. So its paper advantage is smaller in practice.
- **Two points favor Kestra the paper missed:** Prefect OSS ships **no auth**; Kestra's **native Sheets/Drive** is unmatched.
- **Idempotency** is unsolved in both (same script) — confirming it's orchestrator-independent.

Net: the gap is narrower than the paper implied. Kestra still wins **for this shop** on the strength of native Sheets/Drive (a confirmed requirement) + default auth; absent the Workspace need, the call would flip to Prefect.

## One-line summary

The orchestrator is trustworthy; the pipeline's correctness (rate limiting, idempotency) remains the code's job — plan for both, and Kestra is the right tool for this shop given the Workspace integration.
