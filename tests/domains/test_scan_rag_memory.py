"""Tests for scan-to-RAG memory (app.rag.scan_memory) + scanner hooks.

Covers:
  - summary normalization from legacy / v2 / sentinel scan shapes
  - the should_index gate (token-not-found scans are skipped)
  - compact doc builder (exact format)
  - deterministic point IDs (same-day re-scan overwrites)
  - live indexing into Qdrant with a mocked HTTP client
  - fail-open behavior when Qdrant / embedding is down
  - ensure_collection create-on-demand
  - scanner engine hooks (canonical ``tools/scanner.py``, re-exported by the
    ``scanner.py`` shim): scan_token / scan_token_v2 / sentinel_scan schedule
    a background index with the expected source + result dict
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.telegram.rugmunchbot import scanner as bot_scanner
from app.rag import scan_memory
from app.rag.scan_memory import (
    COLLECTION,
    DISTANCE,
    VECTOR_SIZE,
    build_scan_doc,
    ensure_collection,
    extract_scan_summary,
    index_scan_memory,
    scan_point_id,
    should_index_scan,
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


def _legacy_result(**overrides: object) -> dict:
    """Legacy scan_token / v2_token_to_legacy result shape."""
    result: dict = {
        "address": "0xTokenAddress1234567890",
        "chain": "ethereum",
        "name": "TestCoin",
        "symbol": "TEST",
        "risk_score": 42,
        "honeypot": True,
        "liquidity": 80000.0,
        "holders": 1234,
        "threats": ["HONEYPOT DETECTED"],
        "scanned_at": "2026-08-08T10:00:00+00:00",
    }
    result.update(overrides)
    return result


def _v2_result(**overrides: object) -> dict:
    """Raw POST /api/v2/scanner/token/scan payload shape."""
    result: dict = {
        "status": "ok",
        "token": "0xV2TokenAddress123456",
        "chain": "solana",
        "name": "V2Coin",
        "symbol": "V2",
        "safety_score": 35.0,
        "risk_level": "high",
        "risk_flags": ["honeypot flag", "low liquidity"],
        "liquidity_usd": 12000.5,
        "evidence": {"holders": {"holder_count": 567}},
        "scanned_at": "2026-08-08T11:00:00+00:00",
    }
    result.update(overrides)
    return result


def _sentinel_result(**overrides: object) -> dict:
    """SENTINEL deep forensic report shape."""
    result: dict = {
        "token_address": "0xSentinelTokenAddress1234",
        "chain": "bsc",
        "name": "SentinelCoin",
        "composite_risk_score": 88,
        "risk_level": "critical",
        "red_flags": ["Honeypot module failed", "LP unlocked"],
        "holder_analysis": {"total_holders": 999},
        "liquidity_verification": {"total_locked_percentage": 10},
        "scanned_at": "2026-08-08T12:00:00+00:00",
    }
    result.update(overrides)
    return result


# ── Summary extraction ─────────────────────────────────────────────────


class TestExtractScanSummary:
    def test_legacy_shape(self) -> None:
        s = extract_scan_summary(_legacy_result(), source="scan_v1")
        assert s["name"] == "TestCoin"
        assert s["address"] == "0xTokenAddress1234567890"
        assert s["chain"] == "ethereum"
        assert s["risk_score"] == 42
        assert s["risk_max"] == 100
        assert s["honeypot"] is True
        assert s["liquidity"] == 80000.0
        assert s["holders"] == 1234
        assert s["timestamp"] == "2026-08-08T10:00:00+00:00"
        assert s["threats"] == ["HONEYPOT DETECTED"]

    def test_v2_shape_safety_to_risk(self) -> None:
        s = extract_scan_summary(_v2_result(), source="scan_v2")
        assert s["name"] == "V2Coin"
        assert s["address"] == "0xV2TokenAddress123456"
        assert s["chain"] == "solana"
        assert s["risk_score"] == 65.0  # 100 - safety(35)
        assert s["honeypot"] is True  # derived from risk_flags
        assert s["liquidity"] == 12000.5
        assert s["holders"] == 567

    def test_v2_shape_no_honeypot_flag(self) -> None:
        s = extract_scan_summary(_v2_result(risk_flags=["low liquidity"]), source="scan_v2")
        assert s["honeypot"] is None

    def test_sentinel_shape(self) -> None:
        s = extract_scan_summary(_sentinel_result(), source="sentinel")
        assert s["name"] == "SentinelCoin"
        assert s["address"] == "0xSentinelTokenAddress1234"
        assert s["chain"] == "bsc"
        assert s["risk_score"] == 88
        assert s["honeypot"] is True  # derived from red_flags
        assert s["holders"] == 999
        assert s["risk_level"] == "critical"

    def test_sentinel_name_falls_back_to_short_label(self) -> None:
        s = extract_scan_summary(_sentinel_result(name=""), source="sentinel")
        assert s["name"] == "0xSent…1234"

    def test_missing_timestamp_gets_now(self) -> None:
        s = extract_scan_summary(_legacy_result(scanned_at=None), source="scan_v1")
        assert s["timestamp"] != ""


# ── Gate ───────────────────────────────────────────────────────────────


class TestShouldIndexScan:
    def test_legacy_real_scan_indexes(self) -> None:
        assert should_index_scan(_legacy_result(), source="scan_v1") is True

    def test_legacy_not_found_skipped(self) -> None:
        assert should_index_scan({"name": "Unknown", "chain": "ethereum"}, source="scan_v1") is False
        assert should_index_scan({}, source="scan_v1") is False

    def test_v2_real_scan_indexes(self) -> None:
        assert should_index_scan(_v2_result(), source="scan_v2") is True

    def test_sentinel_always_indexes(self) -> None:
        # sentinel only returns a dict on a completed forensic scan
        assert should_index_scan({"token_address": "0xAB"}, source="sentinel") is True


# ── Doc builder ────────────────────────────────────────────────────────


class TestBuildScanDoc:
    def test_exact_doc_format(self) -> None:
        doc = build_scan_doc(extract_scan_summary(_legacy_result(), source="scan_v1"))
        assert doc == (
            "Token TestCoin (0xTokenAddress1234567890) on ethereum: "
            "risk_score=42/100, honeypot=true, liquidity=$80,000, "
            "holders=1,234. Scan at 2026-08-08T10:00:00+00:00."
        )

    def test_v2_doc_risk_from_safety(self) -> None:
        doc = build_scan_doc(extract_scan_summary(_v2_result(), source="scan_v2"))
        assert "risk_score=65/100" in doc
        assert "honeypot=true" in doc
        assert "liquidity=$12,000" in doc
        assert "holders=567" in doc

    def test_sentinel_doc(self) -> None:
        doc = build_scan_doc(extract_scan_summary(_sentinel_result(), source="sentinel"))
        assert "risk_score=88/100" in doc
        assert "honeypot=true" in doc
        assert "holders=999" in doc

    def test_honeypot_false_rendering(self) -> None:
        doc = build_scan_doc(extract_scan_summary(_legacy_result(honeypot=False), source="scan_v1"))
        assert "honeypot=false" in doc

    def test_zero_values_rendered_safely(self) -> None:
        doc = build_scan_doc(
            extract_scan_summary(
                {"address": "0xAB", "chain": "solana", "name": "Lonely", "scanned_at": "2026-08-08T00:00:00+00:00"},
                source="scan_v1",
            )
        )
        assert "risk_score=0/100" in doc
        assert "liquidity=$0" in doc
        assert "holders=0" in doc


# ── Point IDs ──────────────────────────────────────────────────────────


class TestScanPointId:
    def test_same_day_same_id(self) -> None:
        a = scan_point_id("ethereum", "0xABC", "2026-08-08T10:00:00+00:00")
        b = scan_point_id("ethereum", "0xABC", "2026-08-08T23:59:59+00:00")
        assert a == b  # re-scan on the same day overwrites the same point

    def test_different_day_different_id(self) -> None:
        a = scan_point_id("ethereum", "0xABC", "2026-08-08T10:00:00+00:00")
        c = scan_point_id("ethereum", "0xABC", "2026-08-09T00:00:00+00:00")
        assert a != c

    def test_different_chain_or_address_different_id(self) -> None:
        base = scan_point_id("ethereum", "0xABC", "2026-08-08T00:00:00+00:00")
        assert base != scan_point_id("bsc", "0xABC", "2026-08-08T00:00:00+00:00")
        assert base != scan_point_id("ethereum", "0xDEF", "2026-08-08T00:00:00+00:00")


# ── Live indexing ──────────────────────────────────────────────────────


class TestIndexScanMemory:
    @pytest.fixture(autouse=True)
    def _reset_collection_flag(self) -> None:
        scan_memory._COLLECTION_ENSURED = False
        yield
        scan_memory._COLLECTION_ENSURED = False

    async def test_indexes_scan_into_scanned_tokens(self) -> None:
        client = FakeQdrant()
        vector = [0.1] * VECTOR_SIZE
        with patch("app.rag.scan_memory.get_embedding", new=AsyncMock(return_value=vector)):
            result = await index_scan_memory(_legacy_result(), source="scan_v1", client=client)

        assert result["indexed"] is True
        assert result["collection"] == COLLECTION
        assert result["point_id"] == scan_point_id("ethereum", "0xTokenAddress1234567890", "2026-08-08T10:00:00+00:00")
        assert "Token TestCoin (0xTokenAddress1234567890)" in result["doc"]

        # Collection auto-created on first index
        assert COLLECTION in client.created

        # Upsert payload carries content + metadata + top-level tags
        put_requests = [r for r in client.requests if r[0] == "PUT" and r[1].endswith("/points")]
        assert len(put_requests) == 1
        _, _, body = put_requests[0]
        assert body is not None
        point = body["points"][0]
        assert point["id"] == result["point_id"]
        assert point["vector"] == vector
        assert point["payload"]["content"].startswith("Token TestCoin (0xTokenAddress1234567890)")
        meta = point["payload"]["metadata"]
        assert meta["name"] == "TestCoin"
        assert meta["chain"] == "ethereum"
        assert meta["risk_score"] == 42
        assert meta["honeypot"] is True
        assert meta["liquidity"] == 80000.0
        assert meta["holders"] == 1234
        assert meta["source"] == "scan_v1"
        assert point["payload"]["source"] == "scan:scan_v1"
        assert point["payload"]["chain"] == "ethereum"
        assert point["payload"]["collection"] == COLLECTION

    async def test_v2_scan_upsert_shape(self) -> None:
        client = FakeQdrant()
        vector = [0.1] * VECTOR_SIZE
        with patch("app.rag.scan_memory.get_embedding", new=AsyncMock(return_value=vector)):
            result = await index_scan_memory(_v2_result(), source="scan_v2", client=client)

        assert result["indexed"] is True
        put_requests = [r for r in client.requests if r[0] == "PUT" and r[1].endswith("/points")]
        _, _, body = put_requests[0]
        meta = body["points"][0]["payload"]["metadata"]
        assert meta["risk_score"] == 65.0
        assert meta["honeypot"] is True
        assert body["points"][0]["payload"]["source"] == "scan:scan_v2"

    async def test_same_day_rescan_is_idempotent_point_id(self) -> None:
        client = FakeQdrant()
        vector = [0.1] * VECTOR_SIZE
        with patch("app.rag.scan_memory.get_embedding", new=AsyncMock(return_value=vector)):
            await index_scan_memory(_legacy_result(), source="scan_v1", client=client)
            await index_scan_memory(
                _legacy_result(scanned_at="2026-08-08T23:59:59+00:00"), source="scan_v1", client=client
            )

        put_requests = [r for r in client.requests if r[0] == "PUT" and r[1].endswith("/points")]
        assert len(put_requests) == 2
        assert put_requests[0][2]["points"][0]["id"] == put_requests[1][2]["points"][0]["id"]

    async def test_fails_open_when_qdrant_down(self) -> None:
        result = await index_scan_memory(
            _legacy_result(),
            source="scan_v1",
            client=FakeQdrantDown(),
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is False
        assert "error" in result

    async def test_fails_open_when_embedding_down(self) -> None:
        client = FakeQdrant()
        with patch(
            "app.rag.scan_memory.get_embedding",
            new=AsyncMock(side_effect=RuntimeError("embedder down")),
        ):
            result = await index_scan_memory(_legacy_result(), source="scan_v1", client=client)
        assert result["indexed"] is False

    async def test_fails_open_missing_address(self) -> None:
        result = await index_scan_memory({"name": "NoAddr"}, source="scan_v1", client=FakeQdrant())
        assert result["indexed"] is False

    async def test_rejects_dim_mismatch(self) -> None:
        result = await index_scan_memory(
            _legacy_result(),
            source="scan_v1",
            client=FakeQdrant(),
            vector=[0.1] * 768,  # wrong dim — must not be upserted
        )
        assert result["indexed"] is False
        assert "dim" in result["error"]


# ── ensure_collection ──────────────────────────────────────────────────


class TestEnsureCollection:
    @pytest.fixture(autouse=True)
    def _reset_flag(self) -> None:
        scan_memory._COLLECTION_ENSURED = False
        yield
        scan_memory._COLLECTION_ENSURED = False

    async def test_creates_collection_when_missing(self) -> None:
        client = FakeQdrant()
        assert await ensure_collection(client) is True
        assert COLLECTION in client.created
        create_req = next(r for r in client.requests if r[0] == "PUT" and r[1] == f"/collections/{COLLECTION}")
        assert create_req[2]["vectors"] == {"size": VECTOR_SIZE, "distance": DISTANCE}
        assert VECTOR_SIZE == 1024  # bge-m3 primary embedder

    async def test_does_not_recreate(self) -> None:
        client = FakeQdrant()
        client.created.append(COLLECTION)  # already exists
        assert await ensure_collection(client) is True
        assert not any(r[1] == f"/collections/{COLLECTION}" and r[0] == "PUT" for r in client.requests)

    async def test_fails_open_on_unreachable(self) -> None:
        assert await ensure_collection(FakeQdrantDown()) is False


# ── scanner.py hooks ───────────────────────────────────────────────────


class FakeScanClient:
    """Minimal httpx.AsyncClient stand-in for scan_token's HTTP calls."""

    def __init__(self, pairs: list[dict] | None = None) -> None:
        self._pairs = pairs or []

    async def __aenter__(self) -> FakeScanClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
        if "dexscreener.com/tokens/" in url or "/tokens/" in url:
            return FakeResponse(200, {"pairs": self._pairs})
        if "honeypot" in url.lower():
            return FakeResponse(200, {"isHoneypot": False})
        return FakeResponse(200, {"result": {}})

    async def post(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse(200, {"status": "ok"})


class FakeSentinelClient:
    """Stand-in returning a successful SENTINEL report."""

    async def __aenter__(self) -> FakeSentinelClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "token_address": "0xSentinelHook123456789",
                "chain": "bsc",
                "composite_risk_score": 72,
                "risk_level": "high",
                "red_flags": ["LP unlocked"],
                "holder_analysis": {"total_holders": 500},
            },
        )


