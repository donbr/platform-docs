# Kestra Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Kestra can run the platform-docs ETL unattended with retries, a promotion gate that blocks bad alias swaps, and Postgres-backed run telemetry — writing the 608 OpenAI/Vue/Supabase docs into isolated POC collections without touching production.

**Architecture:** A Kestra flow (local Docker) calls the existing `scripts/*.py` as subprocess tasks. Run state is written **natively** via Kestra's Postgres JDBC-Query tasks into an `orchestration.pipeline_runs` table in a **dedicated local Docker Postgres** (`platform_docs` database) — no custom state module, no cloud dependency. Two Python helpers (`verify_counts.py`, `alias_swap.py`) handle Qdrant-side work — count verification and the guarded sandbox alias swap — because JDBC cannot reach Qdrant. Kestra's own metadata lives in an isolated `kestra_system` schema in the same local database. Production `*-v2` collections and aliases are never touched.

> **State store scope:** the spike uses a local Docker Postgres for self-containment (no cloud creds, disposable). The longer-term intent of a cloud state store (Supabase) is preserved for **Sub-project B (production cutover)** — the schema and JDBC-Query design here port to Supabase unchanged by swapping only the connection URL.

**Tech Stack:** Kestra (Docker) + `io.kestra.plugin.jdbc.postgresql`, local Postgres (`pgvector/pgvector:pg16`), `qdrant-client`, existing `uv`-run ETL scripts, pytest.

## Global Constraints

- Python `>=3.11` (repo `requires-python`); run everything via `uv run`.
- Qdrant client is `QdrantClient(url=QDRANT_API_URL, api_key=QDRANT_API_KEY)` — reuse this; read both from env.
- **Never** write to production collections (`platform-docs-v2`, `platform-docs-fastembed-v2`) or production aliases (`platform-docs`, `platform-docs-fastembed`). POC uses collections `platform-docs-poc-v1` / `platform-docs-poc-fastembed-v1` and sandbox aliases `platform-docs-poc-active` / `platform-docs-fastembed-poc-active` only.
- The local `platform_docs` Postgres database's schemas are isolated: telemetry in `orchestration`, Kestra internals in `kestra_system`.
- Existing ETL scripts are called as subprocesses; do not rewrite them. **Exact interfaces (verified against the repo):**
  - `scripts/download_llms_raw.py` — no args, downloads ALL sources.
  - `scripts/split_llms_pages.py` — no args, splits ALL sources.
  - `scripts/upload_to_qdrant.py` (OpenAI 1536d) and `scripts/upload_to_qdrant_fastembed.py` (FastEmbed 384d) — both accept `--sources` (`nargs="+"`, space-separated, **case-sensitive**), `--collection`, `--batch-size`, `--workers`, `--dry-run`. There is **no `--model` flag** and **no single `upload.py`**.
- **Batch sizing is a hard requirement, not a default:** happy-path uploads MUST use `--batch-size 25 --workers 2`. `--batch-size 100` hits OpenAI's 5M TPM ceiling and causes the exact silent-skip failure this spike exists to catch (see CLAUDE.md "Pitfall 6").
- Spike code lives under `spikes/kestra/`. Secrets come from env / Kestra secrets; never commit real keys (only `.env.example` placeholders).

---

### Task 1: POC config module

**Files:**
- Create: `spikes/kestra/poc_config.py`
- Test: `spikes/kestra/tests/test_poc_config.py`

**Interfaces:**
- Produces: `POC_SOURCES: list[str]`, `POC_COLLECTION: str`, `POC_COLLECTION_FASTEMBED: str`, `POC_ALIAS: str`, `POC_ALIAS_FASTEMBED: str`, `PROD_ALIASES: frozenset[str]`, `PROD_COLLECTIONS: frozenset[str]`, `expected_doc_count(sources: list[str], pages_dir: Path) -> int`

- [ ] **Step 1: Write the failing test**

