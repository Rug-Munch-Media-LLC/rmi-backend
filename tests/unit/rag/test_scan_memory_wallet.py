"""Unit tests for app/rag/scan_memory.py — wallet trust memory feed.

Covers the wallet variant of the scan-to-RAG memory: summary extraction
from a ``/wallet`` scan result (+ nested ``trust`` block), the index
gate, doc builder, deterministic per-day point IDs, the Qdrant upsert
flow (httpx mocked) against the ``scanned_wallets`` collection, and the
fail-open contract. No network, no real Qdrant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.rag.scan_memory import (
    VECTOR_SIZE,
    WALLET_COLLECTION,
    build_wallet_doc,
    extract_wallet_summary,
    index_wallet_memory,
    schedule_wallet_memory_index,
    should_index_wallet,
    wallet_point_id,
)


def _wallet_result(trust_score: int | None = 78) -> dict:
    return {
        "address": "0xwally",
        "chain": "ethereum",
        "entity_name": "",
        "entity_type": "unknown",
        "risk_score": 12,
        "scanned_at": "2026-08-16T12:00:00+00:00",
        "trust": {
            "result": "scanned",
            "trust_score": trust_score,
            "trust_level": "high",
            "trust_line": "\U0001f7e2 Trust 78/100 \u2014 aged, CEX-funded, no prior rugs",
            "signals": ["aged", "CEX-funded", "no prior rugs"],
            "factors": {"funding_source": "cex", "rug_count": 0, "age_days": 812},
        },
    }


class TestExtractWalletSummary:
    def test_extracts_trust_and_identity(self) -> None:
        out = extract_wallet_summary(_wallet_result())
        assert out["address"] == "0xwally"
        assert out["chain"] == "ethereum"
        assert out["trust_score"] == 78
        assert out["trust_level"] == "high"
        assert "Trust 78/100" in out["verdict"]
        assert out["signals"] == ["aged", "CEX-funded", "no prior rugs"]
        assert out["funding_source"] == "cex"
        assert out["rug_count"] == 0
        assert out["risk_score"] == 12
        assert out["source"] == "wallet_scan"

    def test_missing_trust_defaults_neutral(self) -> None:
        out = extract_wallet_summary({"address": "0xwally", "chain": "solana"})
        assert out["trust_score"] is None
        assert out["trust_level"] == ""
        assert out["verdict"] == ""
        assert out["funding_source"] == ""
        assert out["rug_count"] is None

    def test_wallet_key_fallback(self) -> None:
        out = extract_wallet_summary({"wallet": "0xwally", "chain": "bsc"})
        assert out["address"] == "0xwally"

    def test_empty_result(self) -> None:
        out = extract_wallet_summary(None)
        assert out["address"] == ""
        assert out["chain"] == "unknown"


class TestShouldIndexWallet:
    def test_indexed_when_trust_present(self) -> None:
        assert should_index_wallet(_wallet_result()) is True

    def test_indexed_when_risk_present(self) -> None:
        assert should_index_wallet({"address": "0xw", "risk_score": 50}) is True

    def test_not_indexed_without_address(self) -> None:
        assert should_index_wallet({"risk_score": 50}) is False

    def test_not_indexed_without_trust_or_risk(self) -> None:
        assert should_index_wallet({"address": "0xw"}) is False


class TestBuildWalletDoc:
    def test_doc_format(self) -> None:
        doc = build_wallet_doc(extract_wallet_summary(_wallet_result()))
        assert "Wallet 0xwally" in doc
        assert "trust_score=78/100" in doc
        assert "trust_level=high" in doc
        assert "Trust 78/100" in doc
        assert "funding=cex" in doc
        assert "prior_rugs=0" in doc

    def test_doc_unknown_defaults(self) -> None:
        doc = build_wallet_doc({})
        assert "trust_score=n/a" in doc
        assert "prior_rugs=n/a" in doc


class TestWalletPointId:
    def test_deterministic_and_date_scoped(self) -> None:
        pid1 = wallet_point_id("ethereum", "0xwally", "2026-08-16T00:00:00")
        pid2 = wallet_point_id("ethereum", "0xwally", "2026-08-16T23:59:59")
        pid3 = wallet_point_id("ethereum", "0xwally", "2026-08-17T00:00:00")
        assert pid1 == pid2  # same day → same point (bounded memory)
        assert pid1 != pid3  # different day → accumulates history

    def test_chain_scoped(self) -> None:
        a = wallet_point_id("ethereum", "0xwally", "2026-08-16")
        b = wallet_point_id("solana", "0xwally", "2026-08-16")
        assert a != b


class TestIndexWalletMemory:
    @pytest.mark.asyncio
    async def test_missing_address_fails_open(self) -> None:
        result = await index_wallet_memory({"trust": {"trust_score": 50}})
        assert result["indexed"] is False
        assert "address" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_no_trust_or_risk_fails_open(self) -> None:
        result = await index_wallet_memory({"address": "0xwally"})
        assert result["indexed"] is False
        assert "no trust/risk" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_dimension_mismatch_fails_open(self) -> None:
        result = await index_wallet_memory(
            _wallet_result(),
            vector=[0.0] * (VECTOR_SIZE + 1),
        )
        assert result["indexed"] is False
        assert "dim" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_successful_upsert_to_wallet_collection(self) -> None:
        client = Mock()
        client.get = AsyncMock(return_value=Mock(status_code=404, text=""))
        client.put = AsyncMock(side_effect=[Mock(status_code=200, text=""), Mock(status_code=200, text="")])
        result = await index_wallet_memory(
            _wallet_result(),
            client=client,
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is True
        assert result["collection"] == WALLET_COLLECTION
        upsert = [c for c in client.put.call_args_list if c.args[0] == f"/collections/{WALLET_COLLECTION}/points"]
        assert upsert
        payload = upsert[0].kwargs["json"]["points"][0]["payload"]
        assert payload["metadata"]["trust_score"] == 78

    @pytest.mark.asyncio
    async def test_qdrant_upsert_failure_fails_open(self) -> None:
        client = Mock()
        client.get = AsyncMock(return_value=Mock(status_code=200, text=""))
        client.put = AsyncMock(return_value=Mock(status_code=500, text="err"))
        result = await index_wallet_memory(
            _wallet_result(),
            client=client,
            vector=[0.1] * VECTOR_SIZE,
        )
        assert result["indexed"] is False
        assert "qdrant status" in result.get("error", "")


class TestScheduleWalletMemoryIndex:
    def test_returns_task_in_running_loop(self) -> None:
        import asyncio

        async def _run() -> None:
            task = schedule_wallet_memory_index(_wallet_result())
            assert task is not None
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(_run())

    def test_returns_none_without_loop(self) -> None:
        with patch("asyncio.create_task", side_effect=RuntimeError("no loop")):
            assert schedule_wallet_memory_index(_wallet_result()) is None