class TestScannerHooks:
    async def test_scan_token_schedules_background_index(self) -> None:
        pairs = [
            {
                "baseToken": {"name": "HookCoin", "symbol": "HOOK"},
                "priceUsd": "1.0",
                "marketCap": 1_000_000,
                "fdv": 1_000_000,
                "volume": {"h24": 50_000},
                "liquidity": {"usd": 80_000},
                "priceChange": {},
                "pairAddress": "0xpair",
                "dexId": "uniswap",
                "chainId": "ethereum",
                "pairCreatedAt": 1_700_000_000,
                "info": {},
            }
        ]
        with (
            patch(
                "app.domains.telegram.rugmunchbot.tools.scanner.httpx.AsyncClient",
                return_value=FakeScanClient(pairs),
            ),
            patch("app.domains.telegram.rugmunchbot.tools.scanner.schedule_scan_memory_index") as mock_sched,
        ):
            result = await bot_scanner.scan_token("0xTokenAddress1234567890", "ethereum")

        assert result["name"] == "HookCoin"
        mock_sched.assert_called_once_with(result, source="scan_v1")

    async def test_scan_token_skips_index_when_not_found(self) -> None:
        with (
            patch(
                "app.domains.telegram.rugmunchbot.tools.scanner.httpx.AsyncClient",
                return_value=FakeScanClient(pairs=[]),
            ),
            patch("app.domains.telegram.rugmunchbot.tools.scanner.schedule_scan_memory_index") as mock_sched,
        ):
            result = await bot_scanner.scan_token("0xTokenAddress1234567890", "ethereum")

        assert result["name"] == "Unknown"
        mock_sched.assert_not_called()

    async def test_scan_token_v2_schedules_background_index(self) -> None:
        # scan_token_v2 posts through the canonical engine's shared client
        # (tools.scanner.backend_json_post), so the v2 HTTP call is mocked at
        # that seam rather than at httpx.AsyncClient.
        payload = {
            "status": "ok",
            "token": "0xV2HookToken123456789",
            "chain": "ethereum",
            "name": "HookCoin",
            "safety_score": 80.0,
            "evidence": {"holders": {"holder_count": 100}},
        }
        with (
            patch(
                "app.domains.telegram.rugmunchbot.tools.scanner.backend_json_post",
                new=AsyncMock(return_value=payload),
            ),
            patch("app.domains.telegram.rugmunchbot.tools.scanner.schedule_scan_memory_index") as mock_sched,
        ):
            data = await bot_scanner.scan_token_v2("0xV2HookToken123456789", "ethereum")

        assert data is not None
        mock_sched.assert_called_once_with(data, source="scan_v2")

    async def test_scan_token_v2_non_ok_status_skips_index(self) -> None:
        with (
            patch(
                "app.domains.telegram.rugmunchbot.tools.scanner.backend_json_post",
                new=AsyncMock(return_value={"status": "error"}),
            ),
            patch("app.domains.telegram.rugmunchbot.tools.scanner.schedule_scan_memory_index") as mock_sched,
        ):
            data = await bot_scanner.scan_token_v2("0xV2HookToken123456789", "ethereum")

        assert data is None
        mock_sched.assert_not_called()

    async def test_sentinel_scan_schedules_background_index(self) -> None:
        with (
            patch(
                "app.domains.telegram.rugmunchbot.tools.scanner.httpx.AsyncClient",
                return_value=FakeSentinelClient(),
            ),
            patch("app.domains.telegram.rugmunchbot.tools.scanner.schedule_scan_memory_index") as mock_sched,
        ):
            data = await bot_scanner.sentinel_scan("0xSentinelHook123456789", "bsc")

        assert data is not None
        assert data["_source"] == "sentinel"
        mock_sched.assert_called_once_with(data, source="sentinel")


# ── schedule helper ────────────────────────────────────────────────────


class TestScheduleScanMemoryIndex:
    async def test_schedules_and_completes_background_task(self) -> None:
        with patch(
            "app.rag.scan_memory.index_scan_memory",
            new=AsyncMock(return_value={"indexed": True}),
        ) as mock_index:
            task = scan_memory.schedule_scan_memory_index(_legacy_result(), source="scan_v1")
            assert task is not None
            await task

        mock_index.assert_awaited_once()
        assert mock_index.await_args.args[0]["name"] == "TestCoin"
        assert mock_index.await_args.kwargs["source"] == "scan_v1"

    async def test_returns_none_without_event_loop(self) -> None:
        with patch("app.rag.scan_memory.asyncio.create_task", side_effect=RuntimeError("no running event loop")):
            assert scan_memory.schedule_scan_memory_index(_legacy_result(), source="scan_v1") is None
