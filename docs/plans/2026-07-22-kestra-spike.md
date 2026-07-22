# Kestra Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove Kestra can run the platform-docs ETL unattended with retries, a promotion gate that blocks bad alias swaps, and Supabase-backed run telemetry — writing 608 real docs into isolated POC collections without touching production.

**Architecture:** A Kestra flow (local Docker) calls the existing `scripts/*.py` as subprocess tasks. Small Python helpers write run state to a Supabase Postgres `orchestration.pipeline_runs` table, verify Qdrant counts before promotion, and re-point a sandbox alias. Kestra's own metadata lives in an isolated `kestra_system` schema in the same Supabase. Production `*-v2` collections and their aliases are never touched.

**Tech Stack:** Kestra (Docker), Supabase Postgres, `psycopg` (v3), `qdrant-client`, existing `uv`-run ETL scripts, pytest.

## Global Constraints

- Python `>=3.11` (repo `requires-python`); run everything via `uv run`.
- Qdrant client is constructed as `QdrantClient(url=QDRANT_API_URL, api_key=QDRANT_API_KEY)` — reuse this pattern; read both from env.
- **Never** write to production collections (`platform-docs-v2`, `platform-docs-fastembed-v2`) or production aliases (`platform-docs`, `platform-docs-fastembed`). POC uses `platform-docs-poc-v1` / `platform-docs-poc-fastembed-v1` and alias `platform-docs-poc-active` only.
- Supabase schemas are isolated: custom telemetry in `orchestration`, Kestra internals in `kestra_system`.
- The existing ETL scripts are called as subprocesses; do not rewrite them. The upload script already supports `--sources`, `--collection`, `--batch-size`, `--workers`, `--dry-run`.
- All spike code lives under `spikes/kestra/`. Secrets come from env / `.env`; never commit real keys (only `.env.example` placeholders).

---

### Task 1: POC config module

**Files:**
- Create: `spikes/kestra/poc_config.py`
- Test: `spikes/kestra/tests/test_poc_config.py`

**Interfaces:**
- Produces: `POC_SOURCES: list[str]`, `POC_COLLECTION: str`, `POC_COLLECTION_FASTEMBED: str`, `POC_ALIAS: str`, `PROD_ALIASES: frozenset[str]`, `PROD_COLLECTIONS: frozenset[str]`, `expected_doc_count(sources: list[str], pages_dir: Path) -> int`

- [ ] **Step 1: Write the failing test**

```python
# spikes/kestra/tests/test_poc_config.py
import json
from pathlib import Path

from spikes.kestra import poc_config


def test_constants_never_reference_production():
    assert poc_config.POC_COLLECTION not in poc_config.PROD_COLLECTIONS
    assert poc_config.POC_ALIAS not in poc_config.PROD_ALIASES
    assert poc_config.POC_ALIAS.endswith("-poc-active")


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

Also create empty `spikes/__init__.py`, `spikes/kestra/__init__.py`, and `spikes/kestra/tests/__init__.py` so the package imports resolve.

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

### Task 2: Supabase schema (telemetry + Kestra isolation)

**Files:**
- Create: `spikes/kestra/sql/001_orchestration_schema.sql`
- Test: `spikes/kestra/tests/test_schema_sql.py`

**Interfaces:**
- Produces: table `orchestration.pipeline_runs` with columns per spec; schema `kestra_system` for Kestra's internal metadata.

- [ ] **Step 1: Write the failing test** (static assertions on the SQL file — no DB needed in CI)

```python
# spikes/kestra/tests/test_schema_sql.py
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "sql" / "001_orchestration_schema.sql").read_text()


def test_creates_isolated_schemas():
    assert "create schema if not exists orchestration" in SQL.lower()
    assert "create schema if not exists kestra_system" in SQL.lower()


def test_pipeline_runs_has_required_columns():
    for col in ["run_id", "flow", "source", "stage", "status", "environment",
                "docs_expected", "docs_uploaded", "collection_version",
                "alias_swapped_at", "started_at", "finished_at", "error"]:
        assert col in SQL, f"missing column: {col}"


