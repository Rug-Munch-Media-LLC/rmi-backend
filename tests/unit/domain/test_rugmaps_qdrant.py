"""Unit tests for RugMaps Qdrant client (app/domains/rugmaps/qdrant.py).

Covers the Phase 0 ``RugMapsQdrant`` wrapper:
- create_collections() idempotency (existing vs created)
- audit() shape and dimension mismatch detection
- health() metrics aggregation
- error handling when Qdrant is unreachable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domains.rugmaps.qdrant import COLLECTIONS, RugMapsQdrant


@pytest.fixture
def qdrant() -> RugMapsQdrant:
    return RugMapsQdrant(url="http://test-qdrant:6333")


@pytest.fixture
def fake_client() -> MagicMock:
    client = MagicMock()
    client.collection_exists = AsyncMock(return_value=False)
    client.create_collection = AsyncMock(return_value=True)
    client.get_collection = AsyncMock(return_value={})
    client.close = AsyncMock()
    return client


def test_collections_definition() -> None:
    """All Phase 0 collections are 1024-dim dense vectors."""
    assert set(COLLECTIONS.keys()) == {
        "rgm_wallets",
        "rgm_clusters",
        "rgm_cluster_patterns",
        "rgm_entity_labels",
    }
    assert all(dim == 1024 for dim in COLLECTIONS.values())


@pytest.mark.asyncio
async def test_create_collections_creates_missing(qdrant: RugMapsQdrant, fake_client: MagicMock) -> None:
    """create_collections() creates every missing collection."""
    qdrant.client = fake_client
    results = await qdrant.create_collections()

    assert all(ok for ok in results.values())
    assert fake_client.create_collection.await_count == len(COLLECTIONS)
    # named dense vector config with cosine distance
    _, kwargs = fake_client.create_collection.await_args_list[0]
    assert kwargs["vectors_config"]["dense"].size == 1024
    assert kwargs["vectors_config"]["dense"].distance.name == "COSINE"
    assert kwargs["hnsw_config"].m == 16
    assert kwargs["hnsw_config"].ef_construct == 200


@pytest.mark.asyncio
async def test_create_collections_idempotent(qdrant: RugMapsQdrant, fake_client: MagicMock) -> None:
    """create_collections() skips collections that already exist."""
    qdrant.client = fake_client
    fake_client.collection_exists = AsyncMock(return_value=True)
    results = await qdrant.create_collections()

    assert all(not ok for ok in results.values())
    fake_client.create_collection.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_collections_handles_error(qdrant: RugMapsQdrant, fake_client: MagicMock) -> None:
    """A failing create is reported as False, not raised."""
    qdrant.client = fake_client
    fake_client.create_collection = AsyncMock(side_effect=RuntimeError("boom"))
    results = await qdrant.create_collections()

    assert all(not ok for ok in results.values())


@pytest.mark.asyncio
async def test_audit_reports_dimension_mismatch(qdrant: RugMapsQdrant) -> None:
    """audit() flags a collection whose vector dim differs from expected."""
    from types import SimpleNamespace

    dense = SimpleNamespace(size=512, distance="Cosine")
    client = MagicMock()
    client.get_collection = AsyncMock(
        return_value=SimpleNamespace(
            points_count=5,
            config=SimpleNamespace(params=SimpleNamespace(vectors={"dense": dense})),
        )
    )
    qdrant.client = client
    rows = await qdrant.audit()

    assert len(rows) == len(COLLECTIONS)
    mismatched = [r for r in rows if r.get("mismatch")]
    assert len(mismatched) == len(COLLECTIONS)  # all 512 vs expected 1024


@pytest.mark.asyncio
async def test_audit_missing_collection(qdrant: RugMapsQdrant, fake_client: MagicMock) -> None:
    """audit() reports missing collections without crashing."""
    qdrant.client = fake_client  # get_collection returns None -> missing
    fake_client.get_collection = AsyncMock(return_value=None)
    rows = await qdrant.audit()

    assert all(not row["exists"] for row in rows)


@pytest.mark.asyncio
async def test_health_aggregates_points(qdrant: RugMapsQdrant) -> None:
    """health() sums point counts across rgm_* collections."""
    from types import SimpleNamespace

    dense = SimpleNamespace(size=1024, distance="Cosine")
    client = MagicMock()
    client.get_collection = AsyncMock(
        return_value=SimpleNamespace(
            points_count=3,
            config=SimpleNamespace(params=SimpleNamespace(vectors={"dense": dense})),
        )
    )
    qdrant.client = client
    metrics = await qdrant.health()

    assert metrics["status"] == "ok"
    assert metrics["collections"] == len(COLLECTIONS)
    assert metrics["total_points"] == 3 * len(COLLECTIONS)
    assert len(metrics["collections_status"]) == len(COLLECTIONS)


@pytest.mark.asyncio
async def test_health_error_path(qdrant: RugMapsQdrant) -> None:
    """health() returns status error when Qdrant is unreachable."""
    qdrant.client = MagicMock()
    qdrant.client.get_collection = AsyncMock(side_effect=Exception("connection refused"))
    metrics = await qdrant.health()

    assert metrics["status"] == "error"
    assert "error" in metrics


@pytest.mark.asyncio
async def test_close_is_best_effort(qdrant: RugMapsQdrant) -> None:
    """close() swallows client close errors."""
    qdrant.client = MagicMock()
    qdrant.client.close = AsyncMock(side_effect=RuntimeError("already closed"))
    await qdrant.close()  # must not raise


@pytest.mark.asyncio
async def test_async_context_manager(qdrant: RugMapsQdrant) -> None:
    """RugMapsQdrant works as an async context manager."""
    client = MagicMock()
    client.close = AsyncMock()
    qdrant.client = client
    async with qdrant as q:
        assert q is qdrant
    client.close.assert_awaited_once()
