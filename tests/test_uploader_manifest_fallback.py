"""Regression: uploaders must load sitemap-fetched sources (Zep) that the splitter's
top-level manifest does not list, and must fail when a requested source loads nothing."""
import importlib
import json
import sys
from pathlib import Path

import pytest

from scripts.fetch_sitemap_pages import write_pages

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
N = 5


@pytest.fixture(params=["upload_to_qdrant", "upload_to_qdrant_fastembed"])
def uploader(request, monkeypatch):
    # The uploaders use bare sibling imports (`from embedding_config import ...`), so
    # scripts/ must be on sys.path. Importing creates no network clients.
    monkeypatch.syspath_prepend(SCRIPTS)
    return importlib.import_module(request.param)


@pytest.fixture
def pages_dir(tmp_path):
    pages = [
        {"title": f"Page {i}", "source_url": f"https://help.getzep.com/p{i}",
         "content": "x" * (300 + i), "content_length": 300 + i}
        for i in range(N)
    ]
    write_pages("Zep", pages, tmp_path)
    # Top-level manifest as split_llms_pages.main() writes it: no Zep.
    (tmp_path / "manifest.json").write_text(json.dumps({"results": {}}))
    return tmp_path


def test_zep_loads_via_per_source_manifest(uploader, pages_dir):
    docs = uploader.load_documents(pages_dir, source_filter=["Zep"])
    assert len(docs) == N
    ids = sorted(d.metadata["doc_id"] for d in docs)
    assert ids[0] == "Zep_0000"
    for d in docs:
        assert d.metadata["source_name"] == "Zep"
        assert d.metadata["source_url"].startswith("https://help.getzep.com/")
        assert d.metadata["total_pages"] == N
        assert d.metadata["avg_content_length"] == pytest.approx(sum(300 + i for i in range(N)) / N)


def test_top_level_manifest_still_wins(uploader, pages_dir):
    (pages_dir / "manifest.json").write_text(
        json.dumps({"results": {"Zep": {"page_count": 99, "avg_size_chars": 1.0}}}))
    docs = uploader.load_documents(pages_dir, source_filter=["Zep"])
    assert docs[0].metadata["total_pages"] == 99


def test_missing_requested_source_reported(uploader, pages_dir):
    docs = uploader.load_documents(pages_dir, source_filter=["Zep"])
    assert uploader.missing_requested_sources(docs, ["Zep"]) == []
    assert uploader.missing_requested_sources(docs, ["Zep", "Nope"]) == ["Nope"]
    assert uploader.missing_requested_sources([], None) == []


def test_source_without_any_manifest_loads_nothing(uploader, tmp_path):
    (tmp_path / "Ghost").mkdir()
    (tmp_path / "Ghost" / "0000_A.json").write_text("{}")
    (tmp_path / "manifest.json").write_text(json.dumps({"results": {}}))
    docs = uploader.load_documents(tmp_path, source_filter=["Ghost"])
    assert docs == []
    assert uploader.missing_requested_sources(docs, ["Ghost"]) == ["Ghost"]
