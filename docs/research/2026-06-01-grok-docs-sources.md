# Grok / xAI Documentation — Source Research Report

**Date:** 2026-06-01
**Method:** 4 parallel research scouts + synthesizer (see
`docs/superpowers/specs/2026-06-01-grok-docs-research-design.md`)
**Bottom line:** A clean, first-party, full-content source **exists and is
recommended**: `https://docs.x.ai/llms.txt`.

## Recommendation (TL;DR)

Ingest **`https://docs.x.ai/llms.txt`** — a single ~1.09 MB `text/plain` file
containing the **entire** Grok developer documentation corpus (120 pages) as
clean markdown, including the Skills and Connectors content. It is first-party,
always current, and needs no HTML scraping.

Three integration changes are required (the corpus does **not** drop cleanly
into the existing pipeline as-is — see "Integration requirements"):

1. **Browser User-Agent** on the downloader (the host returns a false 404 to
   bot UAs).
2. **Point Grok's `llms-full.txt` slot at the same `llms.txt` URL** (xAI does
   not publish a separate `llms-full.txt`; the `llms.txt` already holds full
   bodies).
3. **A new split parser** for the `===/path===` page-delimiter format, which
   none of the three existing strategies handles.

## What was verified (high confidence — bytes retrieved directly)

`curl` with a browser User-Agent, confirmed on 2026-06-01:

| Target | Result |
|--------|--------|
| `https://docs.x.ai/llms.txt` | **200**, `text/plain`, **1,093,745 bytes**, 31,354 lines |
| `https://docs.x.ai/llms-full.txt` | **404** (does not exist; redundant) |
| `https://docs.x.ai/developers/quickstart.md` | **200**, `text/markdown` (per-page `.md` works) |
| `https://docs.x.ai/sitemap.xml` | **200**, 124 URLs |
| `https://x.ai/llms.txt`, `https://grok.com/llms.txt` | not real (404 / consumer SPA shell) |

**Corpus shape:** 120 pages delimited by `===/<path>===` lines, each followed by
a `# Title` H1 and full markdown body (863 fenced code blocks total). Page-size
range 458 chars → ~46 KB; **0 pages below the 200-char stub floor**.

**Coverage by family:** 93 `/developers/*` (API reference, model capabilities,
tools, advanced API usage), 14 `/grok/*` (incl. all Connectors), 6 `/console/*`,
5 `/build/*` (incl. Skills), 1 `/overview`, 1 `/integrations/*`.

**Skills & Connectors (your named priorities) are all present in the corpus:**
`===/grok/connectors===` plus subpages `gmail-google-calendar`, `google-drive`,
`onedrive`, `outlook`, `microsoft-teams`, `sharepoint`, `salesforce`,
`custom-mcp-tunneling`, and `===/build/features/skills-plugins-marketplaces===`.

## The critical gotcha: User-Agent gating

`docs.x.ai` returns **HTTP 404 to non-browser User-Agents** (Python `httpx`
default, WebFetch, etc.) and **200 to browser-like UAs**. This produced a false
"not available" signal in three of four scouts and would silently break the
pipeline's downloader, which sends the default `httpx` UA. **Fix:** set a
browser `User-Agent` header on the `httpx.AsyncClient`.

## Alternatives considered (and why not)

- **Per-page `.md` + sitemap crawl** (`{path}.md` × 124 URLs) — works, clean, but
  124 requests vs. one file. Keep as a fallback / future incremental-refresh
  option.
- **`xai-org/xai-sdk-python`** (GitHub, Apache-2.0, fresh) — clean and
  redistributable, but SDK examples only, not the API reference. Optional
  supplement.
- **`xai-org/xai-cookbook`** — tutorial-grade; license is "Other" (verify before
  ingest). Not a docs mirror.
- **Context7 `/websites/x_ai_developers`** — re-scrape of the same `.md` layer;
  licensing/coverage caveats. No advantage over the first-party file.
- **`grok.com/skills-and-connectors`, `x.ai/news/grok-skills`** — consumer
  marketing, 403 to fetchers. Not developer docs; excluded.
- **No third-party llms.txt directory** lists xAI; the official file is the only
  llms.txt.

## Licensing note

`docs.x.ai` content is first-party but carries **no explicit open license**;
redistribution is governed by xAI's site terms (not retrieved). Ingesting for
internal semantic search is lower-risk than republishing. Flagged for the owner
to confirm before any public redistribution of the indexed content.

## Integration requirements (why this isn't pure config)

The research design assumed a new source would slot into one of the three
existing split strategies. The Grok corpus does **not**:

1. **Downloader (`scripts/download_llms_raw.py`)**
   - Add browser `User-Agent` header (required, or 404).
   - Add source entry. Because there is no separate `llms-full.txt`, point both
     tuple slots at the `llms.txt` URL so the corpus is saved to
     `llms-full.txt`, which is the file the splitter reads:
     `'Grok': ('https://docs.x.ai/llms.txt', 'https://docs.x.ai/llms.txt')`

2. **Splitter (`scripts/split_llms_pages.py`)** — needs a **new parser**:
   - Split on `^===/(?P<path>.+)===$` delimiter lines (one chunk per page).
   - Title = the page's `# ` H1 (fallback: last path segment).
   - `source_url` = `https://docs.x.ai/<path>` — the delimiter conveniently
     carries canonical provenance, so chunks get real URLs (better than the
     header-only strategy, which yields `source_url: null`).
   - Reuse `neutralize_code_block_headers` + the 200-char stub filter for
     consistency. Add a `SOURCES_GROK` list and a processing branch.
   - **Do not** reuse `SOURCES_HEADER_ONLY` (would orphan the `===/path===`
     lines, drop URLs, and risk over-splitting on any in-body `# `) or
     `SOURCES_MULTI_LEVEL` (would shatter each API page into tiny sub-stubs).

3. **Upload** — no change; metadata schema already supports `source_name`,
   `title`, `source_url`, `doc_id`.

## Expected outcome

~120 well-sized Grok pages (avg several KB, no stubs) indexed under
`source_name = "Grok"`, searchable via `search_docs(query, source="Grok")`,
covering the full Grok API plus Skills and Connectors.
