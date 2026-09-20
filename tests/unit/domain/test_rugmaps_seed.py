"""Unit tests for RugMaps wallet seeder (app/domains/rugmaps/seed.py).

Covers the Phase 0→1 seeder:
- embed text composition and deterministic point IDs
- ClickHouse row fetch + Python-side dedupe by (chain, address)
- end-to-end seed_wallets() stats with mocked ClickHouse/embed/upsert
- error tolerance when embedding or upserting fails
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.rugmaps import seed as seed_module
from app.domains.rugmaps.seed import (
    EMBEDDING_MODEL,
    WALLET_COLLECTION,
    _clickhouse_settings,
    _embed_batch,
    _embed_text,
    _point_id,
    seed_wallets,
)

SAMPLE_ROWS = [
    ("0x1111111111111111111111111111111111111111", "ethereum", "Binance Hot Wallet 10", "exchange"),
    ("0x2222222222222222222222222222222222222222", "bsc", "MEXC", "exchange"),
    ("0x3333333333333333333333333333333333333333", "ethereum", "Tornado Cash", "mixer"),
]


def test_embed_text_composes_type_and_label() -> None:
    """Embed text is ``<label_type>: <label>`` — semantic and readable."""
    assert _embed_text("exchange", "Binance Hot Wallet 10") == "exchange: Binance Hot Wallet 10"


def test_point_id_is_deterministic_and_chain_scoped() -> None:
    """Point IDs are stable across runs and unique per (chain, address)."""
    assert _point_id("ethereum", "0xABC") == _point_id("ethereum", "0xABC")
    assert _point_id("ethereum", "0xABC") != _point_id("bsc", "0xABC")
    assert _point_id("ethereum", "0xabc") == _point_id("ethereum", "0xABC")


def test_clickhouse_settings_uses_env() -> None:
    """ClickHouse connection settings resolve from env."""
    with patch.dict(
        "os.environ",
        {"CLICKHOUSE_HOST": "ch.local", "CLICKHOUSE_PORT": "9001", "CLICKHOUSE_USER": "u", "CLICKHOUSE_PASSWORD": "p"},
        clear=False,
    ):
        settings = _clickhouse_settings()
    assert settings["host"] == "ch.local"
    assert settings["port"] == 9001
    assert settings["user"] == "u"
    assert settings["password"] == "p"


@pytest.mark.asyncio
async def test_seed_wallets_end_to_end() -> None:
    """seed_wallets() fetches, embeds, and upserts with correct shapes."""
    fake_ch = MagicMock()
    fake_ch.execute.return_value = SAMPLE_ROWS

    def _fake_vectors(client, embed_url, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 1024 for _ in texts]

    with (
        patch("clickhouse_driver.Client", return_value=fake_ch),
        patch.object(seed_module, "_embed_batch", side_effect=_fake_vectors),
        patch.object(seed_module, "_upsert_points", new=AsyncMock(return_value=3)) as upsert,
    ):
        stats = await seed_wallets(limit=10, qdrant_url="http://qdrant:6333", embed_url="http://infinity:8001")

    assert stats["rows_fetched"] == 3
    assert stats["embedded"] == 3
    assert stats["zero_vectors"] == 0
    assert stats["upserted"] == 3
    assert stats["failed"] == 0
    # Query targets the wallet_memory table by default
    query = fake_ch.execute.call_args[0][0]
    assert "wallet_memory.wallet_labels" in query
    # Upsert batch carries 1024-dim named dense vectors + payload
    points = upsert.call_args[0][1]
    assert len(points) == 3
    vec = points[0].vector["dense"]
    assert len(vec) == 1024
    assert points[0].payload["chain"] == "ethereum"
    assert points[0].payload["label"] == "Binance Hot Wallet 10"
    assert points[0].payload["label_type"] == "exchange"


@pytest.mark.asyncio
async def test_seed_wallets_dedupes_by_address_chain() -> None:
    """Duplicate (chain, address) pairs are collapsed; first (most recent) wins."""
    dup_rows = [
        ("0xAAA", "ethereum", "Newest Label", "scam"),
        ("0xBBB", "ethereum", "Solo", "exchange"),
        ("0xAAA", "ethereum", "Older Label", "scam"),
        ("0xCCC", "bsc", "Gate.io", "exchange"),
    ]
    fake_ch = MagicMock()
    fake_ch.execute.return_value = dup_rows

    captured: list[list[str]] = []

    async def _fake_embed(http, url, texts):
        captured.append(texts)
        return [[0.01] * 1024 for _ in texts]

    with (
        patch("clickhouse_driver.Client", return_value=fake_ch),
        patch.object(seed_module, "_embed_batch", side_effect=_fake_embed),
        patch.object(seed_module, "_upsert_points", new=AsyncMock(return_value=3)),
    ):
        stats = await seed_wallets(limit=10, qdrant_url="http://qdrant:6333", embed_url="http://infinity:8001")

    assert stats["rows_fetched"] == 3
    assert stats["duplicates_skipped"] == 1
    texts = captured[0]
    assert len(texts) == 3
    assert "Newest Label" in texts[0]  # most recent row for 0xAAA wins
    assert "Older Label" not in " ".join(texts)


@pytest.mark.asyncio
async def test_seed_wallets_embed_failure_is_tolerated() -> None:
    """A failing embed batch increments failed count and does not crash."""
    fake_ch = MagicMock()
    fake_ch.execute.return_value = SAMPLE_ROWS

    with (
        patch("clickhouse_driver.Client", return_value=fake_ch),
        patch.object(seed_module, "_embed_batch", side_effect=RuntimeError("infinity down")),
        patch.object(seed_module, "_upsert_points", new=AsyncMock()),
    ):
        stats = await seed_wallets(limit=10, qdrant_url="http://qdrant:6333", embed_url="http://infinity:8001")

    assert stats["rows_fetched"] == 3
    assert stats["failed"] == 3
    assert stats["upserted"] == 0


@pytest.mark.asyncio
async def test_embed_batch_builds_request() -> None:
    """_embed_batch posts to /embeddings with the Qwen3-Embedding-4B model."""
    client = MagicMock()
    client.post = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {"data": [{"index": 0, "embedding": [0.5] * 1024}]},
        )
    )
    vectors = await _embed_batch(client, "http://infinity:8001", ["test label"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024
    _, kwargs = client.post.call_args
    assert kwargs["json"]["model"] == EMBEDDING_MODEL
    assert kwargs["json"]["input"] == ["test label"]


def test_seed_targets_expected_collection() -> None:
    """Seeder writes to rgm_wallets (the Phase 0 wallet collection)."""
    assert WALLET_COLLECTION == "rgm_wallets"
