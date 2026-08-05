# Zep/Graphiti Sitemap Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Zep (and with it Graphiti) to the platform-docs corpus by fetching per-page markdown from Zep's sitemap, and add a per-source floor to the verify gate so no future source can rot unnoticed.

**Architecture:** Zep discontinued `llms-full.txt`, so a new standalone script fetches each sitemap URL's `.md` variant and writes page JSON **directly** into `data/interim/pages/Zep/`, bypassing the split stage entirely (the `.md` endpoint already returns one page per file with its own URL). The 14 existing `llms.txt` sources are untouched. A per-source floor check in the Kestra verify gate becomes the durable guard.

**Tech Stack:** Python 3, `httpx` (async, already a dependency), `xml.etree.ElementTree` (stdlib), `pytest`, Kestra YAML, Qdrant.

**Spec:** `docs/specs/2026-08-05-zep-sitemap-ingestion-design.md`

## Global Constraints

- `MIN_CONTENT_LENGTH` is **200** chars — reuse the existing value, do not redefine it.
- Page JSON schema is exactly `{title, source_url, content, content_length}`.
- Page filenames must be `{index:04d}_{safe_title}.json`; the uploader derives `page_num` from `json_file.stem.split("_")[0]` and `doc_id` as `{source_name}_{page_num}`. Deviating breaks upload.
- `manifest.json` must be excluded from page globs everywhere.
- The `.md` endpoint returns **HTTP 200** for missing pages with a `# Page Not Found` body. Status codes alone are not sufficient validation.
- Excluded sitemap path segments for Zep: `v2` and `changelog`. Expected selection: **196 URLs** (83 root-level, 92 `sdk-reference`, 18 `graphiti`, 3 `memory-mcp-server`).
- Use the browser User-Agent string already defined in `scripts/download_llms_raw.py`; some doc hosts reject non-browser agents.
- Never touch production aliases (`platform-docs`, `platform-docs-fastembed`) outside the guarded `prod_alias_swap.py` path.
- Work on branch `docs/zep-sitemap-ingestion-design` (already created; spec committed there).

---

### Task 1: Sitemap parsing and URL selection

**Files:**
- Create: `scripts/fetch_sitemap_pages.py`
- Test: `tests/test_fetch_sitemap_pages.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `SITEMAP_SOURCES: dict[str, dict]`, `parse_sitemap(xml_text: str) -> list[str]`, `select_urls(urls: list[str], exclude_segments: set[str]) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for the sitemap-driven page fetcher."""
from scripts.fetch_sitemap_pages import parse_sitemap, select_urls

SITEMAP_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://help.getzep.com/overview</loc></url>
  <url><loc>https://help.getzep.com/graphiti/core-concepts/adding-episodes</loc></url>
  <url><loc>https://help.getzep.com/sdk-reference/graph/add-data</loc></url>
  <url><loc>https://help.getzep.com/v2/welcome</loc></url>
  <url><loc>https://help.getzep.com/v2/sdk-reference/deprecated/memory/synthesize-question</loc></url>
  <url><loc>https://help.getzep.com/changelog/2026/8/3</loc></url>
</urlset>
"""


def test_parse_sitemap_extracts_all_locs():
    assert len(parse_sitemap(SITEMAP_FIXTURE)) == 6


def test_select_urls_excludes_prior_version_and_changelog():
    selected = select_urls(parse_sitemap(SITEMAP_FIXTURE), {"v2", "changelog"})
    assert selected == [
        "https://help.getzep.com/graphiti/core-concepts/adding-episodes",
        "https://help.getzep.com/overview",
        "https://help.getzep.com/sdk-reference/graph/add-data",
    ]


def test_select_urls_only_excludes_whole_first_segment():
    """'changelog-notes' must survive an exclusion of the 'changelog' segment."""
    urls = ["https://help.getzep.com/changelog-notes"]
    assert select_urls(urls, {"changelog"}) == urls


