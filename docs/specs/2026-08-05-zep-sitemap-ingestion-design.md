# Zep/Graphiti documentation ingestion — design

**Date:** 2026-08-05
**Status:** Approved, pending implementation plan
**Supersedes the Zep backlog item in:** `docs/superpowers/specs/2026-06-21-unfiled-linear-issues.md`

## Problem

`help.getzep.com/llms-full.txt` is byte-identical to `llms.txt` — 2,079 bytes of
landing-page content. Zep discontinued the full-corpus file. The ETL is not
broken; its upstream contract evaporated silently.

Consequences:

- Zep contributes **1 page** to `platform-docs` and `platform-docs-fastembed`.
- **Zero Graphiti coverage.** Graphiti is the temporal knowledge-graph framework
  that underpins the user's Neo4j Aura / FalkorDB work; its absence is the
  defect that prompted this design.
- That single page carries `source_url: null`, because the header-only splitter
  has no URL to attach.

The failure survived two production alias promotions (v3, 2026-07-22) because the
verify gate checks only the **corpus total**. 8,163 looked correct; nobody counts
per source.

## Upstream research (2026-08-05)

Empirically verified, not assumed:

| Route | Finding |
|---|---|
| `sitemap.xml` | 294 URLs. Filtering `/v2/` and `/changelog/` leaves **196**: 83 root-level, 92 `sdk-reference`, 18 `graphiti`, 3 `memory-mcp-server`. |
| `{url}.md` | Returns clean markdown. **27/27 sampled sitemap URLs returned real content**, mean ~5 KB → ~1.5 MB corpus. Officially documented by Zep in the `/v2/welcome` header. |
| `docs-mcp.getzep.com/mcp` | `zep-docs-mcp v1.0.0`, tool `search_documentation`. **Cloud-biased.** A Graphiti `add_episode` query returned Zep Cloud's `/api/v2/graph/set-ontology`; a FalkorDB driver query returned the "Zep vs Graphiti" marketing page. Zep's own plugin page states it covers Zep Cloud only. |
| `help.getzep.com/_mcp/server` | `fern-docs-mcp-server v1.0.0`, tool `searchDocs`. **Accurate for Graphiti** — the same two probes returned `graphiti/core-concepts/custom-entity-and-edge-types` and `graphiti/configuration/falkor-db-configuration`. |
| Context7 | `/getzep/graphiti` (679 snippets, repo-derived) and `/websites/help_getzep_graphiti` (224 snippets). |

Two decisions fall out of this:

- **`/v2/` is the prior docs version** — a parallel copy of the same tree
  (welcome, concepts, quickstart, sdk-reference) including 29 explicitly
  deprecated pages. Ingesting it would double-index Zep with stale APIs, so a
  query like "add data to the graph" could surface deprecated v2 endpoints
  beside current ones. Excluded.
- **The advertised MCP is the wrong one.** The Fern-native endpoint is the one
  that answers Graphiti questions correctly.

## Design

### 1. `scripts/fetch_sitemap_pages.py` (new)

A sitemap-driven fetcher for sources that publish per-page markdown instead of a
concatenated `llms-full.txt`. Zep is the first consumer; the script is written to
take more.

- Reads `sitemap.xml`; applies include/exclude path-prefix rules per source.
- Fetches `{url}.md` concurrently with bounded parallelism and the browser UA the
  downloader already uses.
- **Treats a `# Page Not Found` body as a failure.** This endpoint returns
  **HTTP 200** for missing pages; status-code checking alone would silently index
  404 stubs. This is the single most important correctness detail in the script.
- Writes **directly** to `data/interim/pages/{Source}/*.json` in the existing
  page schema — `{title, source_url, content, content_length}` — with a real
  `source_url` per page.
- Applies `MIN_CONTENT_LENGTH` (200), rmtree's its own output directory before
  writing (mirroring `split_llms_pages.process_source`), and writes a manifest.
- Exits non-zero when the page yield falls below a configured floor. This is a
  *fetch-time* sanity check on one source (did the fetch itself work?), distinct
  from the pipeline-wide gate in item 5, which re-checks every source's count
  against a committed baseline before promotion. Both are wanted: the first fails
  fast at the network boundary, the second is the safety net that does not depend
  on any particular fetcher being correct.

**Why it bypasses the split stage:** the `.md` endpoint already returns one page
per file with its own URL. Concatenating those into a synthetic `llms-full.txt`
only to re-split them with a regex would reproduce the exact fragility that
caused this defect. The splitter's job is done upstream.

### 2. Retire the dead Zep path

Remove `Zep` from `SOURCES` in `download_llms_raw.py` and from
`SOURCES_HEADER_ONLY` in `split_llms_pages.py`. Without this, the splitter would
overwrite the fetched pages with its 1-page result — `process_source` rmtree's
the output directory for every source it handles. Once Zep is absent from the
splitter lists, the fetcher owns `data/interim/pages/Zep/` outright and there is
no ordering hazard between the two scripts.

### 3. Orchestration

Add a `fetch_sitemap_sources` task to `spikes/kestra/flows/prod_refresh.yaml`
between `split_docs` and `upload_bulk`, guarded by the same `rebuild` input and
retry policy as its neighbours. No other flow changes are needed:

- `Zep` is already in the flow's `bulk_sources` variable.
- `emit_expected --all` counts page directories, so the verify gate picks up the
  new total automatically.

### 4. Graphiti access layer

- Register `https://help.getzep.com/_mcp/server` as an MCP server.
- Add a thin skill that routes Graphiti questions: the Fern MCP for
  documentation, Context7 `/getzep/graphiti` for code-level detail.

**Explicitly out of scope:** ingesting the Graphiti repo (`mcp_server/`,
`examples/`, `CLAUDE.md`) as a new ETL source. It is the only Graphiti content in
neither the docs site nor Context7, but MCP + Context7 is the smaller reversible
choice. Revisit if code-level gaps appear in practice.

### 5. Per-source floor in the verify gate

Extend `spikes/kestra/verify_counts.py` with a per-source floor check driven by a
committed baseline, so any source collapsing below its expected count fails the
gate **before** an alias swap.

This is the durable fix. Items 1–3 repair Zep; item 5 is what stops the next
source from rotting unnoticed for a month. It would have caught both Zep and
Cursor.

## Known follow-up, deliberately not in this design

**Cursor has the same class of defect.** It downloads successfully (140 KB) but
is absent from every strategy list in `split_llms_pages.py`, so it yields zero
pages and reaches Qdrant not at all. The corpus indexes 14 of 15 configured
sources. Item 5 will surface it; fixing it is separate work.

## Impact on the next refresh

Zep goes from 1 → ~196 pages, so the expected corpus total moves from **8,163 to
roughly 8,360**. The next `prod_refresh` is therefore not a like-for-like
comparison with v3, and the count change is expected rather than alarming.

## Testing

- Unit: `# Page Not Found` bodies rejected despite HTTP 200; `/v2/` and
  `/changelog/` excluded by the path filter; pages under `MIN_CONTENT_LENGTH`
  dropped; page JSON matches the schema the uploader expects.
- Integration: a live fetch yields ~196 pages including all 18 `graphiti/` pages,
  each with a non-null `source_url`.
- Gate: per-source floor fails when a source is starved; passes at baseline.
- End-to-end: `prod_refresh` with `confirm=false` builds and verifies a v4
  collection without swapping.
