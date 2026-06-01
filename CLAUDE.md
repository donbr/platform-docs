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

## Reference Docs

- `docs/DEPLOYMENT_GUIDE.md` - FastMCP Cloud deployment details
- `docs/VECTOR_DB_REFRESH_GUIDE.md` - Full-corpus refresh runbook, including the batch-size/worker tuning and silent-skip recovery pattern (Pitfall 6)