```python
# spikes/kestra/tests/test_poc_config.py
import json
from pathlib import Path

from spikes.kestra import poc_config


def test_constants_never_reference_production():
    for c in (poc_config.POC_COLLECTION, poc_config.POC_COLLECTION_FASTEMBED):
        assert c not in poc_config.PROD_COLLECTIONS
    for a in (poc_config.POC_ALIAS, poc_config.POC_ALIAS_FASTEMBED):
        assert a not in poc_config.PROD_ALIASES
        assert a.endswith("-poc-active")


def test_expected_doc_count_counts_json_excluding_manifest(tmp_path: Path):
    src = tmp_path / "OpenAI"
    src.mkdir()
    (src / "0001.json").write_text(json.dumps({"content": "x"}))
    (src / "0002.json").write_text(json.dumps({"content": "y"}))
    (src / "manifest.json").write_text(json.dumps({"page_count": 2}))
    assert poc_config.expected_doc_count(["OpenAI"], tmp_path) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_poc_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spikes'` / attribute errors.

- [ ] **Step 3: Write minimal implementation**

```python
# spikes/kestra/poc_config.py
"""Shared constants and helpers for the Kestra spike. Production-safe by design."""
from pathlib import Path

POC_SOURCES = ["OpenAI", "Vue", "Supabase"]
POC_COLLECTION = "platform-docs-poc-v1"
POC_COLLECTION_FASTEMBED = "platform-docs-poc-fastembed-v1"
POC_ALIAS = "platform-docs-poc-active"
POC_ALIAS_FASTEMBED = "platform-docs-fastembed-poc-active"

PROD_ALIASES = frozenset({"platform-docs", "platform-docs-fastembed"})
PROD_COLLECTIONS = frozenset({"platform-docs-v2", "platform-docs-fastembed-v2"})

DEFAULT_PAGES_DIR = Path(__file__).resolve().parents[2] / "data" / "interim" / "pages"


def expected_doc_count(sources: list[str], pages_dir: Path = DEFAULT_PAGES_DIR) -> int:
    """Count split page JSON files (excluding manifest.json) across the given sources."""
    total = 0
    for source in sources:
        source_dir = pages_dir / source
        if not source_dir.is_dir():
            continue
        total += sum(1 for f in source_dir.glob("*.json") if f.name != "manifest.json")
    return total
```

Also create empty `spikes/__init__.py`, `spikes/kestra/__init__.py`, `spikes/kestra/tests/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest spikes/kestra/tests/test_poc_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add spikes/__init__.py spikes/kestra/__init__.py spikes/kestra/tests/__init__.py \
        spikes/kestra/poc_config.py spikes/kestra/tests/test_poc_config.py
git commit -m "feat(spike): add POC config module with production-safety constants"
```

---

### Task 2: Postgres schema (telemetry + Kestra isolation)

**Files:**
- Create: `spikes/kestra/sql/001_orchestration_schema.sql`
- Test: `spikes/kestra/tests/test_schema_sql.py`

**Interfaces:**
- Produces: table `orchestration.pipeline_runs` (per spec) and schema `kestra_system`.

- [ ] **Step 1: Write the failing test** (static assertions — no DB in CI)

```python
# spikes/kestra/tests/test_schema_sql.py
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "sql" / "001_orchestration_schema.sql").read_text().lower()


def test_creates_isolated_schemas():
    assert "create schema if not exists orchestration" in SQL
    assert "create schema if not exists kestra_system" in SQL


def test_pipeline_runs_has_required_columns():
    for col in ["run_id", "flow", "source", "stage", "status", "environment",
                "docs_expected", "docs_uploaded", "collection_version",
                "alias_swapped_at", "started_at", "finished_at", "error"]:
        assert col in SQL, f"missing column: {col}"


def test_environment_defaults_to_poc():
    assert "default 'poc'" in SQL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_schema_sql.py -v`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the SQL**

