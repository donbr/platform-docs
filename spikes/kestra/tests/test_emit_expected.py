import json

from spikes.kestra import emit_expected


def test_kestra_output_line_is_parseable_marker():
    line = emit_expected.kestra_output_line(628)
    assert line.startswith("::") and line.endswith("::")
    payload = json.loads(line.strip(":"))
    assert payload == {"outputs": {"count": 628}}


def test_count_all_sources_sums_every_source_dir(tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "0001.json").write_text("{}")
    (tmp_path / "A" / "manifest.json").write_text("{}")  # excluded
    (tmp_path / "B").mkdir()
    (tmp_path / "B" / "0001.json").write_text("{}")
    (tmp_path / "B" / "0002.json").write_text("{}")
    assert emit_expected.count_all_sources(tmp_path) == 3
