"""Fail-open tests for ``app/rag/ann_index.py`` Redis persistence.

Regression for the RAG story-ingest e2e failure: ``ingest_url`` crashed
with ``'NoneType' object has no attribute 'pipeline'`` when the Redis
client was unavailable (host-side auth misconfig → ``app.core.redis.get_redis``
returns None). The ANN persist path must degrade gracefully: skip the Redis
write with a logged reason, keep the in-memory index authoritative, and let
the Qdrant primary write (``engine._qdrant_upsert_chunk``) proceed.

Mock boundary: ``app.rag.ann_index._get_redis`` and the embedder/Qdrant
write are patched — no live Redis, no live Qdrant, no network.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from app.rag.ann_index import ANNIndex, get_index

DIM = 1024


def _vec(seed: float = 0.1) -> list[float]:
    return [seed] * DIM


# ── Fakes (minimal redis interface used by ANNIndex) ───────────────────────


class _FakePipe:
    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, str, str]] = []
        self.set_calls: list[tuple[str, str]] = []
        self.executed = False

    def hset(self, key: str, doc_id: str, blob: str) -> _FakePipe:
        self.hset_calls.append((key, doc_id, blob))
        return self

    def set(self, key: str, value: str) -> _FakePipe:
        self.set_calls.append((key, value))
        return self

    def execute(self) -> list[Any]:
        self.executed = True
        return []


class _FakeRedis:
    def __init__(self) -> None:
        self.pipes: list[_FakePipe] = []
        self.deleted: list[tuple[str, ...]] = []

    def pipeline(self) -> _FakePipe:
        pipe = _FakePipe()
        self.pipes.append(pipe)
        return pipe

    def hgetall(self, key: str) -> dict[str, str]:
        return {}

    def delete(self, *keys: str) -> int:
        self.deleted.append(keys)
        return 1


# ── Regression: None client must never raise (the original bug) ───────────


def test_add_with_none_redis_fails_open_and_keeps_memory() -> None:
    """The original crash: r.pipeline() on None → AttributeError.

    With ``_get_redis()`` returning None, add() must not raise; the
    in-memory index stays authoritative (search/count still work).
    """
    idx = ANNIndex("unit_ann_none")
    with mock.patch("app.rag.ann_index._get_redis", return_value=None):
        idx.add("doc-1", _vec(), {"source": "test"}, text="hello world")
        idx.add("doc-2", _vec(0.2), {"source": "test"}, text="second doc")

    assert idx.count() == 2
    hits = idx.search(_vec(), top_k=1)
    assert len(hits) == 1
    assert hits[0].doc_id in {"doc-1", "doc-2"}
    # Dirty docs stay tracked so a later flush_all() (with Redis back) retries.
    assert len(idx._dirty_ids) == 2


def test_persist_noop_never_touches_redis() -> None:
    """A no-op persist (nothing dirty) must not call the Redis client at all."""
    idx = ANNIndex("unit_ann_noop")
    with mock.patch("app.rag.ann_index._get_redis") as m:
        idx._persist()
    m.assert_not_called()


def test_delete_with_none_redis_fails_open() -> None:
    idx = ANNIndex("unit_ann_del")
    with mock.patch("app.rag.ann_index._get_redis", return_value=None):
        idx.add("doc-1", _vec(), {}, text="x")
        assert idx.delete("doc-1") is True
        assert idx.count() == 0


def test_clear_with_none_redis_fails_open() -> None:
    idx = ANNIndex("unit_ann_clear")
    with mock.patch("app.rag.ann_index._get_redis", return_value=None):
        idx.add("doc-1", _vec(), {}, text="x")
        idx.clear()  # must not raise
    assert idx.count() == 0


# ── Normal path: Redis available → pipeline write happens ─────────────────


def test_add_with_redis_persists_via_pipeline() -> None:
    fake = _FakeRedis()
    idx = ANNIndex("unit_ann_ok")
    with mock.patch("app.rag.ann_index._get_redis", return_value=fake):
        idx.add("doc-1", _vec(), {"source": "test"}, text="hello")

    assert len(fake.pipes) == 1
    pipe = fake.pipes[0]
    assert pipe.executed is True
    assert len(pipe.hset_calls) == 1
    assert pipe.hset_calls[0][0] == "rag:ann:unit_ann_ok:docs"
    assert pipe.hset_calls[0][1] == "doc-1"
    assert len(pipe.set_calls) == 1
    assert idx._dirty_ids == set()


def test_persist_redis_execute_error_fails_open_keeps_dirty() -> None:
    class _BoomPipe(_FakePipe):
        def execute(self) -> list[Any]:
            raise ConnectionError("redis went away")

    class _BoomRedis(_FakeRedis):
        def pipeline(self) -> _FakePipe:
            pipe = _BoomPipe()
            self.pipes.append(pipe)
            return pipe

    boom = _BoomRedis()
    idx = ANNIndex("unit_ann_boom")
    with mock.patch("app.rag.ann_index._get_redis", return_value=boom):
        idx.add("doc-1", _vec(), {}, text="x")  # must not raise

    # Dirty set preserved so flush_all() can retry once Redis is back.
    assert "doc-1" in idx._dirty_ids
    assert idx.count() == 1


def test_add_skips_non_serializable_metadata_doc() -> None:
    fake = _FakeRedis()
    idx = ANNIndex("unit_ann_ser")
    with mock.patch("app.rag.ann_index._get_redis", return_value=fake):
        # `set` is not JSON-serializable — must not abort the ingest.
        idx.add("bad-doc", _vec(), {"weird": {"a", "b"}}, text="bad")
        idx.add("good-doc", _vec(0.3), {"source": "test"}, text="good")

    assert idx.count() == 2
    assert len(fake.pipes) >= 1
    # At least the good doc reached the pipe hset; the bad one was skipped.
    written = {pid for pipe in fake.pipes for (_k, pid, _b) in pipe.hset_calls}
    assert "good-doc" in written
    assert "bad-doc" not in written


# ── Glue: ingest_url completes when ANN Redis is unavailable ───────────────


@pytest.mark.asyncio
async def test_ingest_url_completes_when_ann_redis_unavailable() -> None:
    """End-to-end glue through the real engine path (not the real stores).

    Simulates the original failing environment: ``ann_index._get_redis``
    returns None (auth misconfig) while the embedder and the Qdrant primary
    write are healthy. ``ingest_url`` must return status=ok with chunks,
    NOT raise AttributeError.
    """
    upsert_mock = mock.AsyncMock(return_value=True)
    with (
        mock.patch("app.rag.ann_index._get_redis", return_value=None),
        mock.patch(
            "app.rag.engine.get_embedding",
            new=mock.AsyncMock(side_effect=lambda _t: _vec()),
        ),
        mock.patch(
            "app.rag.embeddings.get_embedding",
            new=mock.AsyncMock(side_effect=lambda _t: _vec()),
        ),
        mock.patch("app.rag.qdrant_store.upsert_rag_document", new=upsert_mock),
    ):
        from app.rag.story_ingest import ingest_url

        result = await ingest_url(
            "https://example.com/unit-ann-unavailable",
            content=("Story body for the fail-open ANN path. " * 40),
            title="Unit Fail-Open",
        )

    assert result["status"] == "ok"
    assert result["chunks"] >= 1
    # The Qdrant primary write path ran despite Redis being unavailable.
    assert upsert_mock.await_count >= result["chunks"]


def test_get_index_cache_shared() -> None:
    assert get_index("unit_ann_cache") is get_index("unit_ann_cache")
    get_index("unit_ann_cache").clear()
