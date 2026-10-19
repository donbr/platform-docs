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


def test_zep_fetch_floor_matches_gate_floor():
    from scripts.fetch_sitemap_pages import SITEMAP_SOURCES
    assert SITEMAP_SOURCES["Zep"]["min_pages"] == load_baselines()["Zep"]
