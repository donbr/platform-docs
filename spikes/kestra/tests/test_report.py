from spikes.kestra import report


def test_render_report_marks_complete_and_short_collections():
    md = report.render_report(
        run_id="abc123",
        status="success",
        expected=608,
        counts={"platform-docs-poc-v1": 608, "platform-docs-poc-fastembed-v1": 140},
        aliases={"platform-docs-poc-active": "platform-docs-poc-v1"},
    )
    assert "abc123" in md
    assert "| platform-docs-poc-v1 | 608 | ✅ |" in md
    assert "| platform-docs-poc-fastembed-v1 | 140 | ⚠️ |" in md
    assert "platform-docs-poc-active" in md


def test_render_report_handles_unset_alias():
    md = report.render_report(
        run_id="r", status="failed", expected=10,
        counts={"c": 0}, aliases={"platform-docs-poc-active": ""},
    )
    assert "(unset)" in md
