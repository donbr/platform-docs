# Orchestration Tooling Comparison — July 2026

**Date:** 2026-07-22
**Feeds:** [Orchestration Evaluation & Kestra Spike design](../specs/2026-07-22-orchestration-evaluation-design.md) (Sub-project A, Workstream 1)
**Method:** 12-candidate parallel research fan-out (one agent per tool) + per-candidate adversarial verification of pricing/license/currency + synthesis. 25 agents, 0 errors.

> **Currency caveat:** pricing/license cells reflect live sources fetched on 2026-07-22. Cells the adversarial pass could not confirm current are marked ⚠ and detailed in "Confidence & caveats". For an OSS-first adoption the free self-hosted edition is what matters; managed pricing is secondary. Verify any ⚠ figure with the vendor before committing spend.

> **Update (2026-07-22, post-spike): the operative decision is Kestra.** The paper eval below ranked Prefect 3 first on the raw ETL core, but after a hands-on Kestra spike (validated end-to-end — see the [retrospective](../retrospectives/2026-07-22-kestra-spike-retrospective.md)) and confirmation that **Google Sheets read/write is a standing requirement** for adjacent (grading) workflows, Kestra is the chosen ETL orchestrator. Rationale in the new [Prefect vs Kestra head-to-head](#prefect-vs-kestra--head-to-head-post-spike) below. Prefect remains the pre-vetted fallback.

---

## TL;DR recommendation

**ETL-layer, NOW — recommend: Prefect 3 (Apache-2.0, self-hosted).** It is the only candidate that scored 5/5 and it hits all three pain points natively in the free edition: task retries with backoff+jitter *plus* server-side **global rate limits** (`slot_decay` — the one primitive that actually targets the OpenAI 5M-TPM ceiling rather than just capping parallelism) and global/tag concurrency caps for the Qdrant gRPC resets; cron/interval scheduling; and a full run-history UI — all with state in external Postgres and near-verbatim wrapping of the existing async scripts (add `@flow`/`@task`, delete the silent `try/except`).

**Runner-up: Kestra (4/5)** — already the subject of the in-flight spike. Apache-2.0, single Docker container + external Postgres, built-in cron+backfills, task/flow retries, a Pause/approval task for the alias-swap gate, and a rich UI. It wraps the existing CLI scripts as subprocess tasks with near-zero refactor. Two dings vs Prefect: rate limiting is flow-level (not token-aware), and orchestration lives in YAML rather than in-process Python. **Dagster (4/5)** is the third option if asset-lineage/staleness views are valued.

**"Do we even need an orchestrator?" baseline:** a `cron` + a retry/rate-limit wrapper around the existing scripts would fix pains (1) and (2) cheaply, but leaves pain (3) — observability/run-history/staleness — entirely unsolved, which is the whole reason to adopt a tool.

**Durable-execution layer, FUTURE (Sub-project C) — watch: DBOS (MIT).** Explicitly **not** decided now. DBOS is the standout of the durable trio (scored 5/5): MIT-licensed library (no cluster), Postgres-native state (satisfies the external-Postgres requirement), Postgres-backed queues giving exactly-once + global rate limiting, and shipping agent integrations (OpenAI Agents SDK, Google ADK, MCP). It could plausibly collapse *both* the ETL layer and the future agentic source-discovery/repair layer onto one Postgres. **Temporal (MIT)** remains the heavyweight reference engine if hard determinism/replay is required. Decide this separately after the ETL choice is bedded in.

## Prefect vs Kestra — head-to-head (post-spike)

The operative choice is between these two. **Read this asymmetry first:** Kestra
was **built and run end-to-end** (Sub-project B — ~6 live executions incl. the
failure test); Prefect was **not spiked** — its column is entirely paper-based
(research fan-out + docs). So Kestra's cells are *observed*, Prefect's are
*claimed*. A short Prefect spike is the honest way to make the ETL-core cells
below equally concrete; until then, weight the evidence accordingly.

| Dimension | Prefect 3 (paper) | Kestra (spiked ✓) | Edge |
|---|---|---|---|
| License / OSS core | Apache-2.0 | Apache-2.0 | tie |
| Evidence basis | docs/research only | **validated hands-on** | Kestra (confidence) |
| OpenAI TPM control | server-side **token-aware** global rate limit (`slot_decay`) | flow-level only; TPM held **in-script** (`--batch-size/--workers`) | **Prefect** |
| Python ergonomics | in-process `@flow`/`@task`, typed returns | YAML + subprocess (`cd /repo && uv run …`) | **Prefect** |
| Promotion gate (blue-green) | `pause`/`suspend_flow_run` | Pause task + verify gate — **proven to block a bad swap in the spike** | **Kestra (proven)** |
| **Google Sheets/Drive R/W** | none first-party (DIY `gspread`/API) | **first-party `plugin-googleworkspace` — native read + write**, bundled | **Kestra** ← decisive here |
| Observability / run history | full UI + Postgres | full UI + Postgres — **validated** | tie |
| Ops footprint | server + worker + Postgres | 1 container + Postgres | slight Kestra |
| Setup friction | generally smooth Python (not measured here) | **7 one-time snags** hit in the spike (1.x sharp edges) | Prefect (asymmetric evidence) |
| Idempotency | not solved — your code | not solved — your code (spike proved re-run duplication) | tie (neither) |
| Auth (OSS) | configurable | basic auth **enforced by default**; UI-init friction | mixed |

**Decision — Kestra, with eyes open.** Prefect genuinely wins the *raw ETL core*
(token-aware rate limiting and in-process Python ergonomics). But: (a) the TPM
ceiling and idempotency are **code responsibilities in both** — Prefect's edge
there narrows what you actually gain; (b) **Google Sheets read/write is a
confirmed standing requirement** (grading workflows), and Kestra's native
Workspace plugin is a real, first-party differentiator Prefect can't match
without you writing and maintaining the client; and (c) Kestra is now
**de-risked by an end-to-end spike**, while Prefect is not.

Net: adopt **Kestra** as the ETL orchestrator; keep **Prefect** as the pre-vetted
fallback if in-process Python ergonomics or server-side token-aware rate limiting
later become the dominant constraint. **Recommended next step to close the
asymmetry:** a small Prefect spike (wrap the same three scripts, run the same
gate) so the ETL-core cells are empirical rather than paper — cheap insurance
before fully committing.

## ETL-layer comparison matrix

Ranked by Fit (desc). Cells marked ⚠ are where a claim was refuted or the verification set `currency_ok=false`; values shown are the verified/corrected ones.

| Tool | License / OSS | Managed pricing (Jul 2026) | Self-host cost | Retries + rate-limit | Ext. Postgres state | Promotion-gate / stateful | Observability | Python-async fit | Fit |
|---|---|---|---|---|---|---|---|---|---|
| **Prefect 3** | Apache-2.0 (Cloud proprietary) | ⚠ Hobby $0; Starter $100/mo; **Team $400/mo base incl. 4 seats +$100/extra to 8** (corrected from "$100/user"); "$0.005/min overage" unverified; Pro/Ent quote | Low-mod: server + Postgres 14.9+/pg_trgm + worker | **Excellent** — task retries (backoff+jitter) + server-side **global rate limits (token-aware)** + concurrency caps | Yes — recommended backend | `pause/suspend_flow_run` gate; orchestrator, not durable-exec | Full self-hosted UI, unlimited retention | **Excellent** — async-native, near-verbatim wrap | **5** |
| **Kestra** | Apache-2.0 core (EE proprietary) | Quote-only (Cloud usage-based, **no published rate card**; EE per-instance quote) | **Very light** — 1 Docker container + Postgres (note: 1.3 needs Java 25) | Strong OSS task/flow retries + backoff; ⚠ concurrency is **flow-level, not token-aware** (TPM ceiling stays in-script) | Yes — JDBC Postgres is the default state store | Pause/approval task (OSS) — good; make swap idempotent yourself | Rich OSS UI (Gantt/topology/logs/history) | Subprocess tasks (`uv run`) — near-zero refactor, no in-process SDK | **4** |
| **Dagster** | Apache-2.0 (Dagster+ proprietary) | Solo $10/mo + $0.040/credit; Starter $100/mo + $0.035/credit (**0 bundled credits since May 2026**); serverless $0.010/compute-min; Pro/Ent quote | Moderate — webserver + daemon + code server + Postgres | Strong — `RetryPolicy` (backoff+jitter) + concurrency pools/queue caps | Yes — native run/event/schedule storage | Asset chain + blocking asset checks; ⚠ **no built-in human-approval primitive** (hand-roll) | **Best-in-class** — asset catalog/lineage/staleness | Excellent — async `@asset`/`@op` | **4** |
| **Windmill** | **AGPLv3** core (+ proprietary EE) | Cloud Free $0; Enterprise from $120/mo seat+compute ($20 dev / $10 op / $50 per worker) | Low-mod — server + workers + Postgres (Postgres *is* the state store) | Retries OSS (const+exp); ⚠ **concurrency/rate-limit is EE-only** (approximate via worker caps in OSS) | Yes — Postgres is native state store; point at managed instance | Native approval/suspend steps (OSS) — strong, releases worker slot | Good OSS UI + fresh/stale badges | Strong — `async def main`, uv dep resolution | **4** |
| **Inngest** | ⚠ **SSPL** server+CLI (source-available, *not* OSI-OSS); SDKs Apache-2.0 | Hobby $0; Pro from $99/mo; Enterprise quote (confirmed) | ⚠ Moderate-heavy — server + Postgres + **Redis** (recommended, not strictly mandatory as record claimed) + always-on worker service | **Excellent** — auto-retried memoized steps + GCRA throttle/concurrency/rate-limit | Partial — Postgres + Redis; not Postgres-only | `waitForEvent` gate; durable step engine | Strong traces/run-history dashboard (OSS) | Async SDK but **push model** — restructure scripts into served functions | **4** |
| **Apache Airflow** | Apache-2.0 (no open-core split) | ⚠ No free tier; Astro consumption by **size** (Small $0.35–XL $1.54/hr; workers $0.13+/hr; **dedicated cluster $2.00/hr**) — corrected; "$0.42/hr"/"$2.40/hr" were fabricated | **Heavy** — scheduler + API + triggerer + workers + executor + Postgres | Strong — retries + 3.3 pluggable policies + Pools for concurrency | Yes — external metadata DB is the intended architecture | `all_success`-gated final task; no native human gate | Mature Grid/Graph UI | Good — TaskFlow/subprocess; must package worker env | **4** |
| **Trigger.dev v4** | Apache-2.0 | Free $0; Hobby $10; Pro $50; Ent quote (+ $0.25/10k runs + per-sec compute) | Heavy — Postgres + Redis (+ ClickHouse/registry per community docs); per-task containers | Strong — retry policies + per-queue concurrency + auto-ratelimit retries | Partial — control-plane metadata in Postgres | ⚠ Waitpoints, but **zero-idle Checkpoints are Cloud-only** — the self-host promotion-gate story doesn't fully hold | Strong dashboard/traces | **Weak** — TS-first; Python runs only as spawned subprocess | **3** |
| **Google Cloud Workflows** | Proprietary, managed-only (**fails OSS-first**) | ~$0 at this scale (free 5k internal + 2k external steps/mo; $0.01/1k internal, $0.025/1k external) + Scheduler $0.10/job | N/A — not self-hostable | Declarative retries strong; no token-aware limiter (manual pacing) | **No** — managed state only (**fails requirement**) | Callback pause/resume — strong | Good GCP-native (limited console retention) | **Poor** — YAML DSL; wrap each stage as Cloud Run service | **2** |
| **AWS Step Functions** | Proprietary, managed-only | Standard $25/M transitions (4k/mo free forever); Express $1/M + GB-s; tiny at this scale | N/A (Local JAR = testing only) | **Best-in-class** declarative retries; Map `maxConcurrency` (no token bucket) | **No** — managed state (**fails requirement**) | `waitForTaskToken` approvals — strong | Strong console + 90-day history | **Awkward** — Lambda/Fargate, 256 KB payload cap | **2** |

## Google Drive / Sheets support — Kestra vs Prefect (added 2026-07-22)

Not a matrix column above, but decision-relevant given adjacent workflows that export reports to Google Drive/Sheets. **Kestra wins clearly on native + simpler support.**

| | Kestra | Prefect 3 |
|---|---|---|
| **Google Drive** | ✅ First-party `plugin-googleworkspace` — native tasks: upload, download, export, list, create, **watch** (file trigger). Bundled in the `-full` image. | ❌ None. `prefect-gcp` covers BigQuery/GCS/Cloud Run/Vertex/Secret Manager, **not** Drive. Roll your own with `google-api-python-client` in a `@task`. |
| **Google Sheets** | ✅ First-party — native **read** (ranges, value/datetime rendering) and **write**; Sheets triggers; a dedicated "Connect Google Sheets" how-to. | ⚠ No first-party. Community `prefect-google-sheets` is **Prefect-2.0-era** (Sheets-only, unmaintained for v3), or use `gspread` in a `@task`. |
| **Auth** | Service-account JSON via plugin defaults (set once). | Standard Google client auth, hand-wired in code. |
| **Effort** | Declarative YAML, no-code. | DIY Python (easy given the pipeline is already Python, but you own the client code). |

**Impact on the Prefect-vs-Kestra call:** the matrix put Prefect ahead on the *ETL core* (token-aware rate limits, async-native). But Kestra's turnkey Workspace plugin is a **real, concrete offset** if Drive/Sheets I/O is part of the workload. Net: weigh Prefect's better OpenAI-TPM handling for the embedding uploads against Kestra's batteries-included Workspace I/O — whichever you'll actually lean on more.

Sources: [kestra-io/plugin-googleworkspace](https://github.com/kestra-io/plugin-googleworkspace), [Kestra — Connect Google Sheets](https://kestra.io/docs/how-to-guides/google-sheets), [Kestra — Google Drive plugin](https://kestra.io/plugins/plugin-googleworkspace/drive), [prefect-gcp docs (no Sheets/Drive)](https://docs.prefect.io/integrations/prefect-gcp), [prefect-google-sheets (community, 2.0-era)](https://stefanocascavilla.github.io/prefect-google-sheets/).

## Durable-execution layer (future / Sub-project C)

| Tool | License | Managed pricing (Jul 2026) | Self-host cost | Ext. Postgres state | Agent-coordination fit | Fit |
|---|---|---|---|---|---|---|
| **DBOS** | MIT library (control plane proprietary) | Free (OSS) / Pro $99/mo / Teams $499/mo / Ent quote (checkpoint-metered) | **Very low** — library + one Postgres; no cluster | **Yes — core design** (any external/managed Postgres) | **Very good** — OpenAI Agents SDK + Google ADK plugin + MCP; one tool can cover ETL now *and* the agent layer | **5** |
| **Temporal** | MIT (server + SDKs) | Cloud: Essentials ≥$100/mo or 5%; Business ≥$500/mo or 10%; Ent quote | **Heavy** — 4-service cluster + DB (+ Elasticsearch for search) + worker | Yes — Postgres persistence (Temporal's own schema; +ES for rich search) | **Excellent** — reference durable-execution engine for long-running, crash-safe, human-in-the-loop agents | **3** |
| **Restate** | **BSL 1.1** → Apache-2.0 after 4 yrs (source-available, not OSI today) | Cloud free 50k actions/mo; ⚠ **paid per-action rate not publicly published** (quote-only) | Low-mod — single binary, embedded RocksDB/Bifrost (+ S3 for HA) | **No** — embedded storage, no Postgres backend (**fails the external-Postgres requirement**) | Very strong — durable Virtual Objects, promises/awakeables, Pydantic AI integration | **3** |

## Confidence & caveats

Double-check these before committing — they are the items the adversarial verification flagged as refuted, stale, or unverifiable:

- **Prefect (currency_ok=false):** Team-tier pricing was mischaracterized in the source. Corrected: **$400/mo base including 4 seats, +$100 per extra seat up to 8** (not "$100/user, 4-seat minimum"). The "~$0.005/min compute overage" figure is **unverified** — drop it or confirm with Prefect sales. Core OSS/self-host/feature claims are consistent with v3 docs.
- **Airflow (currency_ok=false):** the Astronomer Astro managed figures "$0.42/hr Team" and "$2.40/hr dedicated cluster" were **fabricated/stale**; rates are **size-based** (identical across plans), dedicated clusters are **$2.00/hr**. Airflow itself is fully Apache-2.0 — the correction is only to the third-party managed vendor.
- **Inngest (currency_ok=false):** the "v1.38.1 released 2026-07-21" claim is off by a year (that version is **July 2025**); the current latest-release/"confirmed current" status could not be verified. Redis is **recommended, not strictly mandatory** as the record overstated. License is **SSPL** (source-available, *not* OSI-approved) — a real hit against an OSS-first mandate. Managed pricing (Hobby $0 / Pro $99) **is** confirmed.
- **Trigger.dev:** self-host is **not** feature-equivalent to Cloud — **Checkpoints, warm starts, and auto-scaling are Cloud-only**, which undermines the "zero-idle-compute checkpoint promotion gate on self-host" story. The heavy service list (ClickHouse, MinIO, registry, 8 GB RAM) comes from community/blog docs, not the canonical self-hosting page — treat as unconfirmed.
- **Kestra:** managed pricing is genuinely **quote-only with no published rate card** (Cloud is new; a rate card could appear anytime — main staleness risk). A stale "v0.22 LTS" mention was wrong (active LTS lines are 1.0 and 1.3). **Kestra 1.3 requires Java 25** — an operational constraint not in the record. Retries/concurrency being OSS is **inferred** from core docs (not explicitly enumerated on the oss-vs-paid matrix), though retries were independently confirmed OSS.
- **Windmill:** the cited latest release (v1.761) is stale (actual ≈v1.766). The **decision-critical** item — concurrency/rate-limit being **EE-only** — was read from current docs but should be re-verified at purchase time, since it's the single feature that most directly addresses the TPM ceiling.
- **Dagster:** Pro/Enterprise remain **quote-only** (no public figures); prior bundled-credit amounts and the open concurrency-bug GitHub issues' fixed/open status were **not re-verified**.
- **Durable trio:** Temporal Enterprise/Mission-Critical, DBOS Cloud/Enterprise, and **Restate's paid per-action rate** are all **quote-only / not publicly published**. Restate Cloud's exact GA date ("2025-09-30") is **unverified** (state as "~Sept/Oct 2025"). Restate's **no-external-Postgres** limitation is a hard mismatch with the stated requirement.
- **General:** every "managed pricing" cell for an OSS-first-eligible tool is secondary to the **free self-hosted edition**, which is what actually gets adopted here — the managed figures are decision-relevant only if the team later offloads hosting.

## How this affects the Kestra spike

**Proceed with the spike — the evidence still supports it.** Kestra remains a verified 4/5 OSS-first fit: Apache-2.0, a single Docker container + external Postgres that satisfies the "state in Postgres" requirement natively, cron+backfills, task/flow retries with backoff, a Pause/approval gate for the blue-green alias swap, and a rich execution UI — a clean, low-refactor wrap of the existing CLI scripts. Make the spike explicitly answer its two known gaps: **(1)** prove the OpenAI 5M-TPM ceiling is held by in-script batch caps + retries, since Kestra's concurrency is flow-level, not token-aware; and **(2)** pin the version and confirm the **Java 25** runtime requirement for the 1.3 line. Keep **Prefect** staged as the ready fallback if the YAML/subprocess seam or the non-token-aware limiting proves too constraining (Prefect scored higher precisely on native async and server-side token-aware rate limits) — and keep **DBOS** on the radar as the future durable layer that could later absorb both jobs.