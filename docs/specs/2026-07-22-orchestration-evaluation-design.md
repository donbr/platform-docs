# Orchestration Evaluation & Kestra Spike — Design

**Date:** 2026-07-22
**Status:** Approved (design), pending implementation plan
**Author:** brainstormed with Claude Code

## Context

`platform-docs` is a 3-stage Python ETL — download `llms.txt` from ~14 doc
sources → split into pages → embed + upload to Qdrant Cloud — serving semantic
search via two FastMCP servers (OpenAI 1536d and FastEmbed 384d collections).
Refresh uses a blue-green pattern: upload into a versioned `*-v2` collection,
then re-point an alias (`platform-docs`, `platform-docs-fastembed`).

Today the pipeline is run **entirely by hand** (`uv run scripts/*.py`) — no CI,
no task runner, no orchestration dependencies. Two classes of pain motivate this
work:

1. **Silent failure.** Failed embedding/upload batches are silently skipped (the
   script exits 0 with a lower `Successful: N` count). OpenAI's 5M TPM ceiling
   and Qdrant Cloud gRPC connection resets both trigger this. There is no run
   history and no per-source completeness signal — which is how four configured
   sources (Cursor, OpenAI, Vue, Supabase) sat un-uploaded until a manual audit
   on 2026-07-22 found them. (The split-config gap for OpenAI/Vue/Supabase was
   fixed that day; Cursor remains broken — its source URL returns HTML.)
2. **No automation or observability.** The full refresh cannot run unattended,
   and there is no dashboard of what ran, what's stale, or what failed.

The user also has a longer-term vision (captured, then deliberately deferred):
a **two-layer** architecture with a durable-execution / agent-coordination layer
(Temporal-class) sitting alongside a data-ETL orchestrator (Kestra/Dagster/
Prefect-class), so a single effort could later coordinate agentic workflows
(auto source discovery/validation/repair, adaptive splitting) as well as batch
ETL.

## Goals

Deliver a **citation-backed, July-2026-current decision** on how to orchestrate
this pipeline, plus a **working Kestra proof-of-concept** that stresses the real
pain points and produces real value.

Success is measured against three user-stated goals: **reliability** (no silent
loss), **automation** (unattended scheduled runs), and **observability**
(run history / per-source freshness).

## Scope

This is **Sub-project A** of a three-part decomposition:

- **A — Orchestration Evaluation & Kestra Spike (this spec).** Two-layer
  landscape research, comparison matrix, recommended architecture, and a Kestra
  spike over the existing ETL with Supabase-backed state.
- **B — ETL-orchestrator production cutover (future cycle).** Run the real
  refresh under the chosen ETL tool, against production aliases.
- **C — Agentic source layer (future cycle).** Durable agents for auto
  source-discovery / validation / repair (the Cursor problem) and adaptive
  splitting — the durable-execution layer.

**Out of scope for A:** any production-alias mutation, the Temporal/durable
layer decision, and rewriting the existing scripts into native orchestrator
tasks (they are called as subprocesses in the spike).

## Design

### Workstream 1 — Research & Decision

**Two layers, evaluated separately** so the tight ETL decision isn't entangled
with the open-ended durable-execution question:

- *Data-ETL orchestration (the decision this cycle drives):* Kestra, Dagster,
  Prefect, Airflow, Windmill, plus newer entrants — Inngest, Trigger.dev,
  Restate, Google Cloud Workflows / Composer, AWS Step Functions.
- *Durable-execution / agent-coordination (informs Sub-project C only, not
  decided here):* Temporal (+ Temporal Cloud), Restate, DBOS, and whatever else
  surfaces.

**Comparison matrix columns:**

- License & OSS edition
- **Reasonably-priced managed / for-fee tier** — concrete $/mo where findable;
  mark "quote-only" where vendors hide pricing
- Self-host operational cost (server, workers, DB deps)
- Retries & rate-limiting / concurrency caps
- Result caching / idempotency
- Scheduling
- **State backend — can it use external Postgres / Supabase**
- Lineage / observability UI
- **Stateful multi-step (alias-swap / promotion-gate) support**
- Agent-coordination fit (informs C)
- Python-async fit
- Overall fit score for *our* pipeline

The two bolded/prioritized dimensions (external-Postgres state backend, and
stateful multi-step gate support) are the aggressive filters that eliminate
lightweight tools which can't back a real production promotion gate.

**Method — parallel agent fan-out.** One research agent per candidate, each
verifying **current (July 2026)** state with citations (release notes, pricing
pages, docs). Prompts must **explicitly instruct agents to look for July-2026
release notes and pricing** — tools change pricing models constantly (e.g.
paywalling concurrency limits), and a recommendation must rest on exact current
state, not stale training data. Findings are adversarially sanity-checked, then
synthesized into the matrix + a two-layer recommendation.

**Agents / skills / tools:**

- `deep-research` skill and/or `general-purpose` agents for the per-candidate
  fan-out
- `Context7` MCP + `WebSearch` / `WebFetch` for currency (July-2026-scoped
  prompts)
- `feature-dev:code-explorer` to ground claims in the actual `scripts/`
- `superpowers:writing-plans` to hand off to Sub-project B

**Deliverable:** `docs/research/2026-07-22-orchestration-comparison.md` — the
matrix and a clear recommended architecture.

