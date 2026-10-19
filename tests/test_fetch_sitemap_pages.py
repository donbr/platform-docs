"""Unit tests for the sitemap-driven page fetcher."""
import json

from scripts.download_llms_raw import SOURCES
from scripts.split_llms_pages import (
    SOURCES_GROK, SOURCES_HEADER_ONLY, SOURCES_MULTI_LEVEL, SOURCES_WITH_URL,
)
from scripts.fetch_sitemap_pages import SITEMAP_SOURCES, parse_page, parse_sitemap, select_urls, write_pages

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