def test_select_urls_deduplicates():
    urls = ["https://help.getzep.com/overview", "https://help.getzep.com/overview"]
    assert select_urls(urls, set()) == ["https://help.getzep.com/overview"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fetch_sitemap_pages'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/fetch_sitemap_pages.py`:

```python
"""Fetch per-page markdown for sources that publish a sitemap instead of llms-full.txt.

Some documentation hosts have retired the concatenated `llms-full.txt` file that
`download_llms_raw.py` depends on. Zep is the first: `help.getzep.com/llms-full.txt`
now returns the same 2 KB landing page as `llms.txt`, which collapsed the source to
a single indexed page and removed Graphiti coverage entirely.

Zep does still publish a sitemap, and documents that appending `.md` to any page URL
returns clean markdown. This script walks that sitemap and writes page JSON directly
into `data/interim/pages/{Source}/` — the same shape `split_llms_pages.py` emits.

It deliberately does NOT round-trip through a synthetic `llms-full.txt`: the `.md`
endpoint already returns one page per file with its own canonical URL, so re-splitting
it with a regex would reintroduce exactly the fragility that caused this outage.
"""
from urllib.parse import urlparse

# defusedxml, not xml.etree: the sitemap is remote content we do not control, and
# the stdlib parser is vulnerable to entity-expansion ("billion laughs") attacks.
from defusedxml import ElementTree as ET

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# Per-source fetch configuration. `min_pages` is a fetch-time sanity floor: if the
# run yields fewer pages than this, something upstream changed and the script fails
# loudly rather than quietly shrinking the corpus.
SITEMAP_SOURCES = {
    "Zep": {
        "sitemap": "https://help.getzep.com/sitemap.xml",
        # `v2` is the prior docs version — a parallel copy of the whole tree including
        # 29 explicitly deprecated pages. Indexing it would double-index Zep and let
        # stale endpoints surface beside current ones.
        "exclude_segments": {"v2", "changelog"},
        "min_pages": 150,
    },
}


def parse_sitemap(xml_text: str) -> list[str]:
    """Return every <loc> URL in a sitemap document."""
    root = ET.fromstring(xml_text)
    return [loc.text.strip() for loc in root.iter(f"{SITEMAP_NS}loc") if loc.text]


def select_urls(urls: list[str], exclude_segments: set[str]) -> list[str]:
    """Drop URLs whose first path segment is excluded; dedupe and sort.

    Matching is on the whole first segment, not a string prefix, so excluding
    'changelog' does not also drop '/changelog-notes'.
    """
    selected = []
    for url in urls:
        path = urlparse(url).path.strip("/")
        if not path:
            continue
        if path.split("/")[0] in exclude_segments:
            continue
        selected.append(url)
    return sorted(dict.fromkeys(selected))
```

- [ ] **Step 4: Declare the defusedxml dependency**

`defusedxml` is currently only present transitively. The script imports it
directly, so add it to `dependencies` in `pyproject.toml`:

```toml
    "defusedxml>=0.7.1",
```

Then sync: `uv sync`

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_sitemap_pages.py tests/test_fetch_sitemap_pages.py pyproject.toml uv.lock
git commit -m "feat(etl): sitemap parsing and URL selection for per-page fetching"
```

---

### Task 2: Markdown to page record

**Files:**
- Modify: `scripts/fetch_sitemap_pages.py`
- Test: `tests/test_fetch_sitemap_pages.py`

**Interfaces:**
- Consumes: `scripts/fetch_sitemap_pages.py` from Task 1
- Produces: `parse_page(markdown: str, url: str) -> dict | None` returning `{title, source_url, content, content_length}` or `None` when the page must be rejected

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetch_sitemap_pages.py`:

```python
from scripts.fetch_sitemap_pages import parse_page

# Every Zep .md page opens with this identical 3-line boilerplate before the H1.
PREAMBLE = (
    "> For clean Markdown of any page, append .md to the page URL.\n"
    "> For a complete documentation index, see https://help.getzep.com/llms.txt.\n"
    "> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP "
    "server at https://help.getzep.com/_mcp/server.\n"
)

PAGE_MD = PREAMBLE + """
# Adding Episodes

Episodes represent a single data ingestion event. An `episode` is itself a node,
and any nodes identified while ingesting the episode are related to the episode
via `MENTIONS` edges. This body is comfortably longer than the minimum content
length so that it is not dropped as a stub by the length filter.
"""

NOT_FOUND_MD = """# Page Not Found

This page does not exist.

## Similar pages

- [Overview](https://help.getzep.com/overview.md)
"""


def test_parse_page_extracts_title_and_url():
    page = parse_page(PAGE_MD, "https://help.getzep.com/graphiti/core-concepts/adding-episodes")
    assert page["title"] == "Adding Episodes"
    assert page["source_url"] == "https://help.getzep.com/graphiti/core-concepts/adding-episodes"


def test_parse_page_strips_boilerplate_preamble():
    """The identical preamble on every page would dilute the embedding signal."""
    page = parse_page(PAGE_MD, "https://help.getzep.com/x")
    assert "append .md to the page URL" not in page["content"]
    assert page["content"].startswith("Episodes represent")


def test_parse_page_content_length_matches_content():
    page = parse_page(PAGE_MD, "https://help.getzep.com/x")
    assert page["content_length"] == len(page["content"])


def test_parse_page_rejects_not_found_despite_http_200():
    """The .md endpoint serves 404s as HTTP 200 — the body is the only signal."""
    assert parse_page(NOT_FOUND_MD, "https://help.getzep.com/missing") is None


def test_parse_page_rejects_stub_below_min_content_length():
    stub = PREAMBLE + "\n# Tiny\n\nToo short.\n"
    assert parse_page(stub, "https://help.getzep.com/tiny") is None


def test_parse_page_rejects_markdown_with_no_heading():
    assert parse_page("just some text with no heading at all", "https://x/y") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_page'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/fetch_sitemap_pages.py` (import `MIN_CONTENT_LENGTH` rather than redefining it):

```python
from scripts.split_llms_pages import MIN_CONTENT_LENGTH

# The .md endpoint returns HTTP 200 for missing pages, with this as the H1.
NOT_FOUND_TITLE = "Page Not Found"


def parse_page(markdown: str, url: str) -> dict | None:
    """Convert one fetched .md document into a page record, or None to reject it.

    Rejects three cases: a soft-404 body, markdown with no H1 to title it, and
    stub pages under MIN_CONTENT_LENGTH (consistent with the splitter, which drops
    short API-reference stubs so they don't dominate top-k for short queries).

    Taking the content from *after* the first H1 also discards the identical
    boilerplate preamble Zep prepends to every page; left in, all ~196 pages would
    share their opening tokens.
    """
    lines = markdown.splitlines()
    title_index = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
    if title_index is None:
        return None

    title = lines[title_index][2:].strip()
    if title == NOT_FOUND_TITLE:
        return None

    content = "\n".join(lines[title_index + 1:]).strip()
    if len(content) < MIN_CONTENT_LENGTH:
        return None

    return {
        "title": title,
        "source_url": url,
        "content": content,
        "content_length": len(content),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_sitemap_pages.py tests/test_fetch_sitemap_pages.py
git commit -m "feat(etl): markdown-to-page conversion with soft-404 rejection"
```

---

### Task 3: Async fetching, page writing, and the runnable script

**Files:**
- Modify: `scripts/fetch_sitemap_pages.py`
- Test: `tests/test_fetch_sitemap_pages.py`

**Interfaces:**
- Consumes: `parse_sitemap`, `select_urls`, `parse_page`, `SITEMAP_SOURCES` from Tasks 1–2
- Produces: `write_pages(source_name: str, pages: list[dict], pages_dir: Path) -> dict` (returns the manifest); `fetch_source(...)` coroutine; a `main()` CLI entry point

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetch_sitemap_pages.py`:

```python
import json
from pathlib import Path

from scripts.fetch_sitemap_pages import write_pages


def _page(title, body_char="x"):
    content = body_char * 300
    return {"title": title, "source_url": f"https://help.getzep.com/{title}",
            "content": content, "content_length": len(content)}


def test_write_pages_uses_uploader_filename_contract(tmp_path):
    """Uploader derives page_num from stem.split('_')[0]; index must lead."""
    write_pages("Zep", [_page("Adding Episodes"), _page("Searching")], tmp_path)
    names = sorted(f.name for f in (tmp_path / "Zep").glob("*.json") if f.name != "manifest.json")
    assert names == ["0000_Adding_Episodes.json", "0001_Searching.json"]


def test_write_pages_sanitizes_titles_into_filenames(tmp_path):
    write_pages("Zep", [_page("Neo4j: Configuration & Setup!")], tmp_path)
    name = next(f.name for f in (tmp_path / "Zep").glob("[0-9]*.json"))
    # `\s+` collapses the run of spaces left by the stripped '&' into ONE underscore.
    assert name == "0000_Neo4j_Configuration_Setup.json"


def test_write_pages_writes_manifest_with_count(tmp_path):
    manifest = write_pages("Zep", [_page("A"), _page("B")], tmp_path)
    on_disk = json.loads((tmp_path / "Zep" / "manifest.json").read_text())
    assert manifest["page_count"] == 2
    assert on_disk["page_count"] == 2
    assert on_disk["source"] == "Zep"


def test_write_pages_clears_stale_files_from_prior_runs(tmp_path):
    """Mirrors split_llms_pages.process_source, so removed pages don't linger
    and get picked up by the uploader's *.json glob."""
    stale = tmp_path / "Zep" / "9999_Removed_Page.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    write_pages("Zep", [_page("Kept")], tmp_path)
    assert not stale.exists()


def test_write_pages_roundtrips_the_page_schema(tmp_path):
    write_pages("Zep", [_page("Adding Episodes")], tmp_path)
    written = json.loads((tmp_path / "Zep" / "0000_Adding_Episodes.json").read_text())
    assert set(written) == {"title", "source_url", "content", "content_length"}
    assert written["source_url"].startswith("https://help.getzep.com/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_pages'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/fetch_sitemap_pages.py`:

```python
import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

PAGES_DIR = Path(__file__).parent.parent / "data" / "interim" / "pages"

# docs.x.ai and others 404 non-browser agents; match download_llms_raw.py.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_CONCURRENCY = 8


def safe_filename(index: int, title: str) -> str:
    """Match split_llms_pages.py exactly — the uploader parses page_num off the stem."""
    safe_title = re.sub(r"[^\w\s-]", "", title)[:50].strip()
    safe_title = re.sub(r"\s+", "_", safe_title)
    return f"{index:04d}_{safe_title}.json"


def write_pages(source_name: str, pages: list[dict], pages_dir: Path = PAGES_DIR) -> dict:
    """Write page JSON plus a manifest, replacing any previous run's output."""
    output_dir = pages_dir / source_name
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(pages):
        (output_dir / safe_filename(i, page["title"])).write_text(
            json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    manifest = {
        "source": source_name,
        "pattern_type": "sitemap_markdown",
        "page_count": len(pages),
        "total_content_chars": sum(p["content_length"] for p in pages),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pages": [
            {"index": i, "title": p["title"], "source_url": p["source_url"],
             "content_length": p["content_length"]}
            for i, p in enumerate(pages)
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


async def fetch_markdown(client: httpx.AsyncClient, url: str,
                         semaphore: asyncio.Semaphore) -> tuple[str, str | None]:
    """Fetch one page's .md variant. Returns (url, text) or (url, None) on failure."""
    async with semaphore:
        try:
            response = await client.get(f"{url}.md", follow_redirects=True)
            response.raise_for_status()
            return url, response.text
        except Exception as exc:  # noqa: BLE001 - report and continue; the floor catches mass failure
            print(f"    ! {url}: {type(exc).__name__}")
            return url, None


async def fetch_source(source_name: str, config: dict, pages_dir: Path = PAGES_DIR) -> dict:
    """Fetch every selected page for one source and write it to the pages dir."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}) as client:
        sitemap = await client.get(config["sitemap"], follow_redirects=True)
        sitemap.raise_for_status()
        urls = select_urls(parse_sitemap(sitemap.text), config["exclude_segments"])
        print(f"  {source_name}: {len(urls)} URLs selected from sitemap")

        results = await asyncio.gather(*(fetch_markdown(client, u, semaphore) for u in urls))

    pages, rejected = [], 0
    for url, text in results:
        if text is None:
            rejected += 1
            continue
        page = parse_page(text, url)
        if page is None:
            rejected += 1
            continue
        pages.append(page)

    manifest = write_pages(source_name, pages, pages_dir)
    manifest["urls_selected"] = len(urls)
    manifest["rejected"] = rejected
    return manifest


async def run(sources: list[str], pages_dir: Path = PAGES_DIR) -> int:
    """Fetch each source; return a process exit code."""
    failed = False
    for source_name in sources:
        config = SITEMAP_SOURCES[source_name]
        manifest = await fetch_source(source_name, config, pages_dir)
        count, floor = manifest["page_count"], config["min_pages"]
        status = "OK" if count >= floor else "BELOW FLOOR"
        print(f"  {source_name}: {count} pages written "
              f"({manifest['rejected']} rejected, floor {floor}) [{status}]")
        if count < floor:
            failed = True
    return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sources", nargs="*", default=list(SITEMAP_SOURCES),
                   help="sitemap sources to fetch (default: all configured)")
    args = p.parse_args()
    unknown = set(args.sources) - set(SITEMAP_SOURCES)
    if unknown:
        p.error(f"unknown sitemap sources: {sorted(unknown)}")
    sys.exit(asyncio.run(run(args.sources)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -v`
Expected: PASS (15 tests total)

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_sitemap_pages.py tests/test_fetch_sitemap_pages.py
git commit -m "feat(etl): async sitemap page fetching with yield floor"
```

---

### Task 4: Retire the dead Zep path

**Files:**
- Modify: `scripts/download_llms_raw.py` (remove the `'Zep'` entry from `SOURCES`)
- Modify: `scripts/split_llms_pages.py` (remove `'Zep'` from `SOURCES_HEADER_ONLY`)

**Interfaces:**
- Consumes: nothing
- Produces: a `data/interim/pages/Zep/` directory owned solely by `fetch_sitemap_pages.py`

Without this, `split_llms_pages.process_source` rmtree's `data/interim/pages/Zep/` and overwrites the fetched pages with its 1-page result. Once Zep is absent from the splitter lists, the splitter never touches that directory and script ordering stops mattering.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fetch_sitemap_pages.py`:

```python
from scripts.download_llms_raw import SOURCES
from scripts.split_llms_pages import (
    SOURCES_GROK, SOURCES_HEADER_ONLY, SOURCES_MULTI_LEVEL, SOURCES_WITH_URL,
)
from scripts.fetch_sitemap_pages import SITEMAP_SOURCES


def test_sitemap_sources_are_not_also_llms_txt_sources():
    """Two writers to one pages dir means the splitter silently clobbers the fetch."""
    assert not (set(SITEMAP_SOURCES) & set(SOURCES))


def test_sitemap_sources_have_no_split_strategy():
    all_split = set(SOURCES_WITH_URL) | set(SOURCES_HEADER_ONLY) | set(SOURCES_MULTI_LEVEL) | set(SOURCES_GROK)
    assert not (set(SITEMAP_SOURCES) & all_split)


def test_every_downloaded_source_still_has_a_split_strategy():
    """Guards the Cursor-class defect: a source that downloads but never splits
    reaches Qdrant not at all."""
    all_split = set(SOURCES_WITH_URL) | set(SOURCES_HEADER_ONLY) | set(SOURCES_MULTI_LEVEL) | set(SOURCES_GROK)
    assert set(SOURCES) - all_split == {"Cursor"}, (
        "A downloaded source has no split strategy. Cursor is the known, tracked "
        "instance; anything else here is a new silent-drop defect."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch_sitemap_pages.py -k "sitemap_sources or split_strategy" -v`
Expected: FAIL — `test_sitemap_sources_are_not_also_llms_txt_sources` fails because `Zep` is still in both.

- [ ] **Step 3: Write minimal implementation**

In `scripts/download_llms_raw.py`, delete the `'Zep'` entry from `SOURCES`:

```python
    'Zep': (
        'https://help.getzep.com/llms.txt',
        'https://help.getzep.com/llms-full.txt',
    ),
```

Replace it with a comment where the entry was:

```python
    # Zep is fetched by scripts/fetch_sitemap_pages.py instead: upstream retired
    # llms-full.txt (it now returns the same 2 KB landing page as llms.txt).
```

In `scripts/split_llms_pages.py`, remove `'Zep',` from `SOURCES_HEADER_ONLY`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: PASS — all tests including the pre-existing `test_split_grok.py` and `test_mcp_server.py`

- [ ] **Step 5: Commit**

```bash
git add scripts/download_llms_raw.py scripts/split_llms_pages.py tests/test_fetch_sitemap_pages.py
git commit -m "refactor(etl): move Zep off the retired llms-full.txt path"
```

---

### Task 5: Live verification run

**Files:**
- No source changes; this task validates against the real upstream.

**Interfaces:**
- Consumes: the complete `scripts/fetch_sitemap_pages.py`
- Produces: a populated `data/interim/pages/Zep/` (~196 pages) for Task 6's baseline

- [ ] **Step 1: Run the fetcher against live Zep**

```bash
uv run scripts/fetch_sitemap_pages.py --sources Zep
```

Expected: `196 URLs selected from sitemap`, then roughly `196 pages written` with a small number rejected, and `[OK]`. Exit code 0.

- [ ] **Step 2: Verify Graphiti coverage and URL quality**

```bash
uv run python -c "
import json, glob
files = [f for f in glob.glob('data/interim/pages/Zep/*.json') if 'manifest' not in f]
pages = [json.load(open(f)) for f in files]
graphiti = [p for p in pages if '/graphiti/' in p['source_url']]
print('total pages:', len(pages))
print('graphiti pages:', len(graphiti))
print('null source_url:', sum(1 for p in pages if not p['source_url']))
print('v2 or changelog leaked:', sum(1 for p in pages if '/v2/' in p['source_url'] or '/changelog' in p['source_url']))
print('not-found leaked:', sum(1 for p in pages if p['title'] == 'Page Not Found'))
print('sample:', sorted(p['title'] for p in graphiti)[:5])
"
```

Expected: `total pages` ~196, `graphiti pages` **18**, `null source_url` **0**, `v2 or changelog leaked` **0**, `not-found leaked` **0**.

- [ ] **Step 3: Confirm the uploader can parse the filenames**

```bash
uv run scripts/upload_to_qdrant.py --sources Zep --dry-run
```

Expected: reports ~196 documents for Zep with no errors, and does not write to Qdrant.

- [ ] **Step 4: Commit the verification note**

Record the observed counts in the plan's checkboxes. If `graphiti pages` is not 18, stop and investigate before proceeding — the sitemap changed.

```bash
git commit --allow-empty -m "test(etl): verify live Zep fetch yields full corpus incl. Graphiti"
```

---

### Task 6: Per-source floor in the verify gate

**Files:**
- Create: `spikes/kestra/source_baselines.json`
- Modify: `spikes/kestra/verify_counts.py`
- Test: `tests/test_source_floors.py`

**Interfaces:**
- Consumes: a populated pages dir from Task 5
- Produces: `load_baselines(path: Path) -> dict[str, int]`, `check_source_floors(pages_dir: Path, baselines: dict[str, int]) -> list[tuple[str, int, int]]` returning `(source, actual, floor)` for every source below its floor

This is the durable fix. Zep sat at 1 page through two production promotions because the gate only checks the corpus total; 8,163 looked correct because nobody counted per source.

- [ ] **Step 1: Write the failing test**

Create `tests/test_source_floors.py`:

```python
"""The per-source floor that would have caught the Zep collapse before promotion."""
import json

from spikes.kestra.verify_counts import check_source_floors, load_baselines


def _make_source(pages_dir, name, count):
    d = pages_dir / name
    d.mkdir(parents=True)
    for i in range(count):
        (d / f"{i:04d}_Page.json").write_text("{}")
    (d / "manifest.json").write_text("{}")


def test_no_shortfall_when_every_source_meets_its_floor(tmp_path):
    _make_source(tmp_path, "Zep", 196)
    _make_source(tmp_path, "Kestra", 485)
    assert check_source_floors(tmp_path, {"Zep": 150, "Kestra": 400}) == []


def test_detects_a_collapsed_source(tmp_path):
    """Exactly the Zep defect: 1 page where ~196 were expected."""
    _make_source(tmp_path, "Zep", 1)
    _make_source(tmp_path, "Kestra", 485)
    assert check_source_floors(tmp_path, {"Zep": 150, "Kestra": 400}) == [("Zep", 1, 150)]


def test_missing_source_directory_counts_as_zero(tmp_path):
    """The Cursor defect: source configured but no pages dir at all."""
    assert check_source_floors(tmp_path, {"Cursor": 10}) == [("Cursor", 0, 10)]


def test_manifest_is_not_counted_as_a_page(tmp_path):
    _make_source(tmp_path, "Zep", 2)
    assert check_source_floors(tmp_path, {"Zep": 3}) == [("Zep", 2, 3)]


def test_load_baselines_reads_the_committed_file(tmp_path):
    path = tmp_path / "source_baselines.json"
    path.write_text(json.dumps({"floors": {"Zep": 150}}))
    assert load_baselines(path) == {"Zep": 150}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_source_floors.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_source_floors'`

- [ ] **Step 3: Write minimal implementation**

Create `spikes/kestra/source_baselines.json`. Set each floor to roughly 80% of the count observed in Task 5, so ordinary upstream churn does not trip the gate but a collapse does:

```json
{
  "_comment": "Per-source page floors for the promotion gate. Set to ~80% of observed counts so normal upstream churn passes but a collapse fails. Update deliberately when a source legitimately shrinks.",
  "floors": {
    "Anthropic": 1280,
    "FastMCP": 140,
    "GoogleADK": 190,
    "Grok": 115,
    "Kestra": 385,
    "LangChain": 780,
    "McpProtocol": 100,
    "OpenAI": 110,
    "Prefect": 570,
    "PydanticAI": 165,
    "Supabase": 315,
    "Temporal": 2290,
    "Vue": 70,
    "Zep": 150
  }
}
```

Add to `spikes/kestra/verify_counts.py`:

```python
import json
from pathlib import Path

BASELINES_PATH = Path(__file__).with_name("source_baselines.json")


def load_baselines(path: Path = BASELINES_PATH) -> dict[str, int]:
    """Read the committed per-source page floors."""
    return json.loads(path.read_text())["floors"]


def check_source_floors(pages_dir: Path, baselines: dict[str, int]) -> list[tuple[str, int, int]]:
    """Return (source, actual, floor) for every source below its floor.

    The corpus total alone cannot catch a single source collapsing — Zep fell from
    ~196 pages to 1 and the total still looked healthy enough to promote twice.
    """
    shortfalls = []
    for source, floor in sorted(baselines.items()):
        source_dir = pages_dir / source
        actual = (
            sum(1 for f in source_dir.glob("*.json") if f.name != "manifest.json")
            if source_dir.is_dir() else 0
        )
        if actual < floor:
            shortfalls.append((source, actual, floor))
    return shortfalls
```

Then wire it into `main()` so the gate fails on any shortfall. Replace the body of `main()` after `actual = qdrant_count(args.collection)` with:

```python
    ok = is_complete(actual, args.expected)
    print(f"verify_counts: collection={args.collection} actual={actual} expected={args.expected} ok={ok}")

    shortfalls = check_source_floors(poc_config.DEFAULT_PAGES_DIR, load_baselines())
    for source, source_actual, floor in shortfalls:
        print(f"verify_counts: SOURCE BELOW FLOOR {source} actual={source_actual} floor={floor}")

    sys.exit(0 if (ok and not shortfalls) else 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: PASS (all tests)

Then confirm the gate passes against the real pages dir populated in Task 5:

```bash
uv run python -c "
from pathlib import Path
from spikes.kestra.verify_counts import check_source_floors, load_baselines
from spikes.kestra import poc_config
print('shortfalls:', check_source_floors(poc_config.DEFAULT_PAGES_DIR, load_baselines()))
"
```

Expected: `shortfalls: []`. Note that `Cursor` is deliberately absent from the floors file — it has no pages dir and would fail the gate permanently. It is tracked as separate work in the spec.

- [ ] **Step 5: Commit**

```bash
git add spikes/kestra/source_baselines.json spikes/kestra/verify_counts.py tests/test_source_floors.py
git commit -m "feat(kestra): per-source page floors in the promotion gate"
```

---

### Task 7: Wire the fetcher into prod_refresh

**Files:**
- Modify: `spikes/kestra/flows/prod_refresh.yaml`

**Interfaces:**
- Consumes: `scripts/fetch_sitemap_pages.py`
- Produces: a `fetch_sitemap_sources` task between `split_docs` and `upload_bulk`

No other flow changes are needed: `Zep` is already in the flow's `bulk_sources` variable, and `compute_expected` runs `emit_expected --all`, which counts page directories and therefore picks up the new total automatically.

- [ ] **Step 1: Add the task to the flow**

Insert immediately after the `split_docs` task in `spikes/kestra/flows/prod_refresh.yaml`:

```yaml
  # Sources whose upstream retired llms-full.txt are fetched per-page from their
  # sitemap instead. Runs after split_docs: the splitter rmtree's the page dir of
  # every source it handles, and these sources are deliberately absent from its
  # strategy lists, but ordering here keeps that independence obvious.
  - id: fetch_sitemap_sources
    type: io.kestra.plugin.scripts.shell.Commands
    runIf: "{{ inputs.rebuild }}"
    taskRunner:
      type: io.kestra.plugin.core.runner.Process
    retry:
      type: constant
      interval: PT30S
      maxAttempt: 3
    commands:
      - cd /repo && uv run scripts/fetch_sitemap_pages.py
```

- [ ] **Step 2: Validate the YAML parses**

```bash
uv run python -c "
import yaml
flow = yaml.safe_load(open('spikes/kestra/flows/prod_refresh.yaml'))
ids = [t['id'] for t in flow['tasks']]
print(ids)
assert 'fetch_sitemap_sources' in ids
assert ids.index('split_docs') < ids.index('fetch_sitemap_sources') < ids.index('upload_bulk')
print('task order OK')
"
```

Expected: prints the task list and `task order OK`.

- [ ] **Step 3: Sync the flow into the running Kestra instance**

```bash
set -a && source spikes/kestra/.env && set +a
curl -sS -u "$KESTRA_BASIC_AUTH_USERNAME:$KESTRA_BASIC_AUTH_PASSWORD" \
  -X POST "http://localhost:8080/api/v1/main/flows/platform_docs" \
  -H "Content-Type: application/x-yaml" \
  --data-binary @spikes/kestra/flows/prod_refresh.yaml | head -c 300
```

If the flow already exists this returns a conflict; use `-X PUT .../flows/platform_docs/prod_refresh` instead. Confirm afterwards:

```bash
curl -sS -u "$KESTRA_BASIC_AUTH_USERNAME:$KESTRA_BASIC_AUTH_PASSWORD" \
  "http://localhost:8080/api/v1/main/flows/platform_docs" \
  | python3 -c "import sys,json; print([f['id'] for f in json.load(sys.stdin)])"
```

- [ ] **Step 4: Dry-run the flow without swapping the alias**

```bash
set -a && source spikes/kestra/.env && set +a
curl -sS -u "$KESTRA_BASIC_AUTH_USERNAME:$KESTRA_BASIC_AUTH_PASSWORD" \
  -X POST "http://localhost:8080/api/v1/main/executions/platform_docs/prod_refresh" \
  -F 'target_collection=platform-docs-v4' -F 'confirm=false' | head -c 300
```

Expected: the flow builds `platform-docs-v4`, `compute_expected` reports roughly **8,360** (up from 8,163), `verify_counts` passes with no source shortfalls, and `swap_prod_alias` is **skipped** because `confirm=false`. Verify in the Postgres telemetry:

```bash
docker compose -f spikes/kestra/docker-compose.yml exec -T postgres \
  psql -U kestra -d platform_docs -c \
  "select run_id, stage, status, docs_expected, alias_swapped_at from orchestration.pipeline_runs order by started_at desc limit 3;"
```

Expected: newest row `status=success`, `alias_swapped_at` NULL.

- [ ] **Step 5: Commit**

```bash
git add spikes/kestra/flows/prod_refresh.yaml
git commit -m "feat(kestra): fetch sitemap sources during prod refresh"
```

---

### Task 8: Graphiti access layer

**Files:**
- Create: `.mcp.json`
- Create: `.claude/skills/graphiti-docs/SKILL.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: a registered `zep-docs` MCP server and a routing skill

Zep ships two MCP servers and the one its plugin page advertises is the wrong one. `docs-mcp.getzep.com/mcp` is Zep Cloud-biased — a Graphiti `add_episode` probe returned Zep Cloud's `/api/v2/graph/set-ontology`, and a FalkorDB probe returned the "Zep vs Graphiti" marketing page. The Fern-native `help.getzep.com/_mcp/server` returned the correct Graphiti page for both.

- [ ] **Step 1: Register the MCP server**

Create `.mcp.json`:

```json
{
  "mcpServers": {
    "zep-docs": {
      "type": "http",
      "url": "https://help.getzep.com/_mcp/server"
    }
  }
}
```

- [ ] **Step 2: Verify the server responds and covers Graphiti**

```bash
curl -sS -X POST https://help.getzep.com/_mcp/server \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' \
  | sed -n 's/^data: //p'
```

Expected: `"serverInfo":{"name":"fern-docs-mcp-server","version":"1.0.0"}`.

- [ ] **Step 3: Write the routing skill**

Create `.claude/skills/graphiti-docs/SKILL.md`:

```markdown
---
name: graphiti-docs
description: Use when answering questions about Graphiti (the temporal knowledge-graph framework behind Zep) or Zep itself — add_episode, custom entity and edge types, communities, graph namespacing, search, or driver configuration for Neo4j, FalkorDB, Kuzu, or AWS Neptune. Routes to the source that actually covers each kind of question.
---

# Graphiti and Zep documentation

Three sources cover Graphiti, and they are good at different things. Pick by
question type rather than defaulting to one.

## Routing

| Question type | Use |
|---|---|
| Graphiti concepts, configuration, drivers | `zep-docs` MCP (`searchDocs`) — Fern-native, returns source URLs |
| Graphiti code-level detail, API signatures, usage patterns | Context7 `/getzep/graphiti` (679 repo-derived snippets) |
| Zep Cloud APIs, SDK reference, broad semantic search | `platform-docs` MCP (`search_docs` with `source="Zep"`) |

## Two traps

**Do not use `docs-mcp.getzep.com/mcp`.** Zep's plugin page advertises it, but it
is Zep Cloud-biased: asked about Graphiti's `add_episode` with custom entity
types it returns Zep Cloud's `/api/v2/graph/set-ontology` REST endpoint, and
asked about the FalkorDB driver it returns the "Zep vs Graphiti" marketing
comparison. The registered `zep-docs` server is the Fern-native one, which
answers both correctly.

**Graphiti is not Zep.** Graphiti is the open-source framework that runs locally;
Zep is the managed platform that operates it at scale. A Zep Cloud REST endpoint
is rarely the answer to a Graphiti question. If a search returns
`/api/v2/...` for a Graphiti question, the retrieval missed.

## Fetching a page directly

Any page on `help.getzep.com` returns clean markdown when you append `.md` to its
URL — for example `https://help.getzep.com/graphiti/configuration/falkor-db-configuration.md`.
This is officially supported. Note that missing pages return **HTTP 200** with a
`# Page Not Found` body, so check the body rather than the status code.
```

- [ ] **Step 4: Document both in CLAUDE.md**

Add to the "Reference Docs" section of `CLAUDE.md`:

```markdown
## Graphiti / Zep documentation

Zep retired `llms-full.txt`, so Zep is fetched per-page from its sitemap by
`scripts/fetch_sitemap_pages.py` (~196 pages incl. 18 Graphiti), not by
`download_llms_raw.py`. For Graphiti questions see the `graphiti-docs` skill —
and note that Zep's *advertised* MCP (`docs-mcp.getzep.com`) is Cloud-biased and
misses Graphiti; the registered `zep-docs` server is the Fern-native one.
```

- [ ] **Step 5: Commit**

```bash
git add .mcp.json .claude/skills/graphiti-docs/SKILL.md CLAUDE.md
git commit -m "feat(docs): register Fern-native Zep MCP and add Graphiti routing skill"
```

---

## Promotion (after all tasks)

The plan deliberately stops short of swapping the production alias. Once Task 7's
dry run is clean, promote with an explicit confirm — one run per embedder:

```bash
# OpenAI
kestra flow execute platform_docs prod_refresh \
  --inputs '{"target_collection":"platform-docs-v4","confirm":true}'
# FastEmbed
kestra flow execute platform_docs prod_refresh \
  --inputs '{"target_collection":"platform-docs-fastembed-v4","prod_alias":"platform-docs-fastembed","upload_script":"upload_to_qdrant_fastembed.py","confirm":true}'
```

Expect the corpus to move from **8,163 to roughly 8,360**. That change is the
point of this work, not a regression. `platform-docs-v3` remains as instant
rollback.
