"""Unit tests for the sitemap-driven page fetcher."""
from scripts.fetch_sitemap_pages import parse_page, parse_sitemap, select_urls

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

This page does not exist. The page you requested may have been moved, renamed,
or removed. Check the links below for the closest current documentation.

## Similar pages

- [Overview](https://help.getzep.com/overview.md)
- [Adding Episodes](https://help.getzep.com/graphiti/core-concepts/adding-episodes.md)
- [Graph Add Data](https://help.getzep.com/sdk-reference/graph/add-data.md)
- [Searching the Graph](https://help.getzep.com/graphiti/core-concepts/searching.md)
- [Custom Entity Types](https://help.getzep.com/graphiti/core-concepts/custom-entity-types.md)
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
