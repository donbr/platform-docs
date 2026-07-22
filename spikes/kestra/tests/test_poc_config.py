import json
from pathlib import Path

from spikes.kestra import poc_config


def test_constants_never_reference_production():
    for c in (poc_config.POC_COLLECTION, poc_config.POC_COLLECTION_FASTEMBED):
        assert c not in poc_config.PROD_COLLECTIONS
    for a in (poc_config.POC_ALIAS, poc_config.POC_ALIAS_FASTEMBED):
        assert a not in poc_config.PROD_ALIASES
        assert a.endswith("-poc-active")


def test_expected_doc_count_counts_json_excluding_manifest(tmp_path: Path):
    src = tmp_path / "OpenAI"
    src.mkdir()
    (src / "0001.json").write_text(json.dumps({"content": "x"}))
    (src / "0002.json").write_text(json.dumps({"content": "y"}))
    (src / "manifest.json").write_text(json.dumps({"page_count": 2}))
    assert poc_config.expected_doc_count(["OpenAI"], tmp_path) == 2
