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

## Production-readiness checklist (before Sub-project B cutover)

1. **Replace the bind-mount + in-container `uv`** with a purpose-built image (repo + deps baked in) or a Docker/K8s task runner. The mount is a dev convenience and carries the secret-exposure surface flagged in review.
2. **Fix idempotency first** — upsert by `doc_id` so re-runs are safe.
3. **Real secrets management** (not a repo-mounted `.env`) and **failure alerting** (Slack/email task).
4. **Solve non-interactive auth init** for reproducible headless/CI deploys.
5. Point state at managed Postgres (trivial URL swap) and actually exercise the scheduler.
6. Pin the exact image version; document the Java-25-in-image detail.

## Bugs found for the wider repo (value beyond the spike)

The spike surfaced a real bug in shared code: `download_llms_raw.py` exited non-zero on an unused-file 404. Fixed to exit on `llms-full.txt` failures only. This would have degraded any future automation, orchestrated or not.

## One-line summary

The orchestrator is trustworthy; the pipeline's correctness (rate limiting, idempotency) remains the code's job — plan for both, and Kestra is the right tool for this shop given the Workspace integration.
