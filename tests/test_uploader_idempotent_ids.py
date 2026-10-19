"""Uploaders must use deterministic point IDs (idempotent upserts) and retry failed batches."""
import importlib
import time
import uuid
from pathlib import Path

import pytest
from langchain_core.documents import Document

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
MODULES = ["upload_to_qdrant", "upload_to_qdrant_fastembed"]


@pytest.fixture(params=MODULES)
def uploader(request, monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS)
    return importlib.import_module(request.param)


def _batch(n=3):
    return [Document(page_content=f"c{i}", metadata={"doc_id": f"Src_{i}"}) for i in range(n)]


class FakeStore:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.calls = []

    def add_documents(self, documents, ids=None, **kw):
        self.calls.append(list(ids))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("DEADLINE_EXCEEDED")
        return list(ids)


def test_point_id_deterministic_valid_distinct(uploader):
    a = uploader.point_id("Zep_1")
    assert a == uploader.point_id("Zep_1")
    assert str(uuid.UUID(a)) == a
    assert a != uploader.point_id("Zep_2")


def test_namespaces_match_across_modules(monkeypatch):
    monkeypatch.syspath_prepend(SCRIPTS)
    a, b = (importlib.import_module(m) for m in MODULES)
    for d in ["Zep_1", "Anthropic_42", "x"]:
        assert a.point_id(d) == b.point_id(d)


def test_upload_batch_passes_deterministic_ids(uploader):
    store, batch = FakeStore(), _batch()
    uploader.upload_batch(batch, store, 0)
    assert store.calls == [[uploader.point_id(d.metadata["doc_id"]) for d in batch]]


def test_upload_batch_retries_then_succeeds(uploader, monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    store, batch = FakeStore(fail_times=2), _batch()
    result = uploader.upload_batch(batch, store, 0)
    expected = [uploader.point_id(d.metadata["doc_id"]) for d in batch]
    assert len(store.calls) == 3
    assert all(c == expected for c in store.calls)
    assert result == expected
    assert sleeps == [2, 4]


def test_upload_batch_reraises_after_three_failures(uploader, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    store = FakeStore(fail_times=99)
    with pytest.raises(RuntimeError):
        uploader.upload_batch(_batch(), store, 0)
    assert len(store.calls) == 3
