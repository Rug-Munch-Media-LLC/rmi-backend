"""Unit tests for ``app/rag/qdrant_store.py`` — sparse vector build + upsert.

Covers:
  - build_sparse_vector: lexical sparse vector construction, stopword
    handling, and consistency with ``engine._build_sparse_query``
  - QdrantStore.point_id: stable uuid5 ids
  - QdrantStore.upsert: request shape (named dense + sparse vectors, payload)
    with a mocked AsyncQdrantClient — no network/DB
  - fail-open behavior: ensure_collection / client errors return False
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from qdrant_client import models

from app.rag.qdrant_store import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    QdrantStore,
    _normalize_sparse,
    build_sparse_vector,
)

# ── build_sparse_vector ────────────────────────────────────────────────────


def test_build_sparse_vector_token_hashes_and_tf_boost() -> None:
    sparse = build_sparse_vector("hello world hello")
    assert sparse is not None
    assert set(sparse["indices"]) == {
        int(__import__("hashlib").sha256(b"hello").hexdigest()[:8], 16),
        int(__import__("hashlib").sha256(b"world").hexdigest()[:8], 16),
    }
    # "hello" appears twice → 1.0 + 0.5*(2-1) = 1.5; "world" once → 1.0
    values_by_index = dict(zip(sparse["indices"], sparse["values"], strict=False))
    assert sorted(values_by_index.values()) == [1.0, 1.5]


def test_build_sparse_vector_returns_none_for_stopwords_and_empty() -> None:
    assert build_sparse_vector("") is None
    assert build_sparse_vector("the and for a") is None
    assert build_sparse_vector("   ") is None


def test_build_sparse_vector_case_insensitive_and_token_regex() -> None:
    sparse = build_sparse_vector("Hello HELLO!")
    assert sparse is not None
    values_by_index = dict(zip(sparse["indices"], sparse["values"], strict=False))
    assert values_by_index == {
        int(__import__("hashlib").sha256(b"hello").hexdigest()[:8], 16): 1.5  # same token, counted twice
    }


def test_build_sparse_vector_matches_engine_query_side() -> None:
    """Ingest-side sparse terms must match the query side exactly."""
    from app.rag.engine import _build_sparse_query

    assert build_sparse_vector("solana token analysis") == _build_sparse_query("solana token analysis")


# ── sparse normalization ───────────────────────────────────────────────────


def test_normalize_sparse_accepts_model_and_dict() -> None:
    assert _normalize_sparse(None) is None
    sv = models.SparseVector(indices=[1], values=[0.5])
    assert _normalize_sparse(sv) is sv
    out = _normalize_sparse({"indices": [1, 2], "values": [0.5, 0.7]})
    assert out == models.SparseVector(indices=[1, 2], values=[0.5, 0.7])


def test_normalize_sparse_rejects_mismatched_lengths() -> None:
    assert _normalize_sparse({"indices": [1, 2], "values": [0.5]}) is None
    assert _normalize_sparse({"indices": [], "values": []}) is None
    assert _normalize_sparse({}) is None


# ── point ids ──────────────────────────────────────────────────────────────


def test_point_id_stable_uuid5() -> None:
    pid1 = QdrantStore.point_id("doc-1")
    pid2 = QdrantStore.point_id("doc-1")
    assert pid1 == pid2
    assert QdrantStore.point_id("doc-2") != pid1


# ── upsert shape (mocked client) ───────────────────────────────────────────


def _store_with_fake_client() -> tuple[QdrantStore, MagicMock]:
    store = QdrantStore(url="http://fake-qdrant:6333", collection_name="test_col")
    client = MagicMock()
    client.upsert = AsyncMock(return_value=None)
    client.collection_exists = AsyncMock(return_value=False)
    client.create_collection = AsyncMock(return_value=None)
    store._client = client
    store._collection_ensured = True  # skip probe network calls in upsert
    return store, client


@pytest.mark.asyncio
async def test_upsert_sends_named_dense_and_sparse_vectors() -> None:
    store, client = _store_with_fake_client()
    ok = await store.upsert(
        "doc-1",
        [0.1, 0.2, 0.3],
        sparse_vector={"indices": [5], "values": [0.7]},
        payload={"content": "hello", "metadata": {"source": "test"}},
    )
    assert ok is True
    client.upsert.assert_awaited_once()
    _, kwargs = client.upsert.await_args
    assert kwargs["collection_name"] == "test_col"
    (point,) = kwargs["points"]
    assert point.id == QdrantStore.point_id("doc-1")
    assert point.payload == {"content": "hello", "metadata": {"source": "test"}}
    vector = point.vector
    assert vector[DENSE_VECTOR_NAME] == [0.1, 0.2, 0.3]
    assert vector[SPARSE_VECTOR_NAME] == models.SparseVector(indices=[5], values=[0.7])


@pytest.mark.asyncio
async def test_upsert_dense_only_when_sparse_none() -> None:
    store, client = _store_with_fake_client()
    await store.upsert("doc-1", [1.0], payload={"content": "x"})
    _, kwargs = client.upsert.await_args
    (point,) = kwargs["points"]
    assert SPARSE_VECTOR_NAME not in point.vector
    assert point.vector[DENSE_VECTOR_NAME] == [1.0]


@pytest.mark.asyncio
async def test_upsert_mismatched_sparse_falls_back_to_dense_only() -> None:
    store, client = _store_with_fake_client()
    await store.upsert("doc-1", [1.0], sparse_vector={"indices": [1], "values": []})
    _, kwargs = client.upsert.await_args
    (point,) = kwargs["points"]
    assert SPARSE_VECTOR_NAME not in point.vector


@pytest.mark.asyncio
async def test_upsert_default_payload_empty_dict() -> None:
    store, client = _store_with_fake_client()
    await store.upsert("doc-1", [1.0])
    _, kwargs = client.upsert.await_args
    (point,) = kwargs["points"]
    assert point.payload == {}


@pytest.mark.asyncio
async def test_upsert_ensures_collection_when_not_ensured() -> None:
    store = QdrantStore(url="http://fake-qdrant:6333", collection_name="test_col")
    client = MagicMock()
    client.collection_exists = AsyncMock(return_value=False)
    client.create_collection = AsyncMock(return_value=None)
    client.upsert = AsyncMock(return_value=None)
    store._client = client
    ok = await store.upsert("doc-1", [1.0])
    assert ok is True
    client.collection_exists.assert_awaited_once_with("test_col")
    _, kwargs = client.create_collection.await_args
    assert kwargs["collection_name"] == "test_col"
    assert kwargs["vectors_config"][DENSE_VECTOR_NAME].size == store.vector_size
    assert DENSE_VECTOR_NAME in kwargs["vectors_config"]
    assert SPARSE_VECTOR_NAME in kwargs["sparse_vectors_config"]


@pytest.mark.asyncio
async def test_upsert_fail_open_on_client_error() -> None:
    store, client = _store_with_fake_client()
    client.upsert = AsyncMock(side_effect=OSError("qdrant down"))
    assert await store.upsert("doc-1", [1.0]) is False


@pytest.mark.asyncio
async def test_upsert_false_when_collection_cannot_be_ensured() -> None:
    store = QdrantStore(url="http://fake-qdrant:6333", collection_name="test_col")
    client = MagicMock()
    client.collection_exists = AsyncMock(side_effect=OSError("no server"))
    store._client = client
    assert await store.upsert("doc-1", [1.0]) is False
