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