def test_environment_defaults_to_poc():
    assert "default 'poc'" in SQL.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_schema_sql.py -v`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Write the SQL**

```sql
-- spikes/kestra/sql/001_orchestration_schema.sql
create schema if not exists orchestration;
create schema if not exists kestra_system;  -- Kestra's own metadata; isolated from telemetry

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

create index if not exists pipeline_runs_flow_started_idx
  on orchestration.pipeline_runs (flow, started_at desc);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest spikes/kestra/tests/test_schema_sql.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Apply to Supabase and verify**

Apply the SQL to the chosen Supabase project (via the Supabase SQL editor, `psql "$PLATFORM_DOCS_DB_URL" -f spikes/kestra/sql/001_orchestration_schema.sql`, or the Supabase MCP `apply_migration`). Then verify:

Run: `psql "$PLATFORM_DOCS_DB_URL" -c "select count(*) from orchestration.pipeline_runs;"`
Expected: `0` (table exists, empty).

- [ ] **Step 6: Commit**

```bash
git add spikes/kestra/sql/001_orchestration_schema.sql spikes/kestra/tests/test_schema_sql.py
git commit -m "feat(spike): add orchestration + kestra_system Supabase schema"
```

---

### Task 3: Run-state helper (`state.py`)

**Files:**
- Create: `spikes/kestra/state.py`
- Test: `spikes/kestra/tests/test_state.py`
- Modify: `pyproject.toml` (add `psycopg[binary]>=3.2` to a `spike` dependency group)

**Interfaces:**
- Consumes: env `PLATFORM_DOCS_DB_URL` (Supabase Postgres connection string).
- Produces:
  - `build_start_row(run_id: str, flow: str, source: str | None, stage: str, environment: str = "poc") -> dict`
  - `build_finish_updates(status: str, docs_expected: int | None, docs_uploaded: int | None, collection_version: str | None, alias_swapped_at: str | None, error: str | None) -> dict`
  - CLI: `python -m spikes.kestra.state start|finish|fail --run-id ... [--flow ... --stage ... --source ... --status ... --docs-expected ... --docs-uploaded ... --error ...]`

- [ ] **Step 1: Write the failing test** (pure row-builders — no DB)

```python
# spikes/kestra/tests/test_state.py
from spikes.kestra import state


def test_build_start_row_defaults_status_running_and_env_poc():
    row = state.build_start_row(run_id="r1", flow="poc", source="OpenAI", stage="upload")
    assert row["run_id"] == "r1"
    assert row["status"] == "running"
    assert row["environment"] == "poc"
    assert row["stage"] == "upload"


def test_build_finish_updates_only_includes_provided_fields():
    updates = state.build_finish_updates(
        status="failed", docs_expected=608, docs_uploaded=140,
        collection_version=None, alias_swapped_at=None, error="boom",
    )
    assert updates["status"] == "failed"
    assert updates["docs_uploaded"] == 140
    assert updates["error"] == "boom"
    assert "collection_version" not in updates  # None fields dropped
    assert "finished_at" in updates  # always stamped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_state.py -v`
Expected: FAIL — module/attributes missing.

- [ ] **Step 3: Write minimal implementation**

```python
# spikes/kestra/state.py
"""Write pipeline run state to Supabase orchestration.pipeline_runs.

Row-builders are pure (unit-tested). DB writes are thin and integration-verified.
"""
import argparse
import os

import psycopg


def build_start_row(run_id, flow, source, stage, environment="poc"):
    return {
        "run_id": run_id,
        "flow": flow,
        "source": source,
        "stage": stage,
        "status": "running",
        "environment": environment,
    }


def build_finish_updates(status, docs_expected, docs_uploaded,
                         collection_version, alias_swapped_at, error):
    candidate = {
        "status": status,
        "docs_expected": docs_expected,
        "docs_uploaded": docs_uploaded,
        "collection_version": collection_version,
        "alias_swapped_at": alias_swapped_at,
        "error": error,
    }
    updates = {k: v for k, v in candidate.items() if v is not None}
    updates["finished_at"] = "now()"
    return updates


def _connect():
    url = os.environ["PLATFORM_DOCS_DB_URL"]
    return psycopg.connect(url)


def insert_start(row):
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f"%({k})s" for k in row)
    sql = f"insert into orchestration.pipeline_runs ({cols}) values ({placeholders})"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, row)


def apply_finish(run_id, updates):
    sets, params = [], {"run_id": run_id}
    for k, v in updates.items():
        if v == "now()":
            sets.append(f"{k} = now()")
        else:
            sets.append(f"{k} = %({k})s")
            params[k] = v
    sql = f"update orchestration.pipeline_runs set {', '.join(sets)} where run_id = %(run_id)s"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["start", "finish", "fail"])
    p.add_argument("--run-id", required=True)
    p.add_argument("--flow", default="platform_docs_poc")
    p.add_argument("--stage", default="flow")
    p.add_argument("--source")
    p.add_argument("--status")
    p.add_argument("--docs-expected", type=int)
    p.add_argument("--docs-uploaded", type=int)
    p.add_argument("--collection-version")
    p.add_argument("--error")
    args = p.parse_args()

    if args.action == "start":
        insert_start(build_start_row(args.run_id, args.flow, args.source, args.stage))
    else:
        status = "failed" if args.action == "fail" else (args.status or "success")
        apply_finish(args.run_id, build_finish_updates(
            status, args.docs_expected, args.docs_uploaded,
            args.collection_version, None, args.error))


if __name__ == "__main__":
    main()
```

