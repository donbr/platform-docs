"""Regression: the OpenAI embedding client must retry 429s (5M TPM) long enough to ride out
full-corpus refreshes instead of silently skipping batches."""
import importlib
import sys
from pathlib import Path

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")


def _load_embedding_config(monkeypatch):
    # embedding_config is imported bare by the uploaders, so scripts/ must be on sys.path.
    # Constructing OpenAIEmbeddings makes no network call.
    monkeypatch.syspath_prepend(SCRIPTS)
    sys.modules.pop("embedding_config", None)
    return importlib.import_module("embedding_config")


def test_get_embeddings_uses_embedding_max_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    ec = _load_embedding_config(monkeypatch)
    assert ec.get_embeddings().max_retries == ec.EMBEDDING_MAX_RETRIES


def test_embedding_max_retries_rides_out_tpm_429s(monkeypatch):
    ec = _load_embedding_config(monkeypatch)
    assert ec.EMBEDDING_MAX_RETRIES >= 6
