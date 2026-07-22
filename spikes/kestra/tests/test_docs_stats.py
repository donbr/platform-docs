import csv

from spikes.kestra import docs_stats


def test_build_rows_one_per_source_sorted_with_zero_default():
    rows = docs_stats.build_rows(
        sources={"Zep": "https://help.getzep.com", "Anthropic": "https://platform.claude.com"},
        counts={"Anthropic": 1603},
        last_downloaded="2026-07-22T17:32:54Z",
        collection_version="v2",
        generated_at="2026-07-22T18:00:00Z",
    )
    assert [r["source"] for r in rows] == ["Anthropic", "Zep"]  # sorted
    assert rows[0]["doc_count"] == 1603
    assert rows[1]["doc_count"] == 0  # missing count defaults to 0
    assert rows[0]["collection_version"] == "v2"


def test_to_csv_roundtrip(tmp_path):
    rows = docs_stats.build_rows(
        {"Temporal": "https://docs.temporal.io"}, {"Temporal": 2258},
        "d", "v2", "g",
    )
    out = tmp_path / "s.csv"
    docs_stats.to_csv(rows, str(out))
    parsed = list(csv.DictReader(out.open()))
    assert parsed[0]["source"] == "Temporal"
    assert parsed[0]["doc_count"] == "2258"
    assert list(parsed[0].keys()) == docs_stats.FIELDS
