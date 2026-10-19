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

from scripts.split_llms_pages import MIN_CONTENT_LENGTH

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
