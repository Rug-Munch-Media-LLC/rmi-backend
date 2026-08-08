"""Tests for whale transfer RAG indexing (app.domains.scanner.whale_indexer).

Covers:
  - payload normalization from the scanner provider event shape
  - compact doc builder (exact format)
  - live indexing into Qdrant with a mocked HTTP client
  - fail-open behavior when Qdrant / embedding is down
  - bounded backfill from ClickHouse (limit clamping, unavailable CH)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.scanner import whale_indexer
from app.domains.scanner.whale_indexer import (
    COLLECTION,
    MAX_BACKFILL,
    VECTOR_SIZE,
    backfill_whale_transfers,
    build_whale_doc,
    ensure_collection,
    extract_whale_payload,
    index_whale_event,
    point_id,
)

# ── Helpers ────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text or json.dumps(self._json)

    def json(self) -> dict:
        return self._json


class FakeQdrant:
    """Records requests; 404s on the collection probe so creation is tested."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[tuple[str, str, dict | None]] = []
        self.created: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.requests.append(("GET", url, None))
        if url == f"/collections/{COLLECTION}" and COLLECTION not in self.created:
            return FakeResponse(404, {}, "not found")
        return FakeResponse(self.status)

    async def put(self, url: str, json: dict | None = None) -> FakeResponse:
        self.requests.append(("PUT", url, json))
        if url.startswith("/collections/") and url.endswith("/points"):
            return FakeResponse(self.status)
        if url.startswith("/collections/"):
            self.created.append(url.split("/")[-1])
            return FakeResponse(200, {"result": True})
        return FakeResponse(self.status)


class FakeQdrantDown:
    async def get(self, url: str) -> FakeResponse:
        raise httpx.ConnectError("connection refused")

    async def put(self, url: str, json: dict | None = None) -> FakeResponse:
        raise httpx.ConnectError("connection refused")


def _provider_event(**overrides: object) -> dict:
    """Event in the exact shape WhaleMoveProvider.poll() emits."""
    event: dict = {
        "chain": "ethereum",
        "block_timestamp": "2026-08-01 12:00:00",
        "transaction_hash": "0xabc123",
        "from_address": "0xSELLER",
        "to_address": "0xBUYER",
        "token_address": "0xTokenAddress1234567890",
        "value_decimal": 25000.0,
        "transfer_type": "transfer",
    }
    event.update(overrides)
    return event


# ── Payload normalization ──────────────────────────────────────────────


class TestExtractWhalePayload:
    def test_normalizes_provider_event(self) -> None:
        payload = extract_whale_payload(_provider_event())
        assert payload["chain"] == "ethereum"
        assert payload["wallet"] == "0xBUYER"  # defaults to receiving wallet
        assert payload["side"] == "bought"
        assert payload["address"] == "0xTokenAddress1234567890"
        assert payload["value_usd"] == 25000.0
        assert payload["ts"] == "2026-08-01 12:00:00"
        assert payload["tx_hash"] == "0xabc123"
        assert payload["smart_money"] is False

    def test_side_inferred_sold_when_wallet_is_sender(self) -> None:
        payload = extract_whale_payload(_provider_event(wallet="0xSELLER"))
        assert payload["side"] == "sold"

    def test_side_and_wallet_overrides_win(self) -> None:
        payload = extract_whale_payload(_provider_event(wallet="0xWATCHED", side="sold", smart_money=True))
        assert payload["wallet"] == "0xWATCHED"
        assert payload["side"] == "sold"
        assert payload["smart_money"] is True

    def test_value_usd_falls_back_to_value_decimal(self) -> None:
        payload = extract_whale_payload(_provider_event(value_decimal=999.5))
        assert payload["value_usd"] == 999.5

    def test_token_label_shortened_when_no_symbol(self) -> None:
        payload = extract_whale_payload(_provider_event(token=""))
        assert payload["token"] == "0xToke…7890"

    def test_missing_timestamp_gets_now(self) -> None:
        payload = extract_whale_payload(_provider_event(block_timestamp=None))
        assert payload["ts"] != ""


# ── Doc builder ────────────────────────────────────────────────────────


