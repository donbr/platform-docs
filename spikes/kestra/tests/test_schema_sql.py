from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "sql" / "001_orchestration_schema.sql").read_text().lower()


def test_creates_isolated_schemas():
    assert "create schema if not exists orchestration" in SQL
    assert "create schema if not exists kestra_system" in SQL


def test_pipeline_runs_has_required_columns():
    for col in ["run_id", "flow", "source", "stage", "status", "environment",
                "docs_expected", "docs_uploaded", "collection_version",
                "alias_swapped_at", "started_at", "finished_at", "error"]:
        assert col in SQL, f"missing column: {col}"


def test_environment_defaults_to_poc():
    assert "default 'poc'" in SQL