```sql
-- spikes/kestra/sql/001_orchestration_schema.sql
-- Isolate Kestra's internal metadata (Kestra config targets currentSchema=kestra_system)
create schema if not exists kestra_system;

-- Custom telemetry schema
create schema if not exists orchestration;

create table if not exists orchestration.pipeline_runs (
  run_id             uuid primary key,
  flow               text not null,
  source             text,
  stage              text,          -- download | split | upload | verify | alias_swap
  status             text not null, -- running | success | failed
  environment        text not null default 'poc',  -- poc | staging | prod
  docs_expected      integer,
  docs_uploaded      integer,
  collection_version text,
  alias_swapped_at   timestamptz,
  started_at         timestamptz not null default now(),
  finished_at        timestamptz,
  error              text
);

create index if not exists idx_pipeline_runs_env_status
  on orchestration.pipeline_runs (environment, status);
create index if not exists idx_pipeline_runs_started
  on orchestration.pipeline_runs (started_at desc);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest spikes/kestra/tests/test_schema_sql.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Start the local Postgres and apply the schema**

The dedicated `postgres` service is defined in Task 6, but the schema must exist before Kestra starts (Kestra creates its tables inside `kestra_system`). Start just Postgres first, then apply — both schemas are created here.

Run:
```bash
docker compose -f spikes/kestra/docker-compose.yml up -d postgres
sleep 5
# PLATFORM_DOCS_DB_URL = postgresql://kestra:kestra@localhost:5433/platform_docs  (see Task 6 .env)
psql "$PLATFORM_DOCS_DB_URL" -f spikes/kestra/sql/001_orchestration_schema.sql
psql "$PLATFORM_DOCS_DB_URL" -c "select count(*) from orchestration.pipeline_runs;"
```
Expected: schema applies cleanly; the count is `0` (table exists, empty).

> Note: this step depends on Task 6's compose file existing. If executing strictly top-to-bottom, create `spikes/kestra/docker-compose.yml` and `.env` (Task 6, Steps 1–2) before running this, then return here. The commit for this task still only includes the SQL + its test.

- [ ] **Step 6: Commit**

```bash
git add spikes/kestra/sql/001_orchestration_schema.sql spikes/kestra/tests/test_schema_sql.py
git commit -m "feat(spike): add orchestration + kestra_system Postgres schema"
```

---

### Task 3: Verification gate (`verify_counts.py`)

**Files:**
- Create: `spikes/kestra/verify_counts.py`
- Test: `spikes/kestra/tests/test_verify_counts.py`

**Interfaces:**
- Consumes: env `QDRANT_API_URL` / `QDRANT_API_KEY`.
- Produces: `is_complete(actual: int, expected: int) -> bool`; CLI `python -m spikes.kestra.verify_counts --collection ... --expected N` — exits `0` when complete, `1` on shortfall (the circuit breaker).

- [ ] **Step 1: Write the failing test** (pure comparison)

```python
# spikes/kestra/tests/test_verify_counts.py
from spikes.kestra import verify_counts


def test_is_complete_true_when_actual_meets_expected():
    assert verify_counts.is_complete(608, 608) is True
    assert verify_counts.is_complete(609, 608) is True


def test_is_complete_false_when_short():
    assert verify_counts.is_complete(140, 608) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_verify_counts.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# spikes/kestra/verify_counts.py
"""Promotion gate: exit 1 unless the POC collection holds >= expected docs."""
import argparse
import os
import sys

from qdrant_client import QdrantClient


def is_complete(actual: int, expected: int) -> bool:
    return actual >= expected


