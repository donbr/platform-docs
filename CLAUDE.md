# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Platform-docs is an ETL pipeline and MCP server deployment for semantic documentation search. It downloads, processes, and indexes developer documentation from multiple sources, then serves semantic search via FastMCP servers.

**Core Components:**
- **ETL Pipeline** (`scripts/`) - Download → Split → Upload workflow
- **MCP Servers** (`src/`) - Two servers with different embedding strategies
- **Qdrant Collections** - `platform-docs` (OpenAI) and `platform-docs-fastembed` (free)

## Environment Setup

```bash
uv sync                    # Install dependencies
cp .env.example .env       # Configure credentials
```

**Required Environment Variables:**
- `QDRANT_API_URL` - Qdrant Cloud URL
- `QDRANT_API_KEY` - Qdrant API key
- `OPENAI_API_KEY` - For OpenAI embeddings (optional for FastEmbed server)

## Common Commands

### ETL Pipeline
```bash
uv run scripts/download_llms_raw.py           # Download all sources
uv run scripts/split_llms_pages.py            # Split into pages
uv run scripts/upload_to_qdrant.py            # Upload (OpenAI embeddings)
uv run scripts/upload_to_qdrant_fastembed.py  # Upload (FastEmbed, free)

# Upload specific source only
uv run scripts/upload_to_qdrant.py --sources GoogleADK

# Dry run (no actual upload)
uv run scripts/upload_to_qdrant.py --dry-run
```

**Parallelism note:** the default `--batch-size 100 --workers 4` hits OpenAI's 5M TPM on full-corpus uploads and can also trigger Qdrant Cloud gRPC connection resets on FastEmbed. Failed batches are **silently skipped** (script exits 0 with a `Successful: N` count). For full refreshes, prefer `--batch-size 25 --workers 2`; for large-doc sources (Anthropic alone), use `--batch-size 10 --workers 1`. See `docs/VECTOR_DB_REFRESH_GUIDE.md` "Pitfall 6" for the full recovery pattern.

### Testing
```bash
uv run pytest tests/test_mcp_server.py -v     # In-memory tests
uv run python run_http_server.py               # HTTP server for testing
```

### Deployment
```bash
fastmcp deploy fastmcp.json                    # Deploy OpenAI server
fastmcp deploy fastmcp-fastembed.json          # Deploy FastEmbed server
```

## Architecture

### ETL Pipeline Stages

1. **Download** (`download_llms_raw.py`)
   - Async downloads from 13+ documentation sources
   - Outputs to `data/raw/{Source}/llms-full.txt`
   - Generates `manifest.json` with download status

2. **Split** (`split_llms_pages.py`)
   - Three splitting strategies based on source format (exact source→strategy mapping lives in the `SOURCES_WITH_URL` / `SOURCES_HEADER_ONLY` / `SOURCES_MULTI_LEVEL` lists at the top of the file):
     - URL Pattern: `# Title\nSource: URL` (LangChain, Anthropic, Prefect, FastMCP, McpProtocol)
     - Header-Only: `# Title` (PydanticAI, Zep, GoogleADK)
     - Multi-Level: `#` and `##` headers (Temporal)
   - Pages shorter than `MIN_CONTENT_LENGTH` (200 chars) are **dropped** as API-reference stubs so they don't dominate top-k for short queries.
   - Outputs to `data/interim/pages/{Source}/*.json`

3. **Upload** (`upload_to_qdrant.py` or `upload_to_qdrant_fastembed.py`)
   - Batch embedding and upload to Qdrant
   - Parallel processing with ThreadPoolExecutor
   - OpenAI embedding config is centralized in `scripts/embedding_config.py` (`get_embeddings()`, text-embedding-3-small @ 1536d)
   - Per-page metadata written to Qdrant: `source_name`, `title`, `source_url`, `doc_id` (`{source_name}_{page_num}`), plus hierarchy fields for multi-level sources

### MCP Servers

| Server | Collection | Embeddings | API Key Required |
|--------|------------|------------|------------------|
| `src/platform_docs/` | `platform-docs` | OpenAI text-embedding-3-small (1536d) | Yes |
| `src/platform_docs_free/` | `platform-docs-fastembed` | BAAI/bge-small-en-v1.5 (384d) | No |

Both `server.py` files define the same two tools but point at different collections/embeddings; keep them in sync when changing tool behavior.

**Tools Exposed:**
- `search_docs(query, k, source)` - Semantic search with optional filtering. `k` is clamped to 1–20 (default 5); previews are truncated to the first 1000 chars.
- `list_sources()` - List available sources with document counts (scrolls the whole collection counting `metadata.source_name`)

