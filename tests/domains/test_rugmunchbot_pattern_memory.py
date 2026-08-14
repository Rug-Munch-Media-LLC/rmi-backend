"""Unit tests for the pattern_memory tool (``tools/pattern_memory.py``).

Covers:
  * the Redis-backed store: ``remember_pattern`` round-trips a record and
    ``pattern_recall`` returns ONLY stored patterns (zero fabrication);
  * the fuzzy numeric-signal matcher (relative-error similarity);
  * honest empty recall when nothing similar is stored;
  * fail-open: Redis down -> remember returns False, recall returns ok=False;
  * no-token / no-signal degradation;
  * signal derivation from a REAL DexScreener snapshot (mocked httpx).

No live Redis: ``get_redis_async`` is patched at the module boundary with an
in-memory fake (same pattern as ``test_rugmunchbot_scheduler_state.py``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.tools import _client, pattern_memory
from app.domains.telegram.rugmunchbot.tools.pattern_memory import (
    _clean_signals,
    _similarity,
    pattern_recall,
    remember_pattern,
)

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


# ── In-memory async Redis stand-in ────────────────────────────────────────


class FakeRedis:
    """Minimal async Redis fake for the store's command subset (hash only)."""

    def __init__(self) -> None:
        self._hash: dict[str, dict[str, str]] = {}

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
        **kwargs: str,
    ) -> int:
        merged = dict(mapping or {})
        if field is not None:
            merged[field] = value if value is not None else ""
        merged.update(kwargs)
        store = self._hash.setdefault(key, {})
        before = len(store)
        store.update(merged)
        return len(store) - before

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hash.get(key, {}))

    async def hlen(self, key: str) -> int:
        return len(self._hash.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        store = self._hash.get(key, {})
        removed = 0
        for f in fields:
            if f in store:
                del store[f]
                removed += 1
        return removed


# ── httpx boundary fake (for the DexScreener snapshot path) ───────────────


class _FakeAsyncClient:
    """httpx.AsyncClient fake — URL-substring -> canned JSON body."""

    _responses: ClassVar[dict[str, Any]] = {}
    _status: ClassVar[int] = 200
    _raise: ClassVar[Exception | None] = None
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "GET", "url": url, "params": params or {}, "headers": headers or {}})
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:
                if _FakeAsyncClient._raise is not None:
                    raise _FakeAsyncClient._raise

                class _Resp:
                    status_code = _FakeAsyncClient._status

                    def json(self, _body: Any = body) -> Any:
                        return _body

                return _Resp()

        class _Resp404:
            status_code = 404

            def json(self) -> Any:
                return {"error": "not found"}

        return _Resp404()


