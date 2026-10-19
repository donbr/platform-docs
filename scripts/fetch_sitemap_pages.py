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
