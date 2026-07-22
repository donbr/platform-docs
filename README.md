# platform-docs

Semantic documentation search via Qdrant vector store and FastMCP servers.

## Overview

This project provides an ETL pipeline and MCP servers for semantic search over developer documentation from multiple sources (Anthropic, LangChain, Prefect, FastMCP, PydanticAI, Zep, McpProtocol, Temporal, GoogleADK, and more).

**Two MCP Servers:**
- `platform-docs` - OpenAI embeddings (text-embedding-3-small, 1536d)
- `platform-docs-free` - FastEmbed (BAAI/bge-small-en-v1.5, 384d) - no API key required

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Edit .env with your Qdrant and OpenAI credentials

# 3. Run the ETL pipeline
uv run scripts/download_llms_raw.py    # Download documentation
uv run scripts/split_llms_pages.py     # Split into pages
uv run scripts/upload_to_qdrant.py     # Upload with OpenAI embeddings
# OR
uv run scripts/upload_to_qdrant_fastembed.py  # Upload with free FastEmbed
```

## Project Structure

```
platform-docs/
├── src/
│   ├── platform_docs/           # MCP server (OpenAI embeddings)
│   └── platform_docs_free/      # MCP server (FastEmbed, free)
├── scripts/
│   ├── download_llms_raw.py     # Stage 1: Download llms.txt files
│   ├── split_llms_pages.py      # Stage 2: Parse into pages
│   ├── upload_to_qdrant.py      # Stage 3a: Upload with OpenAI
│   └── upload_to_qdrant_fastembed.py  # Stage 3b: Upload with FastEmbed
├── tests/
│   └── test_mcp_server.py       # In-memory MCP tests
├── spikes/                      # Orchestration proofs-of-concept
│   ├── kestra/                  # Kestra flow + local stack (chosen orchestrator)
│   └── prefect/                 # Prefect equivalent (for the head-to-head)
├── data/                        # ETL data (gitignored)
│   ├── raw/                     # Downloaded llms.txt files
│   ├── interim/pages/           # Split documentation pages
│   └── processed/               # Upload manifests
├── fastmcp.json                 # Deploy config for OpenAI server
└── fastmcp-fastembed.json       # Deploy config for FastEmbed server
```

## ETL Pipeline

| Stage | Script | Output |
|-------|--------|--------|
| **Download** | `download_llms_raw.py` | `data/raw/{source}/llms-full.txt` |
| **Split** | `split_llms_pages.py` | `data/interim/pages/{source}/*.json` |
| **Upload** | `upload_to_qdrant.py` | Qdrant `platform-docs` collection |
| **Upload (Free)** | `upload_to_qdrant_fastembed.py` | Qdrant `platform-docs-fastembed` collection |

## Orchestration (Kestra)

The ETL can run **unattended** under [Kestra](https://kestra.io) — the chosen orchestrator after a hands-on evaluation against Prefect. A validated, local proof-of-concept lives in [`spikes/kestra/`](spikes/kestra/README.md): it runs download → split → upload → **verify gate** → alias promote, with retries, run history in Postgres, and optional Google Drive reporting. It writes to **sandbox POC collections only** — production collections and aliases are never touched.

```bash
cd spikes/kestra
cp .env.example .env            # fill in Qdrant/OpenAI keys + basic-auth creds
docker compose up -d postgres && psql "$PLATFORM_DOCS_DB_URL" -f sql/001_orchestration_schema.sql
docker compose up -d            # Kestra dashboard → http://localhost:8080/ui/
# load + run the flow: see spikes/kestra/README.md
```

Why Kestra over Prefect (both spiked end-to-end): native Google Sheets/Drive plugin, auth enforced by default, and a lighter single-container footprint — full analysis in [`docs/research/2026-07-22-orchestration-comparison.md`](docs/research/2026-07-22-orchestration-comparison.md) and the [retrospective](docs/retrospectives/2026-07-22-kestra-spike-retrospective.md).

## MCP Server Tools

Both servers expose the same tools:

- **`search_docs(query, k, source)`** - Semantic search with optional source filtering
- **`list_sources()`** - List available documentation sources with counts

## Testing

```bash
# Run in-memory tests
uv run pytest tests/test_mcp_server.py -v

# Run HTTP server for manual testing
uv run python run_http_server.py
```

## Deployment

Deploy to FastMCP Cloud:

```bash
# OpenAI embeddings server
fastmcp deploy fastmcp.json

# FastEmbed server (free)
fastmcp deploy fastmcp-fastembed.json
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QDRANT_API_URL` | Yes | Qdrant Cloud instance URL |
| `QDRANT_API_KEY` | Yes | Qdrant API key |
| `OPENAI_API_KEY` | For OpenAI server | OpenAI API key for embeddings |

## License

MIT License