class TestBuildWhaleDoc:
    def test_exact_doc_format(self) -> None:
        payload = extract_whale_payload(_provider_event(smart_money=True))
        doc = build_whale_doc(payload)
        assert doc == (
            "Whale 0xBUYER bought $25,000 of 0xToke…7890 "
            "(0xTokenAddress1234567890) on ethereum at 2026-08-01 12:00:00. "
            "Smart-money: yes. Tx: 0xabc123"
        )

    def test_smart_money_no(self) -> None:
        doc = build_whale_doc(extract_whale_payload(_provider_event()))
        assert "Smart-money: no." in doc

    def test_value_formatted_with_thousands_separator(self) -> None:
        doc = build_whale_doc(extract_whale_payload(_provider_event(value_decimal=1234567.89)))
        assert "$1,234,568 of" in doc


# ── Point IDs ──────────────────────────────────────────────────────────


class TestPointId:
    def test_deterministic_per_tx(self) -> None:
        assert point_id("0xabc") == point_id("0xabc")
        assert point_id("0xabc") != point_id("0xdef")


# ── Live indexing ──────────────────────────────────────────────────────


class TestIndexWhaleEvent:
    @pytest.fixture(autouse=True)
    def _reset_collection_flag(self) -> None:
        whale_indexer._COLLECTION_ENSURED = False
        yield
        whale_indexer._COLLECTION_ENSURED = False

    async def test_indexes_event_into_whale_transfers(self) -> None:
        client = FakeQdrant()
        vector = [0.1] * VECTOR_SIZE
        with patch("app.domains.scanner.whale_indexer.get_embedding", new=AsyncMock(return_value=vector)):
            result = await index_whale_event(_provider_event(), client=client)

        assert result["indexed"] is True
        assert result["collection"] == COLLECTION
        assert result["point_id"] == point_id("0xabc123")
        assert "Whale 0xBUYER bought $25,000 of" in result["doc"]

        # Collection auto-created on first index
        assert COLLECTION in client.created

        # Upsert payload carries content + metadata + top-level tags
        put_requests = [r for r in client.requests if r[0] == "PUT" and r[1].endswith("/points")]
        assert len(put_requests) == 1
        _, _, body = put_requests[0]
        assert body is not None
        point = body["points"][0]
        assert point["id"] == point_id("0xabc123")
        assert point["vector"] == vector
        assert point["payload"]["content"].startswith("Whale 0xBUYER bought")
        assert point["payload"]["metadata"]["token"] == "0xToke…7890"
        assert point["payload"]["metadata"]["side"] == "bought"
        assert point["payload"]["metadata"]["value_usd"] == 25000.0
        assert point["payload"]["metadata"]["tx_hash"] == "0xabc123"
        assert point["payload"]["source"] == "whale_move"
        assert point["payload"]["chain"] == "ethereum"
        assert point["payload"]["collection"] == COLLECTION

    async def test_reindex_same_tx_is_idempotent_point_id(self) -> None:
        client = FakeQdrant()
        vector = [0.1] * VECTOR_SIZE
        with patch("app.domains.scanner.whale_indexer.get_embedding", new=AsyncMock(return_value=vector)):
            await index_whale_event(_provider_event(), client=client)
            await index_whale_event(_provider_event(), client=client)

        put_requests = [r for r in client.requests if r[0] == "PUT" and r[1].endswith("/points")]
        assert len(put_requests) == 2
        assert put_requests[0][2]["points"][0]["id"] == put_requests[1][2]["points"][0]["id"]

    async def test_fails_open_when_qdrant_down(self) -> None:
        result = await index_whale_event(
            _provider_event(),
            client=FakeQdrantDown(),
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is False
        assert "error" in result

    async def test_fails_open_when_embedding_down(self) -> None:
        client = FakeQdrant()
        with patch(
            "app.domains.scanner.whale_indexer.get_embedding",
            new=AsyncMock(side_effect=RuntimeError("embedder down")),
        ):
            result = await index_whale_event(_provider_event(), client=client)
        assert result["indexed"] is False

    async def test_rejects_dim_mismatch(self) -> None:
        result = await index_whale_event(
            _provider_event(),
            client=FakeQdrant(),
            vector=[0.1] * 768,  # wrong dim — must not be upserted
        )
        assert result["indexed"] is False
        assert "dim" in result["error"]


# ── Bounded backfill ───────────────────────────────────────────────────


class TestBackfill:
    @pytest.fixture(autouse=True)
    def _reset_collection_flag(self) -> None:
        whale_indexer._COLLECTION_ENSURED = False
        yield
        whale_indexer._COLLECTION_ENSURED = False

    def _fake_clickhouse(self, rows: list[tuple]) -> object:
        class FakeCH:
            def __init__(self) -> None:
                self.executed: list[tuple[str, dict]] = []

            def execute(self, query: str, params: dict) -> list[tuple]:
                self.executed.append((query, params))
                return rows

        return FakeCH()

    def test_backfill_clamps_limit_to_max(self) -> None:
        ch = self._fake_clickhouse(
            rows=[("ethereum", "2026-08-01", f"0xtx{i}", "0xF", "0xB", "0xA", 20000.0, "transfer") for i in range(10)]
        )

        with (
            patch("app.domains.scanner.whale_indexer.get_clickhouse", return_value=ch),
            patch(
                "app.domains.scanner.whale_indexer.index_whale_event", new=AsyncMock(return_value={"indexed": True})
            ) as mock_index,
        ):
            result = asyncio.run(backfill_whale_transfers(limit=9999))

        assert result["limit"] == MAX_BACKFILL
        query, params = ch.executed[0]
        assert "LIMIT %(limit)s" in query
        assert params == {"min_val": 10_000.0, "limit": 500}
        assert mock_index.await_count == 10  # only as many rows as returned

    def test_backfill_indexes_each_row(self) -> None:
        ch = self._fake_clickhouse(
            rows=[
                ("solana", "2026-08-01 10:00:00", "0xtx1", "0xS", "0xB", "0xTOK", 50000.0, "transfer"),
                ("ethereum", "2026-08-01 11:00:00", "0xtx2", "0xA", "0xC", "0xTOK2", 20000.0, "transfer"),
            ]
        )
        with (
            patch("app.domains.scanner.whale_indexer.get_clickhouse", return_value=ch),
            patch(
                "app.domains.scanner.whale_indexer.index_whale_event", new=AsyncMock(return_value={"indexed": True})
            ) as mock_index,
        ):
            result = asyncio.run(backfill_whale_transfers(limit=500))

        assert result["backfilled"] == 2
        assert result["errors"] == 0
        # Row payloads are passed through in provider shape (positional arg)
        args = [c.args[0] for c in mock_index.await_args_list]
        assert args[0]["chain"] == "solana"
        assert args[0]["value_decimal"] == 50000.0

    def test_backfill_clickhouse_unavailable(self) -> None:
        with patch("app.domains.scanner.whale_indexer.get_clickhouse", return_value=None):
            import asyncio

            result = asyncio.run(backfill_whale_transfers(limit=500))
        assert result == {"backfilled": 0, "limit": 500, "errors": 0}

    def test_backfill_zero_limit_noop(self) -> None:
        with (
            patch("app.domains.scanner.whale_indexer.get_clickhouse", return_value=object()),
            patch("app.domains.scanner.whale_indexer.index_whale_event", new=AsyncMock()) as mock_index,
        ):
            import asyncio

            result = asyncio.run(backfill_whale_transfers(limit=0))
        assert result["backfilled"] == 0
        mock_index.assert_not_awaited()


# ── ensure_collection ──────────────────────────────────────────────────


class TestEnsureCollection:
    @pytest.fixture(autouse=True)
    def _reset_flag(self) -> None:
        whale_indexer._COLLECTION_ENSURED = False
        yield
        whale_indexer._COLLECTION_ENSURED = False

    async def test_creates_collection_when_missing(self) -> None:
        client = FakeQdrant()
        assert await ensure_collection(client) is True
        assert COLLECTION in client.created
        create_req = next(r for r in client.requests if r[0] == "PUT" and r[1] == f"/collections/{COLLECTION}")
        assert create_req[2]["vectors"] == {"size": VECTOR_SIZE, "distance": "Cosine"}

    async def test_does_not_recreate(self) -> None:
        client = FakeQdrant()
        client.created.append(COLLECTION)  # already exists
        assert await ensure_collection(client) is True
        assert not any(r[1] == f"/collections/{COLLECTION}" and r[0] == "PUT" for r in client.requests)

    async def test_fails_open_on_unreachable(self) -> None:
        assert await ensure_collection(FakeQdrantDown()) is False