Add the dependency:

```toml
# pyproject.toml — add under [dependency-groups] (or [project.optional-dependencies])
spike = ["psycopg[binary]>=3.2"]
```

- [ ] **Step 4: Run tests + install dep**

Run: `uv sync --group spike && uv run pytest spikes/kestra/tests/test_state.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Integration smoke (writes one row)**

Run:
```bash
uv run python -m spikes.kestra.state start --run-id smoke-1 --stage flow
uv run python -m spikes.kestra.state finish --run-id smoke-1 --status success --docs-uploaded 0
psql "$PLATFORM_DOCS_DB_URL" -c "select run_id,status,finished_at from orchestration.pipeline_runs where run_id='smoke-1';"
```
Expected: one row, `status=success`, `finished_at` populated. Then clean up: `psql "$PLATFORM_DOCS_DB_URL" -c "delete from orchestration.pipeline_runs where run_id='smoke-1';"`

- [ ] **Step 6: Commit**

```bash
git add spikes/kestra/state.py spikes/kestra/tests/test_state.py pyproject.toml uv.lock
git commit -m "feat(spike): add Supabase run-state helper and CLI"
```

---

### Task 4: Verification gate (`verify_counts.py`)

**Files:**
- Create: `spikes/kestra/verify_counts.py`
- Test: `spikes/kestra/tests/test_verify_counts.py`

**Interfaces:**
- Consumes: `poc_config`, env `QDRANT_API_URL` / `QDRANT_API_KEY`.
- Produces: `is_complete(actual: int, expected: int) -> bool`; CLI `python -m spikes.kestra.verify_counts --collection ... --expected N` that exits `0` when complete, `1` on mismatch (the circuit breaker).

- [ ] **Step 1: Write the failing test** (pure comparison)

```python
# spikes/kestra/tests/test_verify_counts.py
from spikes.kestra import verify_counts


def test_is_complete_true_when_actual_meets_expected():
    assert verify_counts.is_complete(608, 608) is True
    assert verify_counts.is_complete(609, 608) is True  # >= expected is ok


def test_is_complete_false_when_short():
    assert verify_counts.is_complete(140, 608) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest spikes/kestra/tests/test_verify_counts.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# spikes/kestra/verify_counts.py
"""Promotion gate: fail (exit 1) unless the POC collection holds >= expected docs."""
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

### Task 5: Sandbox alias swap (`alias_swap.py`)

**Files:**
- Create: `spikes/kestra/alias_swap.py`
- Test: `spikes/kestra/tests/test_alias_swap.py`

**Interfaces:**
- Consumes: `poc_config`, env `QDRANT_API_URL` / `QDRANT_API_KEY`.
- Produces: `assert_sandbox_alias(name: str) -> None` (raises `ValueError` for any production alias); CLI `python -m spikes.kestra.alias_swap --alias ... --collection ...` that points the alias at the collection.

- [ ] **Step 1: Write the failing test** (safety guard)