@pytest.fixture(autouse=True)
def isolate_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the Redis + httpx boundaries and reset the budget tracker so
    tests are order-independent."""
    from app.domains.telegram.rugmunchbot.tools.budget import budget as budget_tracker

    fake = FakeRedis()
    monkeypatch.setattr(pattern_memory, "get_redis_async", lambda: fake)
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient._raise = None
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    budget_tracker.reset()
    yield fake
    budget_tracker.reset()


# ── Signal cleaning + similarity (pure functions) ─────────────────────────


class TestSimilarity:
    def test_exact_match_is_one(self) -> None:
        assert _similarity({"change_24h": 200.0}, {"change_24h": 200.0}) == 1.0

    def test_close_values_high_score(self) -> None:
        score = _similarity({"change_24h": 200.0}, {"change_24h": 210.0})
        assert 0.9 <= score <= 1.0

    def test_opposite_directions_zero(self) -> None:
        # +200% vs -85% are different patterns, not similar.
        assert _similarity({"change_24h": 200.0}, {"change_24h": -85.0}) == 0.0

    def test_no_overlap_zero(self) -> None:
        assert _similarity({"change_24h": 5.0}, {"volume_24h": 1000.0}) == 0.0

    def test_partial_overlap_averaged(self) -> None:
        query = {"change_24h": 100.0, "volume_24h": 1000.0}
        stored = {"change_24h": 100.0, "liquidity_usd": 5.0}  # 1 overlap key
        assert 0.9 <= _similarity(query, stored) <= 1.0

    def test_clean_signals_drops_non_finite(self) -> None:
        assert _clean_signals({"a": 1.0, "b": None, "c": "x", "d": float("nan")}) == {"a": 1.0}


# ── remember_pattern: store round-trip ────────────────────────────────────


class TestRememberPattern:
    async def test_round_trip_stored_and_retrievable(self, isolate_boundaries: FakeRedis) -> None:
        ok = await remember_pattern(
            token=EVM,
            pattern="pump-and-dump",
            signals={"change_24h": 200.0, "change_48h": -85.0},
            outcome={"label": "rug", "note": "LP removed"},
        )
        assert ok is True
        raw = await isolate_boundaries.hgetall("bot:pattern_memory:patterns")
        assert raw, "expected a stored record"
        # Exactly one record, JSON-parses, carries the signals + outcome.
        import json

        (record,) = raw.values()
        rec = json.loads(record)
        assert rec["token"] == EVM.lower()
        assert rec["pattern"] == "pump-and-dump"
        assert rec["signals"]["change_24h"] == 200.0
        assert rec["outcome"]["label"] == "rug"
        assert rec["detected_at"]

    async def test_invalid_record_not_stored(self, isolate_boundaries: FakeRedis) -> None:
        assert await remember_pattern(token="", pattern="pump-and-dump") is False
        assert await remember_pattern(token=EVM, pattern="") is False
        raw = await isolate_boundaries.hgetall("bot:pattern_memory:patterns")
        assert not raw

    async def test_redis_down_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pattern_memory, "get_redis_async", lambda: None)
        assert await remember_pattern(token=EVM, pattern="honeypot") is False


# ── pattern_recall: zero-fabrication recall ───────────────────────────────


class TestPatternRecall:
    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_recall_returns_only_stored_patterns(self) -> None:
        await remember_pattern(
            token="0xPUMPS",
            pattern="pump-and-dump",
            signals={"change_24h": 200.0, "change_48h": -85.0},
            outcome={"label": "rug", "note": "LP removed after 6h"},
        )
        await remember_pattern(
            token="0xHONEY",
            pattern="honeypot",
            signals={"change_24h": 5.0, "volume_24h": 1_000_000.0, "sell_ratio": 0.01},
            outcome={"label": "honeypot", "note": "no sell possible"},
        )
        result = await pattern_recall(signals={"change_24h": 210.0, "change_48h": -80.0})
        assert result.ok
        data = result.data
        assert data["store_available"] is True
        assert data["stored_count"] == 2
        matches = data["matches"]
        assert matches, "expected the pump-and-dump pattern to match"
        # Zero fabrication: every match is a stored record, never invented.
        assert matches[0]["token"] == "0xpumps"
        assert matches[0]["pattern"] == "pump-and-dump"
        assert matches[0]["outcome"]["label"] == "rug"
        assert 0.5 <= matches[0]["similarity"] <= 1.0
        # The honeypot pattern (different signal profile) must NOT match.
        assert all(m["pattern"] != "honeypot" for m in matches)
        # Outcome distribution counts real stored outcomes only.
        assert data["outcome_distribution"] == {"rug": 1}

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_no_similar_pattern_returns_empty_matches(self) -> None:
        await remember_pattern(
            token="0xHONEY",
            pattern="honeypot",
            signals={"change_24h": 5.0, "volume_24h": 1_000_000.0},
            outcome={"label": "honeypot"},
        )
        result = await pattern_recall(signals={"change_24h": 300.0, "change_48h": -90.0})
        assert result.ok
        assert result.data["matches"] == []
        assert result.data["outcome_distribution"] == {}
        assert "No similar patterns in memory" in result.data["summary_text"]

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_empty_store_returns_no_matches(self) -> None:
        result = await pattern_recall(signals={"change_24h": 10.0})
        assert result.ok
        assert result.data["matches"] == []
        assert result.data["stored_count"] == 0

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_redis_down_ok_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pattern_memory, "get_redis_async", lambda: None)
        result = await pattern_recall(signals={"change_24h": 10.0})
        assert not result.ok
        assert "unavailable" in (result.error or "")

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_no_token_no_signals_ok_false(self) -> None:
        result = await pattern_recall()
        assert not result.ok
        assert "need a token" in (result.error or "")

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_symbol_derives_signals_from_real_snapshot(self) -> None:
        # Seed memory with a pattern, then ask about a DIFFERENT symbol whose
        # DexScreener snapshot closely matches a stored pattern.
        await remember_pattern(
            token="0xPUMPS",
            pattern="pump-and-dump",
            signals={"change_24h": 200.0, "volume_24h": 500_000.0},
            outcome={"label": "rug"},
        )
        _FakeAsyncClient._responses = {
            "api.dexscreener.com/latest/dex/search": {
                "pairs": [
                    {
                        "chainId": "solana",
                        "dexId": "raydium",
                        "pairAddress": "PAIR_PEPE",
                        "baseToken": {"address": SOL, "symbol": "PEPE", "name": "Pepe"},
                        "priceUsd": "0.5",
                        "priceChange": {"h24": 190.0, "h6": 80.0, "h1": 12.0},
                        "volume": {"h24": 480_000.0},
                        "liquidity": {"usd": 12_000.0},
                    }
                ]
            }
        }
        result = await pattern_recall(symbol="PEPE")
        assert result.ok
        assert result.data["matches"], "expected snapshot-derived recall to match"
        assert result.symbol_set == {"PEPE"}
        assert result.data["matches"][0]["pattern"] == "pump-and-dump"

    @pytest.mark.usefixtures("isolate_boundaries")
    async def test_address_snapshot_derivation(self) -> None:
        _FakeAsyncClient._responses = {
            f"api.dexscreener.com/latest/dex/tokens/{EVM}": {
                "pairs": [
                    {
                        "chainId": "ethereum",
                        "dexId": "uniswap-v2",
                        "pairAddress": "PAIR_1",
                        "baseToken": {"address": EVM, "symbol": "BONK", "name": "Bonk"},
                        "priceUsd": "0.00002",
                        "priceChange": {"h24": 5.0},
                        "volume": {"h24": 12345.0},
                        "liquidity": {"usd": 50_000.0},
                    }
                ]
            }
        }
        result = await pattern_recall(address=EVM)
        assert result.ok
        assert result.address_set == {EVM}
        # No stored patterns -> no matches, but store IS available.
        assert result.data["stored_count"] == 0
        assert result.data["matches"] == []
