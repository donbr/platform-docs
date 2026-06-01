# Platform Docs Deployment Guide

## Overview

This project provides two parallel MCP server deployments for semantic documentation search:

| Endpoint | Collection | Embedding Model | API Key Required |
|----------|------------|-----------------|------------------|
| `platform-docs` | `platform-docs` | OpenAI text-embedding-3-small (1536d) | Yes (OpenAI) |
| `platform-docs-free` | `platform-docs-fastembed` | BAAI/bge-small-en-v1.5 (384d) | No |

Both contain 7,423 documents from 8 sources: Anthropic, LangChain, Prefect, FastMCP, McpProtocol, PydanticAI, Temporal, Zep.

---

## Deployment Configurations

### platform-docs (OpenAI Embeddings)

**FastMCP Cloud Settings:**
- **Name**: `platform-docs`
- **Entry Point**: `platform_docs_server.py:mcp`
- **Config File**: `fastmcp.json`

**Environment Variables Required:**
```
QDRANT_API_URL=https://your-instance.cloud.qdrant.io:6333
QDRANT_API_KEY=your-qdrant-key
OPENAI_API_KEY=sk-proj-your-openai-key
```

**Add to Claude Code:**
```bash
claude mcp add --scope user --transport http platform-docs https://platform-docs.fastmcp.app/mcp
```

---

### platform-docs-free (FastEmbed - No API Key)

**FastMCP Cloud Settings:**
- **Name**: `platform-docs-free`
- **Entry Point**: `platform_docs_fastembed_server.py:mcp`
- **Config File**: `fastmcp-fastembed.json`

**Environment Variables Required:**
```
QDRANT_API_URL=https://your-instance.cloud.qdrant.io:6333
QDRANT_API_KEY=your-qdrant-key
```
*No OpenAI API key needed!*

**Add to Claude Code:**
```bash
claude mcp add --scope user --transport http platform-docs-free https://platform-docs-free.fastmcp.app/mcp
```

---

## Data Refresh Process

### Step 1: Download Fresh Documentation
```bash
uv run scripts/download_llms_raw.py
```
Downloads `llms.txt` and `llms-full.txt` from 12 sources to `data/raw/`.

### Step 2: Split into Pages
```bash
uv run scripts/split_llms_pages.py
```
Parses documentation into individual JSON files in `data/interim/pages/`.

### Step 3: Upload to Collections

**For OpenAI collection (platform-docs):**
```bash
uv run scripts/upload_to_qdrant.py --collection platform-docs
```

**For FastEmbed collection (platform-docs-fastembed):**
```bash
uv run scripts/upload_to_qdrant_fastembed.py --collection platform-docs-fastembed
```

### Step 4: Verify Upload
Use the validation queries below to confirm both collections are working.

---

## Validation Test Queries

Use these queries to test the MCP servers in Claude Code:

### Test 1: List Sources
```
Use platform-docs to list all available documentation sources.
```
**Expected**: 8 sources, 7,423 total documents

### Test 2: Basic Search
```
Search platform-docs for "how to create a FastMCP server"
```
**Expected**: FastMCP documentation results with code examples

### Test 3: Source Filtering
```
Search platform-docs for "API authentication" filtering to Anthropic only
```
**Expected**: Anthropic-specific results about API keys and headers

### Test 4: Cross-Source Query
```
Using platform-docs, find documentation about durable execution and workflows
```
**Expected**: Results from Temporal and possibly LangChain

### Test 5: Free Server Comparison
```
Use platform-docs-free to search for "RAG agent implementation"
```
**Expected**: Similar results to platform-docs (may differ in ranking)

### Test 6: Large Document Search
```
Search platform-docs for "Claude extended thinking" with k=10
```
**Expected**: Multiple Anthropic docs about thinking and reasoning features

---

## Troubleshooting

### "No results found for source filter"
The payload index might not be created. Run:
```python
from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSchemaType

client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_KEY)
client.create_payload_index(
    collection_name="platform-docs",
    field_name="metadata.source_name",
    field_schema=PayloadSchemaType.KEYWORD,
)
```

### First query is slow on platform-docs-free
FastEmbed downloads the model on first use (~50MB). Subsequent queries are fast.

### OpenAI rate limit errors during upload

The default `--batch-size 100 --workers 4` hits OpenAI's 5M-tokens-per-minute (TPM) limit on `text-embedding-3-small` during full corpus uploads. Failed batches surface as 429 errors and are **not retried** by the script — they're simply skipped, leaving the collection short.

Empirical settings (full-corpus refresh, ~7K pages):

| Settings | Outcome |
|---|---|
| `--batch-size 100 --workers 4` (default) | ~26 batches fail with 429 (~35% of corpus missing) |
| `--batch-size 25 --workers 2` | ~8 batches still fail (~200 docs missing), all from large-doc sources |
| `--batch-size 10 --workers 1` | Clean — zero failures, ~3 min per 1000 large docs |

**Heuristic:** parallelism budget scales inversely with average page size. Anthropic pages average ~65 K chars (~16 K tokens) and burn TPM fastest; Temporal pages average ~2 K chars (~500 tokens) and tolerate more parallelism. If a batch averages over ~5 K tokens, drop to `--workers 1`.

**Recovery pattern** when only specific sources are short after a partial upload:

```bash
# 1. Delete the partial source from Qdrant by ID (the script uses auto-generated UUIDs,
#    so re-uploading without a delete creates duplicates).
uv run python -c "
import os; from pathlib import Path; from dotenv import load_dotenv
from qdrant_client import QdrantClient
load_dotenv(Path('.env'))
c = QdrantClient(url=os.environ['QDRANT_API_URL'], api_key=os.environ['QDRANT_API_KEY'], prefer_grpc=True)
ids=[]; offset=None
while True:
    pts, offset = c.scroll(collection_name='platform-docs', limit=1000, offset=offset, with_payload=True, with_vectors=False)
    ids += [p.id for p in pts if (p.payload or {}).get('metadata',{}).get('source_name')=='Anthropic']
    if offset is None: break
c.delete(collection_name='platform-docs', points_selector=ids)
"

# 2. Re-upload only that source, serial.
uv run scripts/upload_to_qdrant.py --sources Anthropic --batch-size 10 --workers 1
```

### Qdrant Cloud gRPC connection resets during FastEmbed upload

FastEmbed has no API rate limits, but Qdrant Cloud's gRPC endpoint can reset connections under heavy parallel writes (errors like `recvmsg:Connection reset by peer` or `sendmsg: Broken pipe`). Same `--batch-size 50 --workers 2` recovery pattern applies; rebuilds at this setting have been clean.

---

## Architecture Summary

```
GitHub Repo (donbr/graphiti-qdrant)
    │
    ├── data/
    │   ├── raw/           ← Downloaded llms.txt files
    │   ├── interim/pages/ ← Split JSON documents
    │   └── processed/     ← Upload manifests
    │
    ├── Scripts
    │   ├── download_llms_raw.py
    │   ├── split_llms_pages.py
    │   ├── upload_to_qdrant.py (OpenAI)
    │   └── upload_to_qdrant_fastembed.py (Free)
    │
    └── MCP Servers
        ├── platform_docs_server.py → platform-docs.fastmcp.app
        └── platform_docs_fastembed_server.py → platform-docs-free.fastmcp.app
```

Both servers connect to Qdrant Cloud where the vector collections are stored.
