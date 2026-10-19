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
import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

# defusedxml, not xml.etree: the sitemap is remote content we do not control, and
# the stdlib parser is vulnerable to entity-expansion ("billion laughs") attacks.
from defusedxml import ElementTree as ET

try:
    from scripts.split_llms_pages import MIN_CONTENT_LENGTH
except ModuleNotFoundError:
    # Run directly (`uv run scripts/fetch_sitemap_pages.py`): scripts/ is sys.path[0],
    # so the package path does not resolve. Same bare-sibling style as upload_to_qdrant.py.
    from split_llms_pages import MIN_CONTENT_LENGTH

PAGES_DIR = Path(__file__).parent.parent / "data" / "interim" / "pages"

# docs.x.ai and others 404 non-browser agents; match download_llms_raw.py.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_CONCURRENCY = 8

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