def qdrant_count(collection: str) -> int:
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    return client.count(collection_name=collection, exact=True).count


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", required=True)
    p.add_argument("--expected", type=int, required=True)
    args = p.parse_args()
    actual = qdrant_count(args.collection)
    ok = is_complete(actual, args.expected)
    print(f"verify_counts: collection={args.collection} actual={actual} expected={args.expected} ok={ok}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest spikes/kestra/tests/test_verify_counts.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add spikes/kestra/verify_counts.py spikes/kestra/tests/test_verify_counts.py
git commit -m "feat(spike): add Qdrant count verification gate"
```

---

### Task 4: Guarded sandbox alias swap (`alias_swap.py`)

**Files:**
- Create: `spikes/kestra/alias_swap.py`
- Test: `spikes/kestra/tests/test_alias_swap.py`

**Interfaces:**
- Consumes: `poc_config`, env `QDRANT_API_URL` / `QDRANT_API_KEY`.
- Produces: `assert_sandbox_alias(name: str) -> None` (raises `ValueError` for any production alias or non-`-poc-active` name); CLI `python -m spikes.kestra.alias_swap --alias ... --collection ...`.

- [ ] **Step 1: Write the failing test** (safety guard)

```python
# spikes/kestra/tests/test_alias_swap.py
import pytest

from spikes.kestra import alias_swap


def test_guard_rejects_production_aliases():
    for name in ("platform-docs", "platform-docs-fastembed"):
        with pytest.raises(ValueError):
            alias_swap.assert_sandbox_alias(name)


def test_guard_allows_both_poc_aliases():
    alias_swap.assert_sandbox_alias("platform-docs-poc-active")
    alias_swap.assert_sandbox_alias("platform-docs-fastembed-poc-active")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_alias_swap.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# spikes/kestra/alias_swap.py
"""Point a SANDBOX alias at a POC collection. Refuses to touch production aliases."""
import argparse
import os

from qdrant_client import QdrantClient
from qdrant_client.models import CreateAlias, CreateAliasOperation

from spikes.kestra import poc_config


def assert_sandbox_alias(name: str) -> None:
    if name in poc_config.PROD_ALIASES or not name.endswith("-poc-active"):
        raise ValueError(f"refusing to swap non-sandbox alias: {name!r}")


def swap(alias: str, collection: str) -> None:
    assert_sandbox_alias(alias)
    client = QdrantClient(url=os.environ["QDRANT_API_URL"], api_key=os.environ["QDRANT_API_KEY"])
    client.update_collection_aliases(change_aliases_operations=[
        CreateAliasOperation(create_alias=CreateAlias(collection_name=collection, alias_name=alias)),
    ])
    print(f"alias_swap: {alias} -> {collection}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--alias", default=poc_config.POC_ALIAS)
    p.add_argument("--collection", default=poc_config.POC_COLLECTION)
    args = p.parse_args()
    swap(args.alias, args.collection)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest spikes/kestra/tests/test_alias_swap.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add spikes/kestra/alias_swap.py spikes/kestra/tests/test_alias_swap.py
git commit -m "feat(spike): add guarded sandbox alias swap (both collections)"
```

---

### Task 5: Kestra flow YAML (reconciled)

**Files:**
- Create: `spikes/kestra/flows/platform_docs_poc.yaml`

**Interfaces:**
- Consumes: the four helpers above (via `uv run`), the ETL scripts, and three Postgres JDBC secrets (Task 6). Input `expected_doc_count` (INT, default 608) — bump to 999 to exercise the failure gate (Task 7).
- Produces: a validated, loadable flow `platform_docs.poc`.

Design notes baked in:
- **State is written natively** by `io.kestra.plugin.jdbc.postgresql.Query` tasks (per-stage rows), not a Python module.
- **TPM safety comes from `--batch-size 25 --workers 2`**, NOT from flow concurrency. The flow-level `concurrency` block only serializes whole-flow executions; its comment says exactly that.
- Both collections are uploaded, verified, and alias-swapped.

- [ ] **Step 1: Write the flow**

```yaml
# spikes/kestra/flows/platform_docs_poc.yaml
id: poc
namespace: platform_docs
description: "Spike ETL for platform-docs into POC sandbox collections/aliases; validates the promotion gate."

inputs:
  - id: expected_doc_count
    type: INT
    defaults: 608   # OpenAI(139) + Vue(92) + Supabase(377). Set 999 to force the gate to fail.

variables:
  openai_collection: "platform-docs-poc-v1"
  fastembed_collection: "platform-docs-poc-fastembed-v1"
  openai_alias: "platform-docs-poc-active"
  fastembed_alias: "platform-docs-fastembed-poc-active"

# Serialize whole-flow executions (NOT a TPM control — batch-size/workers do that)
concurrency:
  behavior: QUEUE
  limit: 1

# Postgres connection for all JDBC state tasks (secrets configured in Task 6)
pluginDefaults:
  - type: io.kestra.plugin.jdbc.postgresql.Query
    values:
      url: "{{ secret('PLATFORM_DOCS_JDBC_URL') }}"

tasks:
  - id: record_run_start
    type: io.kestra.plugin.jdbc.postgresql.Query
    sql: |
      INSERT INTO orchestration.pipeline_runs
        (run_id, flow, stage, status, environment, collection_version, docs_expected)
      VALUES
        ('{{ execution.id }}', '{{ flow.id }}', 'download', 'running', 'poc', 'v1', {{ inputs.expected_doc_count }});

  - id: download_sources
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    retry:
      type: constant
      interval: PT30S
      maxAttempt: 3
    commands:
      - uv run scripts/download_llms_raw.py

  - id: split_docs
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    commands:
      - uv run scripts/split_llms_pages.py

  - id: update_state_uploading
    type: io.kestra.plugin.jdbc.postgresql.Query
    sql: |
      UPDATE orchestration.pipeline_runs SET stage = 'upload'
      WHERE run_id = '{{ execution.id }}';

  - id: upload_openai
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    retry:
      type: constant
      interval: PT1M
      maxAttempt: 3
    commands:
      - uv run scripts/upload_to_qdrant.py --sources OpenAI Vue Supabase --collection {{ vars.openai_collection }} --batch-size 25 --workers 2

  - id: upload_fastembed
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    retry:
      type: constant
      interval: PT1M
      maxAttempt: 3
    commands:
      - uv run scripts/upload_to_qdrant_fastembed.py --sources OpenAI Vue Supabase --collection {{ vars.fastembed_collection }} --batch-size 25 --workers 2

  - id: update_state_verifying
    type: io.kestra.plugin.jdbc.postgresql.Query
    sql: |
      UPDATE orchestration.pipeline_runs SET stage = 'verify'
      WHERE run_id = '{{ execution.id }}';

  # THE GATE: either verify exits non-zero -> flow fails -> alias swaps skipped -> errors handler runs.
  - id: verify_counts
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    commands:
      - uv run python -m spikes.kestra.verify_counts --collection {{ vars.openai_collection }} --expected {{ inputs.expected_doc_count }}
      - uv run python -m spikes.kestra.verify_counts --collection {{ vars.fastembed_collection }} --expected {{ inputs.expected_doc_count }}

  - id: alias_swap
    type: io.kestra.plugin.scripts.shell.Commands
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    commands:
      - uv run python -m spikes.kestra.alias_swap --alias {{ vars.openai_alias }} --collection {{ vars.openai_collection }}
      - uv run python -m spikes.kestra.alias_swap --alias {{ vars.fastembed_alias }} --collection {{ vars.fastembed_collection }}

  - id: record_run_success
    type: io.kestra.plugin.jdbc.postgresql.Query
    sql: |
      UPDATE orchestration.pipeline_runs
      SET status = 'success', stage = 'alias_swap', finished_at = NOW(),
          alias_swapped_at = NOW(), docs_uploaded = {{ inputs.expected_doc_count }}
      WHERE run_id = '{{ execution.id }}';

errors:
  - id: record_run_failure
    type: io.kestra.plugin.jdbc.postgresql.Query
    sql: |
      UPDATE orchestration.pipeline_runs
      SET status = 'failed', finished_at = NOW(),
          error = 'flow failed before promotion'
      WHERE run_id = '{{ execution.id }}';
```

> **Version-drift checklist (verify against current Kestra via Context7 during Task 6 `flow validate`):** `taskRunner:` block (older Kestra used `runner: PROCESS`); `pluginDefaults:` (older/newer may spell it `taskDefaults`/`pluginDefaults`); the postgresql `Query` task for DML (some versions want `fetchType: NONE` or a `Queries` task). These are the expected drift points — adjust rather than treat as plan defects.

- [ ] **Step 2: Commit (validation happens in Task 6 once the stack is up)**

```bash
git add spikes/kestra/flows/platform_docs_poc.yaml
git commit -m "feat(spike): add reconciled Kestra flow (JDBC state, real commands, dual collection)"
```

---

### Task 6: Docker stack (dedicated local Postgres + Kestra) + secret

**Files:**
- Create: `spikes/kestra/docker-compose.yml`
- Create: `spikes/kestra/.env.example`
- Create: `spikes/kestra/README.md`

**Interfaces:**
- Consumes: `SECRET_PLATFORM_DOCS_JDBC_URL` (base64 JDBC for the flow's state tasks), plus `QDRANT_API_URL` / `QDRANT_API_KEY` / `OPENAI_API_KEY` for the shell tasks. Kestra's own backend creds are hardcoded to the local `postgres` service.
- Produces: a private Postgres (`platform_docs` db) on host port `5433`, Kestra at `http://localhost:8080` with metadata in `kestra_system`, repo mounted at `/app`, flow loaded.

Design: a dedicated `postgres` service (not the shared `agent-memory-postgres`) — fully isolated, its own named volume, disposable. Kestra and the flow's JDBC tasks reach it over the compose network as `postgres:5432`; host-side `psql` reaches it at `localhost:5433`.

> **Version pin (research-flagged, July 2026):** do NOT use `kestra/kestra:latest` — pin an explicit tag for reproducibility. Two specifics the research surfaced:
> - Use the **`-full`** image variant (`kestra/kestra:<version>-full`) so the `io.kestra.plugin.jdbc.postgresql` plugin the flow's state tasks depend on is bundled; the slim base image ships fewer plugins.
> - **Kestra 1.3+ bundles Java 25 inside the image**, so no host JDK is needed for this Docker-based spike. The Java-25 requirement only bites if you ever run the bare Kestra JAR outside Docker (not the case here) — but pin the version so a silent base-image bump can't change the runtime under you.
>
> `v1.3-full` is a placeholder for the current stable 1.3-line tag — **confirm the exact patch tag** on Docker Hub (`kestra/kestra` tags) or via Context7 (`kestra`) before the run, and record the resolved tag in the README.

- [ ] **Step 1: Write the compose file**

```yaml
# spikes/kestra/docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: platform_docs
      POSTGRES_USER: kestra
      POSTGRES_PASSWORD: kestra
    ports:
      - "5433:5432"          # host 5433 (5432 is taken by agent-memory-postgres)
    volumes:
      - pg_spike:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U kestra -d platform_docs"]
      interval: 5s
      timeout: 3s
      retries: 10

  kestra:
    image: kestra/kestra:v1.3-full   # PINNED (research-flagged) — NOT :latest. See "Version pin" note below.
    command: server standalone
    user: root
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "8080:8080"
    environment:
      KESTRA_CONFIGURATION: |
        kestra:
          repository:
            type: postgres
          queue:
            type: postgres
        datasources:
          postgres:
            url: jdbc:postgresql://postgres:5432/platform_docs?currentSchema=kestra_system
            username: kestra
            password: kestra
    env_file:
      - .env
    volumes:
      - ../../:/app          # mount repo so `uv run scripts/...` works in-container
    working_dir: /app

volumes:
  pg_spike:
```

- [ ] **Step 2: Write `.env.example` and README**

```bash
# spikes/kestra/.env.example  (copy to .env and fill; NEVER commit real keys)

# --- Secret consumed by the flow's JDBC state tasks ---
# Base64 of the LOCAL orchestration-schema JDBC URL (reachable from inside the kestra container):
# generate: echo -n 'jdbc:postgresql://postgres:5432/platform_docs?user=kestra&password=kestra&currentSchema=orchestration' | base64 -w0
SECRET_PLATFORM_DOCS_JDBC_URL=__base64_value__

# --- Host-side connection for psql (schema apply + runbook assertions) ---
PLATFORM_DOCS_DB_URL=postgresql://kestra:kestra@localhost:5433/platform_docs

# --- Plain env for the shell/ETL tasks (uv run scripts) ---
QDRANT_API_URL=__set_me__
QDRANT_API_KEY=__set_me__
OPENAI_API_KEY=__set_me__
```

`spikes/kestra/README.md` documents, in order: (1) `cp .env.example .env` and fill — `SECRET_PLATFORM_DOCS_JDBC_URL` is the **base64** of the local `orchestration`-schema JDBC URL (that is how Kestra exposes `{{ secret('PLATFORM_DOCS_JDBC_URL') }}`); the state tasks run inside the kestra container, so the host is `postgres`, not `localhost`; (2) start Postgres + apply schema (Task 2 Step 5); (3) `docker compose -f spikes/kestra/docker-compose.yml up -d`; (4) ensure `uv` in-container (Step 4); (5) load the flow (Step 5). Because Postgres uses a named volume (`pg_spike`), the DB survives `down`; use `down -v` to reset.

- [ ] **Step 3: Bring the full stack up and verify Kestra ↔ local Postgres**

Run:
```bash
docker compose -f spikes/kestra/docker-compose.yml up -d
sleep 20
curl -sf http://localhost:8080/api/v1/version && echo " <- kestra up"
psql "$PLATFORM_DOCS_DB_URL" -c "select count(*) from information_schema.tables where table_schema='kestra_system';"
```
Expected: version JSON prints; `kestra_system` table count `> 0` (Kestra created its tables in the isolated schema, leaving `orchestration` for telemetry only). If the count is `0`, Kestra failed to migrate — check `docker compose ... logs kestra` for a datasource/schema error.

- [ ] **Step 4: Ensure `uv` in the container**

Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra bash -lc 'which uv || (curl -LsSf https://astral.sh/uv/install.sh | sh)'`
Then: `docker compose -f spikes/kestra/docker-compose.yml exec kestra uv --version`
Expected: a version prints. Document whichever path worked in the README.

- [ ] **Step 5: Validate AND load the flow**

Mounting the YAML does not register it — load it into the namespace before Tasks 7/8.

Run:
```bash
docker compose -f spikes/kestra/docker-compose.yml exec kestra \
  kestra flow validate /app/spikes/kestra/flows/platform_docs_poc.yaml
docker compose -f spikes/kestra/docker-compose.yml exec kestra \
  kestra flow namespace update platform_docs /app/spikes/kestra/flows
curl -sf http://localhost:8080/api/v1/flows/platform_docs/poc >/dev/null && echo loaded
```
Expected: `valid`, namespace update reports `platform_docs.poc`, `loaded` prints. If `validate` errors, reconcile against the Task 5 version-drift checklist via Context7.

- [ ] **Step 6: Commit**

```bash
git add spikes/kestra/docker-compose.yml spikes/kestra/.env.example spikes/kestra/README.md
git commit -m "feat(spike): add Kestra docker stack + dedicated local Postgres backend"
```

---

### Task 7: Deliberate failure test (circuit-breaker proof)

**Files:**
- Create: `spikes/kestra/tests/test_failure_gate.md` (manual runbook — integration test vs live infra)

**Interfaces:**
- Consumes: running stack (Task 6); input `expected_doc_count=999`.

- [ ] **Step 1: Record the current sandbox alias targets (baseline)**

Run:
```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')]
print(a or 'NONE')"
```
Expected: `NONE` (first run) or prior POC targets. Note it.

- [ ] **Step 2: Execute the flow with a forced shortfall**

The download/split/upload tasks succeed (608 real docs land), but `verify_counts` compares 608 against 999 and exits non-zero.

Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra kestra flow execute platform_docs poc --inputs '{"expected_doc_count": 999}'`

- [ ] **Step 3: Assert the gate tripped and NO swap happened**

Run:
```bash
# (a) flow failed -> latest pipeline_runs row is 'failed'
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status, stage, error from orchestration.pipeline_runs order by started_at desc limit 1;"
# (b) alias targets UNCHANGED from Step 1 baseline
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')]
print(a or 'NONE')"
```
Expected: (a) latest row `status=failed`; (b) alias targets identical to Step 1 (no swap). **Success criterion #2.**

- [ ] **Step 4: Commit the runbook**

```bash
git add spikes/kestra/tests/test_failure_gate.md
git commit -m "test(spike): add circuit-breaker failure runbook (expected=999)"
```

---

### Task 8: End-to-end happy-path run (real payload)

**Files:**
- Create: `spikes/kestra/tests/test_happy_path.md` (manual runbook)

**Interfaces:**
- Consumes: running stack; uploads 608 docs into both POC collections.

- [ ] **Step 1: Execute the flow with defaults (`expected_doc_count=608`)**

Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra kestra flow execute platform_docs poc`
Expected: all tasks green through `alias_swap` and `record_run_success`.

- [ ] **Step 2: Assert BOTH POC collections are EXACTLY complete — the TPM-hold / no-silent-loss check**

This is the research-flagged assertion. Because Kestra's concurrency is **flow-level, not token-aware** (Workstream 1 finding), the OpenAI 5M-TPM ceiling is held ENTIRELY by the in-script `--batch-size 25 --workers 2` caps plus task retries — Kestra itself does not throttle tokens. This step proves those settings held with zero silent batch loss (the original pain point).

Run:
```bash
# (a) exact counts must EQUAL the expected 608 (not merely >=). A short count = batches were silently dropped.
for c in platform-docs-poc-v1 platform-docs-poc-fastembed-v1; do
  echo -n "$c: "
  curl -s -H "api-key: $QDRANT_API_KEY" -H "Content-Type: application/json" \
    -X POST "$QDRANT_API_URL/collections/$c/points/count" -d '{"exact":true}'
done
```
Expected (a): both counts **exactly `608`**.

Then (b) — confirm the upload scripts reported no dropped batches. In the Kestra UI (Executions → this run → Gantt), open the `upload_openai` and `upload_fastembed` task logs and read the tail: each ends with `Successful: N documents`. Confirm **N equals that task's uploaded total and there is NO `Failed: <n> batches` line with n>0** (the silent-skip path in `upload_to_qdrant*.py`). Equivalent API check:
```bash
# tail the two upload tasks' logs and look for any non-zero failed-batch line
curl -s -H "api-key: $QDRANT_API_KEY" "http://localhost:8080/api/v1/executions/search?namespace=platform_docs&flowId=poc&size=1" >/dev/null
# then inspect logs in the UI; or: docker compose ... logs kestra | grep -E 'Successful:|Failed: [1-9]'
docker compose -f spikes/kestra/docker-compose.yml logs kestra 2>/dev/null | grep -E 'Successful:|Failed: [1-9]' | tail -20
```
Expected (b): `Successful:` lines present for both uploads; **no `Failed: [1-9]…` line**. If (a) is short or (b) shows failures, the TPM/gRPC settings did not hold — drop to `--batch-size 10 --workers 1` for the large source (Supabase) per CLAUDE.md "Pitfall 6" and re-run before this step counts as passing. **This is the empirical proof that the orchestrated pipeline fixed the silent-skip bug — success criterion #1.**

- [ ] **Step 3: Assert both sandbox aliases point at their POC collections**

Run:
```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'].endswith('-poc-active')])"
```
Expected: `platform-docs-poc-active -> platform-docs-poc-v1` and `platform-docs-fastembed-poc-active -> platform-docs-poc-fastembed-v1`.

- [ ] **Step 4: Assert telemetry complete + production untouched**

Run:
```bash
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status, docs_expected, docs_uploaded, alias_swapped_at from orchestration.pipeline_runs order by started_at desc limit 1;"
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" | python3 -c "import sys,json
print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'] in ('platform-docs','platform-docs-fastembed')])"
```
Expected: latest row `status=success`, `docs_uploaded=608`, `alias_swapped_at` set; production aliases STILL point at `platform-docs-v2` / `platform-docs-fastembed-v2` (unchanged). **Success criteria #1, #3, #4.**

- [ ] **Step 5: Commit the runbook**

```bash
git add spikes/kestra/tests/test_happy_path.md
git commit -m "test(spike): add end-to-end happy-path runbook"
```

---

## Notes for the executor

- **State DB (decided):** a dedicated local Docker Postgres (`postgres` service in Task 6's compose, `platform_docs` db, host port `5433`) — NOT the shared `agent-memory-postgres` container (that belongs to another project; reusing it would couple lifecycles and share its connection budget). Both `orchestration` and `kestra_system` schemas live in this local db. Cloud Supabase is deferred to Sub-project B, where only the connection URL changes.
- **Reconciliation record:** state is written by native JDBC-Query tasks (adopted from the reviewed draft) rather than a Python `state.py` — fewer moving parts, more Kestra-idiomatic, and it removes the `psycopg` dependency. The tradeoff: the flow needs the postgresql plugin (bundled in the pinned `kestra/kestra:<version>-full` image — Task 6) and a base64 Kestra secret for the connection (Task 6). The failure test uses the draft's cleaner `expected_doc_count=999` override instead of a batch-size hack.
- **Command layer is verified against the repo** — real script names, `--sources` as `nargs="+"` (space-separated, case-sensitive `OpenAI Vue Supabase`), `--collection`, and mandatory `--batch-size 25 --workers 2`. There is no `--model` flag and no `scripts/upload.py`/`verify.py`/`alias_swap.py` in `scripts/` (the last two are created under `spikes/kestra/`).
- **Kestra version drift** is expected at the YAML property level (see Task 5 checklist); confirm via Context7 (`kestra`) at `flow validate` time, not by assuming the plan is wrong.