**Source filtering caveat:** `source=` filters on the `metadata.source_name` payload key. If Qdrant has no payload index on that field, the filtered search raises "Index required" — the server catches this and falls back to an unfiltered search (fetching `k*3`) then filters in Python. For reliable/efficient filtering, create a payload index on `metadata.source_name` in the collection.

## Adding New Documentation Sources

1. Add source URL to `SOURCES` dict in `scripts/download_llms_raw.py`
2. Add source to appropriate pattern list in `scripts/split_llms_pages.py`:
   - `SOURCES_WITH_URL` - Has `Source: URL` line
   - `SOURCES_HEADER_ONLY` - Just `# Title` headers
   - `SOURCES_MULTI_LEVEL` - Splits on `#` and `##`
3. Run the pipeline: download → split → upload

## Data Directory Structure

```
data/
├── raw/                    # Downloaded llms.txt files
│   ├── {Source}/
│   │   ├── llms.txt
│   │   └── llms-full.txt
│   └── manifest.json
├── interim/pages/          # Split pages
│   ├── {Source}/
│   │   └── *.json
│   └── manifest.json
└── processed/              # Upload manifests
    └── upload_manifest.json
```

## Testing Strategy

- **In-memory tests** - FastMCP Client tests (fast, no network server)
- **HTTP tests** - Transport layer validation
- Run tests before deployment to catch issues early

## Orchestration (Kestra spike)

The ETL can be run **unattended** under Kestra (chosen orchestrator; see the head-to-head vs Prefect in the research doc below). The spike lives in `spikes/kestra/` and is a validated, local, self-contained proof-of-concept — **not yet the production path**.

- **What it does:** a Kestra flow (`spikes/kestra/flows/platform_docs_poc.yaml`) runs download → split → upload (both collections) → **verify gate** → alias promote, with task retries, run telemetry in Postgres (`orchestration.pipeline_runs`), and optional Google Drive reporting. The verify gate blocks the alias swap on any doc-count shortfall (proven in the failure runbook).
- **Production safety:** the spike writes to **POC collections only** (`platform-docs-poc-v1` / `-fastembed-v1`) behind sandbox aliases (`*-poc-active`). It never touches the production `*-v2` collections or the `platform-docs` / `platform-docs-fastembed` aliases. `alias_swap.py` refuses any non-sandbox alias.
- **Run it & full setup:** see **`docs/guides/kestra-setup-walkthrough.md`** — bring-up steps, the 7 one-time gotchas, version pin, and the Google service-account browser flow. Stack: `docker compose -f spikes/kestra/docker-compose.yml` (local Postgres 127.0.0.1:5433 + Kestra `v1.3.29` 127.0.0.1:8080); dashboard http://localhost:8080/ui/ (basic auth; creds in gitignored `spikes/kestra/.env`).
- **Two facts to keep in mind:** Kestra concurrency is **flow-level, not token-aware** (OpenAI 5M TPM stays held by the in-script `--batch-size 25 --workers 2`); the upload is **not idempotent** (re-runs append duplicates — reset POC collections or key by `doc_id` before enabling the nightly `Schedule` trigger).
- **Google Sheets/Drive (why Kestra):** first-party `plugin-googleworkspace`. `flows/docs_stats_sheet.yaml` upserts a documentation-stats Sheet (via `spikes/kestra/docs_stats.py`); the POC flow's Drive report is gated on `upload_to_drive`. Both need the service account from the walkthrough.

## Reference Docs

- `docs/DEPLOYMENT_GUIDE.md` - FastMCP Cloud deployment details
- `docs/VECTOR_DB_REFRESH_GUIDE.md` - Full-corpus refresh runbook, including the batch-size/worker tuning and silent-skip recovery pattern (Pitfall 6)
- `docs/specs/2026-07-22-orchestration-evaluation-design.md` - Orchestration evaluation & Kestra spike design
- `docs/plans/2026-07-22-kestra-spike.md` - Task-by-task Kestra spike implementation plan
- `docs/research/2026-07-22-orchestration-comparison.md` - July-2026 orchestrator comparison + empirical Prefect-vs-Kestra head-to-head
- `docs/retrospectives/2026-07-22-kestra-spike-retrospective.md` - Hands-on assessment, production-readiness checklist, and bugs the spike surfaced
- `docs/guides/kestra-setup-walkthrough.md` - Kestra bring-up, gotchas, and the Google service-account browser flow
- `docs/guides/prefect-setup-walkthrough.md` - Prefect bring-up + comparative limitations (no native Sheets/Drive)
- `spikes/kestra/README.md` / `spikes/prefect/README.md` - How to run each orchestration spike
