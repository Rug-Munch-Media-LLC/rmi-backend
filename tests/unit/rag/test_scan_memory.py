"""Unit tests for app/rag/scan_memory.py — scan-to-RAG memory indexer.

Covers the pure summary extraction (legacy v1 / v2 / sentinel shapes),
index gating, doc builder, deterministic point IDs, the Qdrant
collection-ensure + upsert flow (httpx mocked), and the fail-open
contract (dimension mismatch, missing address, Qdrant errors). No
network, no real Qdrant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.rag.scan_memory import (
    COLLECTION,
    VECTOR_SIZE,
    _short_label,
    build_scan_doc,
    ensure_collection,
    extract_scan_summary,
    index_scan_memory,
    scan_point_id,
    schedule_scan_memory_index,
    should_index_scan,
)


class TestShortLabel:
    def test_short_address_unchanged(self) -> None:
        assert _short_label("0xabc") == "0xabc"

    def test_long_address_compact(self) -> None:
        label = _short_label("0x1234567890abcdef1234567890abcdef12345678")
        assert label.startswith("0x1234")
        assert label.endswith("5678")
        assert "…" in label

    def test_none_handled(self) -> None:
        assert _short_label(None) == ""


class TestExtractScanSummary:
    def test_scan_v1_legacy_shape(self) -> None:
        out = extract_scan_summary(
            {
                "address": "0xabc",
                "chain": "ethereum",
                "risk_score": 85,
                "risk_level": "high",
                "honeypot": True,
                "holders": 120,
                "liquidity": 50000.0,
                "name": "Test Token",
                "symbol": "TT",
                "threats": ["honeypot", "rug"],
                "scanned_at": "2026-01-01T00:00:00+00:00",
            },
            source="scan_v1",
        )
        assert out["name"] == "Test Token"
        assert out["risk_score"] == 85
        assert out["honeypot"] is True
        assert out["holders"] == 120
        assert out["liquidity"] == 50000.0
        assert out["threats"] == ["honeypot", "rug"]
        assert out["source"] == "scan_v1"

    def test_scan_v2_safety_score_inverted(self) -> None:
        out = extract_scan_summary(
            {
                "token_address": "0xabc",
                "chain": "solana",
                "safety_score": 30,
                "risk_flags": ["honeypot"],
                "name": "S Token",
                "symbol": "ST",
                "liquidity_usd": 1000,
                "evidence": {"holders": {"holder_count": 50}},
            },
            source="scan_v2",
        )
        # safety 30 → risk 70 (100 - safety, clamped 0..100)
        assert out["risk_score"] == 70.0
        assert out["honeypot"] is True
        assert out["holders"] == 50
        assert out["liquidity"] == 1000.0

    def test_scan_v2_safety_clamped(self) -> None:
        out = extract_scan_summary({"safety_score": 150}, source="scan_v2")
        assert out["risk_score"] == 0.0
        out = extract_scan_summary({"safety_score": -5}, source="scan_v2")
        assert out["risk_score"] == 100.0

    def test_sentinel_shape(self) -> None:
        out = extract_scan_summary(
            {
                "token_address": "0xabc",
                "chain": "bsc",
                "composite_risk_score": 92,
                "risk_level": "critical",
                "red_flags": ["honeypot", "lp_removed"],
                "holder_analysis": {"total_holders": 500},
                "liquidity_usd": 2000.0,
                "name": "",
            },
            source="sentinel",
        )
        assert out["risk_score"] == 92
        assert out["holders"] == 500
        assert out["honeypot"] is True
        # empty name → short label of address
        assert out["name"] == _short_label("0xabc")

    def test_empty_result_neutral_defaults(self) -> None:
        out = extract_scan_summary(None, source="scan_v1")
        assert out["risk_score"] == 0.0
        assert out["honeypot"] is None
        assert out["chain"] == "unknown"
        assert out["address"] == ""


class TestShouldIndexScan:
    def test_named_token_indexed(self) -> None:
        assert should_index_scan({"name": "Real Token"}, source="scan_v1") is True

    def test_unknown_name_not_indexed(self) -> None:
        assert should_index_scan({"name": "Unknown"}, source="scan_v1") is False

    def test_empty_result_not_indexed(self) -> None:
        assert should_index_scan({}, source="scan_v1") is False

    def test_sentinel_always_indexed_when_complete(self) -> None:
        assert should_index_scan({"composite_risk_score": 10}, source="sentinel") is True
        assert should_index_scan({"token_address": "0xabc"}, source="sentinel") is True
        assert should_index_scan({}, source="sentinel") is False


class TestBuildScanDoc:
    def test_doc_format(self) -> None:
        summary = {
            "name": "TT",
            "address": "0xabc",
            "chain": "ethereum",
            "risk_score": 85,
            "risk_max": 100,
            "honeypot": True,
            "liquidity": 50000,
            "holders": 1200,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        doc = build_scan_doc(summary)
        assert "Token TT (0xabc)" in doc
        assert "risk_score=85/100" in doc
        assert "honeypot=true" in doc
        assert "liquidity=$50,000" in doc
        assert "holders=1,200" in doc

    def test_doc_falsy_defaults(self) -> None:
        doc = build_scan_doc({})
        assert "Unknown" in doc
        assert "honeypot=false" in doc


class TestScanPointId:
    def test_deterministic_and_date_scoped(self) -> None:
        pid1 = scan_point_id("ethereum", "0xabc", "2026-01-01T00:00:00")
        pid2 = scan_point_id("ethereum", "0xabc", "2026-01-01T12:00:00")
        pid3 = scan_point_id("ethereum", "0xabc", "2026-01-02T00:00:00")
        assert pid1 == pid2  # same day → same point (bounded memory)
        assert pid1 != pid3  # different day → new point

    def test_chain_and_address_scoped(self) -> None:
        a = scan_point_id("ethereum", "0xabc", "2026-01-01")
        b = scan_point_id("solana", "0xabc", "2026-01-01")
        assert a != b


class TestEnsureCollection:
    @pytest.mark.asyncio
    async def test_already_ensured_short_circuits(self, monkeypatch) -> None:
        import app.rag.scan_memory as sm

        monkeypatch.setattr(sm, "_COLLECTION_ENSURED", True)
        client = Mock()
        assert await ensure_collection(client) is True
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_when_404(self, monkeypatch) -> None:
        import app.rag.scan_memory as sm

        monkeypatch.setattr(sm, "_COLLECTION_ENSURED", False)
        client = Mock()
        probe = Mock(status_code=404)
        created = Mock(status_code=200, text="")
        client.get = AsyncMock(return_value=probe)
        client.put = AsyncMock(return_value=created)
        assert await ensure_collection(client) is True
        create_call = client.put.call_args
        assert create_call.args[0] == f"/collections/{COLLECTION}"
        body = create_call.kwargs["json"]
        assert body["vectors"]["distance"] == "Cosine"

    @pytest.mark.asyncio
    async def test_probe_error_returns_false(self, monkeypatch) -> None:
        import app.rag.scan_memory as sm

        monkeypatch.setattr(sm, "_COLLECTION_ENSURED", False)
        client = Mock()
        client.get = AsyncMock(return_value=Mock(status_code=500, text="boom"))
        assert await ensure_collection(client) is False

    @pytest.mark.asyncio
    async def test_http_error_returns_false(self, monkeypatch) -> None:
        import httpx

        import app.rag.scan_memory as sm

        monkeypatch.setattr(sm, "_COLLECTION_ENSURED", False)
        client = Mock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("down"))
        assert await ensure_collection(client) is False


class TestIndexScanMemory:
    @pytest.mark.asyncio
    async def test_missing_address_fails_open(self) -> None:
        result = await index_scan_memory({"name": "NoAddr"}, source="scan_v1")
        assert result["indexed"] is False
        assert "address" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_dimension_mismatch_fails_open(self) -> None:
        result = await index_scan_memory(
            {"address": "0xabc", "name": "TT", "chain": "ethereum"},
            source="scan_v1",
            vector=[0.0] * (VECTOR_SIZE + 1),
        )
        assert result["indexed"] is False
        assert "dim" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_successful_upsert(self) -> None:
        import app.rag.scan_memory as sm

        client = Mock()
        client.get = AsyncMock(return_value=Mock(status_code=404, text=""))
        client.put = AsyncMock(side_effect=[Mock(status_code=200, text=""), Mock(status_code=200, text="")])
        result = await index_scan_memory(
            {
                "address": "0xabc",
                "name": "TT",
                "chain": "ethereum",
                "risk_score": 10,
                "scanned_at": "2026-01-01T00:00:00+00:00",
            },
            source="scan_v1",
            client=client,
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is True
        assert result["collection"] == COLLECTION
        # upsert path hit /collections/scanned_tokens/points
        upsert = [c for c in client.put.call_args_list if c.args[0] == f"/collections/{COLLECTION}/points"]
        assert upsert

    @pytest.mark.asyncio
    async def test_qdrant_upsert_failure_fails_open(self) -> None:
        import app.rag.scan_memory as sm

        client = Mock()
        client.get = AsyncMock(return_value=Mock(status_code=200, text=""))
        client.put = AsyncMock(return_value=Mock(status_code=500, text="err"))
        result = await index_scan_memory(
            {"address": "0xabc", "name": "TT", "chain": "ethereum"},
            source="scan_v1",
            client=client,
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is False
        assert "qdrant status" in result.get("error", "")


class TestScheduleScanMemoryIndex:
    def test_returns_task_in_running_loop(self) -> None:
        import asyncio

        async def _run() -> None:
            task = schedule_scan_memory_index({"address": "0xabc", "name": "TT"})
            assert task is not None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_run())

    def test_returns_none_without_loop(self) -> None:
        # No running event loop in a sync test context
        with patch("asyncio.create_task", side_effect=RuntimeError("no loop")):
            assert schedule_scan_memory_index({"address": "0xabc"}) is None
