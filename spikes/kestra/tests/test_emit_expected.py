import json

from spikes.kestra import emit_expected


def test_kestra_output_line_is_parseable_marker():
    line = emit_expected.kestra_output_line(628)
    assert line.startswith("::") and line.endswith("::")
    payload = json.loads(line.strip(":"))
    assert payload == {"outputs": {"count": 628}}