### Workstream 2 — Kestra Spike

A Kestra flow modeling the **real DAG**:

```text
download → split → upload-openai → upload-fastembed → verify-counts → alias-swap
```

with the features that actually matter for our pain points:

- **Retries** on the upload tasks (Qdrant gRPC resets).
- **Concurrency limit** on embedding/upload (OpenAI 5M TPM ceiling).
- **Pre-swap verification gate:** `verify-counts` blocks the alias re-point
  unless the new collection's point count matches `docs_expected`. This is the
  direct fix for the silent-skip problem.
- **Existing scripts called as subprocess tasks** (preserve their CLI and flags
  — `--sources`, `--dry-run`, `--batch-size`, `--workers`). Notes captured on
  which tasks would most benefit from becoming native Kestra tasks later.

**Sandbox target (not production).** The spike executes the full
download → split → upload pipeline for real — writing real vectors to Qdrant —
but into **dedicated POC collections** (`platform-docs-poc-v1` /
`platform-docs-poc-fastembed-v1`), never the production `*-v2` collections. The
`alias-swap` task points a **temporary sandbox alias** `platform-docs-poc-active`
at the POC collection, and never touches the live `platform-docs` / `-fastembed`
aliases. The production collections and aliases are thus untouched at every
stage. This proves the promotion gate and generates real Supabase state without
any risk to live endpoints if the pipeline fails mid-flight.

**First real payload.** The spike's inaugural run uploads the 608
OpenAI/Vue/Supabase pages split on 2026-07-22 (OpenAI 139, Vue 92, Supabase
377), so the POC produces real value rather than throwaway data.

**Deliberate failure test.** A dedicated spike test manually triggers a failure
*before* the verification gate (e.g. force a mid-upload error) to prove that
Kestra's error handling: (a) writes a `failed` status row to the Supabase
`pipeline_runs` table, and (b) halts — the alias swap must NOT execute. This
validates that the observability and the gate both work on the unhappy path,
not just the happy path.

**Deliverables:** `spikes/kestra/` — flow YAML, Supabase schema SQL, and run
notes.

### State / Data Model (Postgres)

> **Amendment (2026-07-22):** the *spike* uses a dedicated **local Docker
> Postgres** (`pgvector/pgvector:pg16`, `platform_docs` db on host port 5433)
> rather than cloud Supabase — self-contained, disposable, no cloud creds, and
> it does not share the existing `agent-memory-postgres` container. The schema
> and JDBC-Query state design are unchanged and port to Supabase for the
> production cutover (Sub-project B) by swapping only the connection URL. The
> "Supabase-as-Kestra-backend" risk below is therefore retired for the spike;
> it re-enters scope in Sub-project B.

State lives in a dedicated Postgres schema, `orchestration`, isolated
from Kestra's own internal metadata (`kestra_system`; see Risks). Core table:

```sql
create schema if not exists orchestration;

create table orchestration.pipeline_runs (
  run_id            uuid primary key,
  flow              text not null,
  source            text,
  stage             text,          -- download | split | upload | verify | alias_swap
  status            text not null, -- running | success | failed
  environment       text not null default 'poc',  -- poc | staging | prod
  docs_expected     integer,
  docs_uploaded     integer,
  collection_version text,
  alias_swapped_at  timestamptz,
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  error             text
);
```

`docs_expected` vs `docs_uploaded` per source is exactly the completeness
telemetry that was missing when the four sources dropped. The `environment`
column is included **now** (values `poc` / `staging` / `prod`) so the table does
not need migrating when this graduates from spike to production.

## Deliverables & Layout

- `docs/specs/2026-07-22-orchestration-evaluation-design.md` — this spec
- `docs/research/2026-07-22-orchestration-comparison.md` — matrix + recommendation
- `spikes/kestra/` — flow YAML, Supabase schema SQL, run notes

## Success Criteria

**Research:** the matrix covers every candidate with a verified July-2026
managed-pricing column and a clear two-layer recommendation.

**Spike:**

1. One unattended end-to-end run completes against the `platform-docs-poc-active`
   sandbox alias.
2. The verification gate **provably blocks** a bad alias-swap (the deliberate
   failure test halts before swap and records a `failed` row).
3. Run state — including per-source `docs_expected` vs `docs_uploaded` — lands in
   Supabase `orchestration.pipeline_runs`.
4. The 608 new OpenAI/Vue/Supabase docs are live in Qdrant in the POC
   collections (`platform-docs-poc-v1` / `platform-docs-poc-fastembed-v1`), with
   the production `*-v2` collections and aliases untouched.

## Risks & Mitigations

- **Kestra local setup friction (Docker).** Mitigate by scoping the spike to a
  single local Docker Compose stack; document the exact setup in run notes.
- **Managed-pricing opacity.** Some vendors hide pricing — recorded explicitly
  as "quote-only" in the matrix rather than guessed.
- **Supabase as Kestra's own backend.** Kestra officially supports Postgres for
  its internal metadata repository, but it is connection-heavy under rapid state
  updates. The spike retires this risk early AND **isolates Kestra's internal
  tables into a separate schema (`kestra_system`)**, away from the custom
  `orchestration` schema, so Kestra's rapid internal writes never step on our
  pipeline telemetry.
- **Scope creep into the Temporal layer.** Explicitly deferred to Sub-project C
  so this cycle stays shippable.