```python
# spikes/kestra/tests/test_alias_swap.py
import pytest

from spikes.kestra import alias_swap


def test_guard_rejects_production_aliases():
    with pytest.raises(ValueError):
        alias_swap.assert_sandbox_alias("platform-docs")
    with pytest.raises(ValueError):
        alias_swap.assert_sandbox_alias("platform-docs-fastembed")


def test_guard_allows_poc_alias():
    alias_swap.assert_sandbox_alias("platform-docs-poc-active")  # no raise
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
from qdrant_client.models import CreateAliasOperation, CreateAlias

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
git commit -m "feat(spike): add guarded sandbox alias swap"
```

---

### Task 6: Kestra flow YAML

**Files:**
- Create: `spikes/kestra/flows/platform_docs_poc.yaml`

**Interfaces:**
- Consumes: all helpers above via `uv run` subprocess commands; a boolean flow input `force_failure` (drives Task 8's failure test).
- Produces: a validated Kestra flow `platform_docs.poc`.

- [ ] **Step 1: Write the flow**

```yaml
# spikes/kestra/flows/platform_docs_poc.yaml
id: poc
namespace: platform_docs

inputs:
  - id: force_failure
    type: BOOLEAN
    defaults: false

variables:
  runId: "{{ execution.id }}"
  collection: "platform-docs-poc-v1"
  alias: "platform-docs-poc-active"
  sources: "OpenAI Vue Supabase"

tasks:
  - id: start_state
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - uv run python -m spikes.kestra.state start --run-id "{{ vars.runId }}" --stage flow

  - id: download
    type: io.kestra.plugin.scripts.shell.Commands
    retry:
      type: constant
      interval: PT30S
      maxAttempt: 3
    commands:
      - uv run scripts/download_llms_raw.py

  - id: split
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - uv run scripts/split_llms_pages.py

  - id: upload_openai
    type: io.kestra.plugin.scripts.shell.Commands
    # concurrency cap (OpenAI 5M TPM) via conservative batch/workers per CLAUDE.md
    concurrent:
      limit: 1
    retry:
      type: constant
      interval: PT1M
      maxAttempt: 3
    commands:
      - >-
        uv run scripts/upload_to_qdrant.py --sources {{ vars.sources }}
        --collection {{ vars.collection }} --batch-size 25 --workers 2
        {{ inputs.force_failure ? '--batch-size 0' : '' }}

  - id: verify_counts
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - >-
        EXPECTED=$(uv run python -c "from spikes.kestra import poc_config as c; print(c.expected_doc_count(c.POC_SOURCES))")
        && uv run python -m spikes.kestra.verify_counts --collection {{ vars.collection }} --expected "$EXPECTED"

  - id: alias_swap
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - uv run python -m spikes.kestra.alias_swap --alias {{ vars.alias }} --collection {{ vars.collection }}

  - id: finish_state
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - uv run python -m spikes.kestra.state finish --run-id "{{ vars.runId }}" --status success

errors:
  - id: fail_state
    type: io.kestra.plugin.scripts.shell.Commands
    commands:
      - uv run python -m spikes.kestra.state fail --run-id "{{ vars.runId }}" --error "flow failed"
```

> Note: `verify_counts` exits non-zero on a short count, which fails the flow → `alias_swap` never runs and the `errors` handler records `failed`. That is the circuit breaker. `--batch-size 0` under `force_failure` produces zero uploaded docs, guaranteeing the gate trips (Task 8).

- [ ] **Step 2: Validate the flow syntax against the running Kestra (deferred to Task 7 stack)**

After Task 7 brings the stack up:
Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra kestra flow validate /app/spikes/kestra/flows/platform_docs_poc.yaml`
Expected: `✓ flow ... is valid` (if the current Kestra version renamed a property, confirm against Kestra docs via Context7 and adjust — plugin task type / retry keys are the likely drift points).

- [ ] **Step 3: Commit**

```bash
git add spikes/kestra/flows/platform_docs_poc.yaml
git commit -m "feat(spike): add Kestra flow with retries, concurrency cap, and verify gate"
```

---

### Task 7: Docker stack (Kestra → Supabase backend)

**Files:**
- Create: `spikes/kestra/docker-compose.yml`
- Create: `spikes/kestra/.env.example`
- Create: `spikes/kestra/README.md`

**Interfaces:**
- Consumes: `PLATFORM_DOCS_DB_URL` (Supabase), `QDRANT_API_URL`, `QDRANT_API_KEY`, `OPENAI_API_KEY`.
- Produces: a local Kestra reachable at `http://localhost:8080`, using the `kestra_system` schema for its metadata, with the repo mounted at `/app`.

- [ ] **Step 1: Write the compose file**

```yaml
# spikes/kestra/docker-compose.yml
services:
  kestra:
    image: kestra/kestra:latest
    command: server standalone
    user: root
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
            url: ${KESTRA_DB_JDBC_URL}   # jdbc:postgresql://<supabase-host>:5432/postgres?currentSchema=kestra_system
            username: ${KESTRA_DB_USER}
            password: ${KESTRA_DB_PASSWORD}
    env_file:
      - .env
    volumes:
      - ../../:/app          # mount repo so `uv run scripts/...` works in-container
    working_dir: /app
```

- [ ] **Step 2: Write `.env.example` and README**

```bash
# spikes/kestra/.env.example  (copy to .env and fill; NEVER commit real keys)
KESTRA_DB_JDBC_URL=jdbc:postgresql://db.<project>.supabase.co:5432/postgres?currentSchema=kestra_system
KESTRA_DB_USER=postgres
KESTRA_DB_PASSWORD=__set_me__
PLATFORM_DOCS_DB_URL=postgresql://postgres:__set_me__@db.<project>.supabase.co:5432/postgres
QDRANT_API_URL=__set_me__
QDRANT_API_KEY=__set_me__
OPENAI_API_KEY=__set_me__
```

`spikes/kestra/README.md` documents: apply Task 2 SQL first; `cp .env.example .env` and fill; `docker compose -f spikes/kestra/docker-compose.yml up -d`; open `http://localhost:8080`; the container has `uv` available (install step below if the base image lacks it).

- [ ] **Step 3: Bring the stack up and verify Kestra ↔ Supabase**

Run:
```bash
docker compose -f spikes/kestra/docker-compose.yml up -d
sleep 20
curl -sf http://localhost:8080/api/v1/version && echo " <- kestra up"
psql "$PLATFORM_DOCS_DB_URL" -c "select count(*) from information_schema.tables where table_schema='kestra_system';"
```
Expected: version JSON prints; the `kestra_system` table count is `> 0` (Kestra created its tables in the isolated schema). **Risk-retirement checkpoint:** if Kestra's connection load to cloud Supabase is unacceptable, fall back to a local `postgres` service for the `kestra` datasource and keep Supabase for `PLATFORM_DOCS_DB_URL` telemetry only — record the decision in the README.

- [ ] **Step 4: Ensure `uv` in the container**

If `docker compose ... exec kestra which uv` is empty, add an install step to the README (`exec kestra bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'`) or switch the image to one with uv preinstalled. Verify: `docker compose -f spikes/kestra/docker-compose.yml exec kestra uv --version`.

- [ ] **Step 5: Validate AND load the flow into Kestra**

Mounting the YAML at `/app` does not register it — the flow must be uploaded to the namespace before Tasks 8/9 can execute it by id.

Run:
```bash
docker compose -f spikes/kestra/docker-compose.yml exec kestra \
  kestra flow validate /app/spikes/kestra/flows/platform_docs_poc.yaml
docker compose -f spikes/kestra/docker-compose.yml exec kestra \
  kestra flow namespace update platform_docs /app/spikes/kestra/flows
```
Expected: `valid`, then the update reports `platform_docs.poc` created/updated. Confirm it is loaded: `curl -sf http://localhost:8080/api/v1/flows/platform_docs/poc >/dev/null && echo loaded`.

- [ ] **Step 6: Commit**

```bash
git add spikes/kestra/docker-compose.yml spikes/kestra/.env.example spikes/kestra/README.md
git commit -m "feat(spike): add Kestra docker stack wired to isolated Supabase schema"
```

---

### Task 8: Deliberate failure test (circuit breaker proof)

**Files:**
- Create: `spikes/kestra/tests/test_failure_gate.md` (manual verification runbook — this is an integration test against live infra)

**Interfaces:**
- Consumes: the running stack (Task 7), the flow input `force_failure`.

- [ ] **Step 1: Record the current sandbox alias target (baseline)**

Run:
```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" \
  | python3 -c "import sys,json; a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name']=='platform-docs-poc-active']; print(a or 'NONE')"
```
Expected: `NONE` (first run) or the prior POC collection. Note it.

- [ ] **Step 2: Execute the flow with `force_failure=true`**

Trigger from the Kestra UI (Executions → New) or CLI:
Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra kestra flow execute platform_docs poc --inputs '{"force_failure": true}'`

- [ ] **Step 3: Assert the gate tripped and the swap did NOT happen**

Run:
```bash
# (a) verify_counts failed -> flow failed -> pipeline_runs has a 'failed' row
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status,error from orchestration.pipeline_runs order by started_at desc limit 1;"
# (b) alias target is UNCHANGED from Step 1 baseline
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" \
  | python3 -c "import sys,json; a=[x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name']=='platform-docs-poc-active']; print(a or 'NONE')"
```
Expected: (a) latest row `status=failed`; (b) alias target identical to the Step 1 baseline (no swap). **This is success criterion #2.**

- [ ] **Step 4: Commit the runbook**

```bash
git add spikes/kestra/tests/test_failure_gate.md
git commit -m "test(spike): add circuit-breaker failure runbook"
```

---

### Task 9: End-to-end happy-path run (real payload)

**Files:**
- Create: `spikes/kestra/tests/test_happy_path.md` (manual verification runbook)

**Interfaces:**
- Consumes: the running stack; uploads the 608 OpenAI/Vue/Supabase docs into `platform-docs-poc-v1`.

- [ ] **Step 1: Execute the flow with defaults (`force_failure=false`)**

Run: `docker compose -f spikes/kestra/docker-compose.yml exec kestra kestra flow execute platform_docs poc`
Expected: all tasks green through `alias_swap` and `finish_state`.

- [ ] **Step 2: Assert POC collection populated (>= 608)**

Run:
```bash
curl -s -H "api-key: $QDRANT_API_KEY" -H "Content-Type: application/json" \
  -X POST "$QDRANT_API_URL/collections/platform-docs-poc-v1/points/count" -d '{"exact":true}'
```
Expected: `count >= 608`.

- [ ] **Step 3: Assert sandbox alias now points at the POC collection**

Run:
```bash
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" \
  | python3 -c "import sys,json; print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name']=='platform-docs-poc-active'])"
```
Expected: `platform-docs-poc-active -> platform-docs-poc-v1`.

- [ ] **Step 4: Assert telemetry + production untouched**

Run:
```bash
psql "$PLATFORM_DOCS_DB_URL" -c \
  "select status,docs_expected,docs_uploaded from orchestration.pipeline_runs order by started_at desc limit 1;"
curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_API_URL/aliases" \
  | python3 -c "import sys,json; print([x for x in json.load(sys.stdin)['result']['aliases'] if x['alias_name'] in ('platform-docs','platform-docs-fastembed')])"
```
Expected: latest row `status=success` with `docs_uploaded >= 608`; production aliases STILL point at `platform-docs-v2` / `platform-docs-fastembed-v2` (unchanged). **Success criteria #1, #3, #4.**

- [ ] **Step 5: Commit the runbook + final notes**

```bash
git add spikes/kestra/tests/test_happy_path.md
git commit -m "test(spike): add end-to-end happy-path runbook"
```

---

## Notes for the executor

- **Prerequisite decision (before Task 2):** choose the Supabase project for `PLATFORM_DOCS_DB_URL`. A dedicated `platform-docs` Supabase project is cleanest; do NOT reuse the `aie9-grading` project's default schema. Both `orchestration` and `kestra_system` schemas can live in whichever project is chosen.
- **Kestra version drift:** Task 6's YAML uses stable core/shell plugin task types, but plugin property names occasionally change between Kestra releases. If `flow validate` complains, confirm the current syntax via Context7 (`kestra`) before hand-editing — this is expected, not a plan defect.
- **`upload_to_qdrant_fastembed.py`** (the 384d collection) is intentionally out of the happy-path flow to keep the spike single-collection; add a parallel `upload_fastembed` task targeting `platform-docs-poc-fastembed-v1` once the OpenAI path is green if you want to exercise both.
