"""Tests for attack_detector — C6 time-based attack detection engine.

Covers:
  - Sandwich detector (same-block, same-pair, same-sender triads)
  - Pump & dump detector (4h volume spike, price spike, drawdown)
  - Flash loan detector (same-tx multi-swap repay pattern)
  - Orchestrator wiring: ClickHouse insert, Redis alerts, Telegram, whale mark
  - DataBus LOCAL provider registration
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.attack_detector import (
    ALL_CHAINS,
    clear_cache,
    detect_flash_loans,
    detect_pump_dumps,
    detect_sandwiches,
    get_cache_stats,
    run_attack_detection,
)

# ── Fake ClickHouse client ────────────────────────────────────────────────


class FakeCHClient:
    """Sync ClickHouse client stand-in that dispatches on SQL fragments."""

    def __init__(
        self,
        sandwich_rows: list[tuple] | None = None,
        pair_rows: list[tuple] | None = None,
        volume_rows: list[tuple] | None = None,
        price_rows: list[tuple] | None = None,
        flash_rows: list[tuple] | None = None,
        insert_count: int = 1,
    ) -> None:
        self.sandwich_rows = sandwich_rows or []
        self.pair_rows = pair_rows or []
        self.volume_rows = volume_rows or []
        self.price_rows = price_rows or []
        self.flash_rows = flash_rows or []
        self.insert_count = insert_count
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        sql_norm = sql.strip()
        if sql_norm.startswith("INSERT INTO"):
            return self.insert_count
        if sql_norm.startswith("ALTER TABLE"):
            return 1
        if "SELECT 1" in sql:
            return [(1,)]
        if sql_norm.startswith("CREATE TABLE"):
            return []
        if "JOIN wallet_memory.dex_events b" in sql:
            return self.sandwich_rows
        if "sumIf" in sql and "INTERVAL 4 HOUR" in sql:
            return self.volume_rows
        if "argMax" in sql and "GROUP BY block_number, block_timestamp" in sql:
            return self.price_rows
        if "GROUP BY contract_address, token0, token1" in sql:
            return self.pair_rows
        if "HAVING count() >= 4" in sql:
            return self.flash_rows
        return []


class FakeRedis:
    """Async Redis stand-in with SET NX + expire semantics."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def ping(self) -> bool:
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def rpush(self, key: str, value: str) -> int:
        self.data[key] = self.data.get(key, "") + value
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        return True


# ── Fixtures / patchers ───────────────────────────────────────────────────


def _mock_ch(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.attack_detector._get_ch_client", return_value=fake)


def _mock_redis(fake: FakeRedis):
    return patch("app.domains.scanner.engines.attack_detector._get_redis", AsyncMock(return_value=fake))


def _mock_usd_price(price_usd: float = 1.0):
    return patch(
        "app.domains.scanner.engines.attack_detector._estimate_usd",
        AsyncMock(return_value=100.0),
    )


# ── Sandwich detector ─────────────────────────────────────────────────────


class TestSandwichDetector:
    async def test_detects_balanced_sandwich_high_confidence(self):
        """Balanced buy/sell legs with sell >= buy → HIGH confidence."""
        rows = [
            # attacker, block, buy_tx, victim_tx, sell_tx, buy, sell, pair, token0, token1, ts, logidx
            (
                "0xattacker",
                1000,
                "0xaaa",
                "0xbbb",
                "0xccc",
                1000,
                1100,
                "0xpair",
                "0xtok",
                "0xweth",
                "2026-08-02 10:00:00",
                3,
            ),
        ]
        with _mock_ch(FakeCHClient(sandwich_rows=rows)), _mock_usd_price():
            events = await detect_sandwiches("ethereum", hours_back=1, force_fresh=True)
        assert len(events) == 1
        ev = events[0]
        assert ev["attack_type"] == "sandwich"
        assert ev["confidence"] == "HIGH"
        assert ev["chain"] == "ethereum"
        assert ev["block_number"] == 1000
        assert ev["tx_hashes"] == ["0xaaa", "0xbbb", "0xccc"]
        assert ev["attacker"] == "0xattacker"
        assert ev["pair_address"] == "0xpair"

    async def test_unbalanced_sandwich_low_confidence(self):
        """Unbalanced legs (sell >> buy) → LOW confidence."""
        rows = [
            (
                "0xattacker",
                2000,
                "0xaaa",
                "0xbbb",
                "0xccc",
                1000,
                9000,
                "0xpair",
                "0xtok",
                "0xweth",
                "2026-08-02 10:00:00",
                3,
            ),
        ]
        with _mock_ch(FakeCHClient(sandwich_rows=rows)), _mock_usd_price():
            events = await detect_sandwiches("ethereum", hours_back=1, force_fresh=True)
        assert len(events) == 1
        assert events[0]["confidence"] == "LOW"

    async def test_returns_empty_when_no_client(self):
        """No ClickHouse client → graceful empty result."""
        with patch("app.domains.scanner.engines.attack_detector._get_ch_client", return_value=None):
            events = await detect_sandwiches("ethereum", force_fresh=True)
        assert events == []

    async def test_skips_zero_amount_rows(self):
        """Rows with zero buy/sell amounts are skipped."""
        rows = [
            ("0xa", 3000, "0x1", "0x2", "0x3", 0, 1100, "0xpair", "0xtok", "0xweth", "2026-08-02 10:00:00", 3),
        ]
        with _mock_ch(FakeCHClient(sandwich_rows=rows)), _mock_usd_price():
            events = await detect_sandwiches("ethereum", force_fresh=True)
        assert events == []


# ── Pump & dump detector ──────────────────────────────────────────────────


class TestPumpDumpDetector:
    def _prices(self) -> list[tuple]:
        # baseline 1.0 → peak 10.0 (900% spike) → last 3.0 (70% drawdown)
        return [
            (100, "2026-08-02 09:00:00", 1.0),
            (101, "2026-08-02 09:01:00", 1.0),
            (102, "2026-08-02 09:02:00", 1.0),
            (103, "2026-08-02 09:03:00", 1.0),
            (104, "2026-08-02 09:04:00", 1.0),
            (105, "2026-08-02 09:05:00", 10.0),
            (106, "2026-08-02 09:06:00", 3.0),
        ]

    async def test_detects_completed_pump_dump_high_confidence(self):
        """Volume spike + price spike + drawdown → HIGH confidence."""
        fake = FakeCHClient(
            pair_rows=[("0xpair", "0xtok", "0xweth", 100, 100000.0)],
            volume_rows=[(11000.0, 12000.0)],  # baseline (12000-11000)/5=200 → 55x spike
            price_rows=self._prices(),
        )
        with _mock_ch(fake), _mock_usd_price():
            events = await detect_pump_dumps("ethereum", lookback_hours=24, force_fresh=True)
        assert len(events) == 1
        ev = events[0]
        assert ev["attack_type"] == "pump_dump"
        assert ev["confidence"] == "HIGH"
        assert ev["pair_address"] == "0xpair"
        assert ev["block_number"] == 105  # peak block
        assert any("vol spike" in e for e in ev["evidence"])

    async def test_volume_spike_without_price_spike_is_low(self):
        """Volume spike alone → LOW confidence."""
        fake = FakeCHClient(
            pair_rows=[("0xpair", "0xtok", "0xweth", 100, 100000.0)],
            volume_rows=[(11000.0, 12000.0)],
            price_rows=[  # flat prices → no price spike
                (100, "2026-08-02 09:00:00", 1.0),
                (101, "2026-08-02 09:01:00", 1.1),
                (102, "2026-08-02 09:02:00", 1.05),
            ],
        )
        with _mock_ch(fake), _mock_usd_price():
            events = await detect_pump_dumps("ethereum", lookback_hours=24, force_fresh=True)
        assert len(events) == 1
        assert events[0]["confidence"] == "LOW"

    async def test_no_volume_spike_returns_empty(self):
        """No 10x volume spike → no event."""
        fake = FakeCHClient(
            pair_rows=[("0xpair", "0xtok", "0xweth", 100, 100000.0)],
            volume_rows=[(5000.0, 12000.0)],  # baseline 1400 → 3.5x → below 10x
            price_rows=self._prices(),
        )
        with _mock_ch(fake), _mock_usd_price():
            events = await detect_pump_dumps("ethereum", lookback_hours=24, force_fresh=True)
        assert events == []

    async def test_returns_empty_when_no_client(self):
        with patch("app.domains.scanner.engines.attack_detector._get_ch_client", return_value=None):
            events = await detect_pump_dumps("ethereum", force_fresh=True)
        assert events == []


# ── Flash loan detector ───────────────────────────────────────────────────


class TestFlashLoanDetector:
    async def test_detects_same_tx_repay_high_confidence(self):
        """4 swaps in one tx with in ≈ out → HIGH confidence."""
        rows = [
            ("0xabc", "0xpair", "0xtok", "0xweth", 4, 1000000.0, 1050000.0, 555, "2026-08-02 10:00:00"),
            # repay diff 5% <= 10% → passes
        ]
        with _mock_ch(FakeCHClient(flash_rows=rows)), _mock_usd_price():
            events = await detect_flash_loans("ethereum", hours_back=1, force_fresh=True)
        assert len(events) == 1
        ev = events[0]
        assert ev["attack_type"] == "flash_loan"
        assert ev["confidence"] == "HIGH"
        assert ev["tx_hashes"] == ["0xabc"]
        assert ev["block_number"] == 555

    async def test_rejects_non_repay_pattern(self):
        """Tx where in != out on the pair (no repay) → skipped."""
        rows = [
            ("0xabc", "0xpair", "0xtok", "0xweth", 4, 1000000.0, 3000000.0, 555, "2026-08-02 10:00:00"),
            # repay diff 67% > 10% → rejected
        ]
        with _mock_ch(FakeCHClient(flash_rows=rows)), _mock_usd_price():
            events = await detect_flash_loans("ethereum", force_fresh=True)
        assert events == []

    async def test_returns_empty_when_no_client(self):
        with patch("app.domains.scanner.engines.attack_detector._get_ch_client", return_value=None):
            events = await detect_flash_loans("ethereum", force_fresh=True)
        assert events == []


# ── Orchestrator ──────────────────────────────────────────────────────────


class TestRunAttackDetection:
    @pytest.mark.asyncio
    async def test_full_pipeline_wires_alerts_and_persists(self):
        """New events are inserted, pushed to Redis, telegrammed, whale-marked."""
        sandwich_event = {
            "attack_type": "sandwich",
            "confidence": "HIGH",
            "chain": "ethereum",
            "block_number": 1000,
            "tx_hashes": ["0xaaa", "0xbbb", "0xccc"],
            "token_address": "0xtok",
            "pair_address": "0xpair",
            "value_usd": 100.0,
            "attacker": "0xattacker",
            "evidence": ["same block 1000"],
        }
        fake_ch = FakeCHClient()
        fake_redis = FakeRedis()

        with (
            _mock_ch(fake_ch),
            _mock_redis(fake_redis),
            patch(
                "app.domains.scanner.engines.attack_detector.detect_sandwiches",
                AsyncMock(return_value=[sandwich_event]),
            ),
            patch("app.domains.scanner.engines.attack_detector.detect_pump_dumps", AsyncMock(return_value=[])),
            patch("app.domains.scanner.engines.attack_detector.detect_flash_loans", AsyncMock(return_value=[])),
            patch(
                "app.domains.telegram.alert_hooks.send_scanner_alert",
                AsyncMock(return_value={"ok": True}),
            ) as mock_telegram,
        ):
            result = await run_attack_detection(chain="ethereum", hours_back=1, force_fresh=True)

        assert result["result"] == "scanned"
        assert result["events_new"] == 1
        assert result["inserted"] == 1
        assert result["alerts_pushed"] == 1
        assert result["telegram_sent"] == 1
        assert result["whale_marked"] == 1
        assert result["chains_scanned"] == ["ethereum"]
        # Whale ALTER executed against ClickHouse
        assert any("ALTER TABLE" in sql for sql in fake_ch.executed)
        # Attack alert key written to Redis
        assert any("attack_alert" in k for k in fake_redis.data)
        # HIGH confidence → Telegram fired
        mock_telegram.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_low_confidence_skips_telegram(self):
        """Only HIGH-confidence events reach Telegram."""
        low_event = {
            "attack_type": "flash_loan",
            "confidence": "LOW",
            "chain": "ethereum",
            "block_number": 2000,
            "tx_hashes": ["0xabc"],
            "token_address": "0xtok",
            "pair_address": "0xpair",
            "value_usd": None,
            "attacker": "",
            "evidence": ["2 swaps"],
        }
        with (
            _mock_ch(FakeCHClient()),
            _mock_redis(FakeRedis()),
            patch("app.domains.scanner.engines.attack_detector.detect_sandwiches", AsyncMock(return_value=[])),
            patch("app.domains.scanner.engines.attack_detector.detect_pump_dumps", AsyncMock(return_value=[])),
            patch(
                "app.domains.scanner.engines.attack_detector.detect_flash_loans", AsyncMock(return_value=[low_event])
            ),
            patch("app.domains.telegram.alert_hooks.send_scanner_alert", AsyncMock()) as mock_telegram,
        ):
            result = await run_attack_detection(chain="ethereum", force_fresh=True)

        assert result["telegram_sent"] == 0
        mock_telegram.assert_not_awaited()
        assert result["alerts_pushed"] == 1

    @pytest.mark.asyncio
    async def test_cached_window_skips_rescan(self):
        """A fresh Redis window cache short-circuits the run."""
        fake_redis = FakeRedis()
        fake_redis.data["attack_detect_window:ethereum:1"] = '{"hours_back": 1}'
        with (
            _mock_ch(FakeCHClient()),
            _mock_redis(fake_redis),
            patch("app.domains.scanner.engines.attack_detector.detect_sandwiches", AsyncMock()) as mock_sandwich,
            patch("app.domains.scanner.engines.attack_detector.detect_pump_dumps", AsyncMock()),
            patch("app.domains.scanner.engines.attack_detector.detect_flash_loans", AsyncMock()),
        ):
            result = await run_attack_detection(chain="ethereum", hours_back=1)

        assert result["cached"] is True
        mock_sandwich.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dedupes_events_already_seen(self):
        """Events seen within the Redis TTL are not re-inserted/altered."""
        event = {
            "attack_type": "sandwich",
            "confidence": "MEDIUM",
            "chain": "ethereum",
            "block_number": 1000,
            "tx_hashes": ["0xaaa", "0xbbb", "0xccc"],
            "token_address": "0xtok",
            "pair_address": "0xpair",
            "value_usd": None,
            "attacker": "0xattacker",
            "evidence": [],
        }
        fake_redis = FakeRedis()
        # Pre-seed the dedup key → the event is treated as already seen
        fake_redis.data["attack_seen:ethereum:sandwich:1000:0xpair"] = "1"
        with (
            _mock_ch(FakeCHClient()),
            _mock_redis(fake_redis),
            patch("app.domains.scanner.engines.attack_detector.detect_sandwiches", AsyncMock(return_value=[event])),
            patch("app.domains.scanner.engines.attack_detector.detect_pump_dumps", AsyncMock(return_value=[])),
            patch("app.domains.scanner.engines.attack_detector.detect_flash_loans", AsyncMock(return_value=[])),
        ):
            result = await run_attack_detection(chain="ethereum", force_fresh=True)

        assert result["events_new"] == 0
        assert result["inserted"] == 0
        assert result["alerts_pushed"] == 0


# ── Cache helpers ─────────────────────────────────────────────────────────


class TestCacheHelpers:
    def test_cache_stats_and_clear(self):
        """Cache stats return expected keys and clear empties the cache."""
        clear_cache()
        stats = get_cache_stats()
        assert stats["cache_size"] == 0
        assert stats["cache_max"] > 0
        assert stats["cache_ttl_seconds"] == 15 * 60
        assert stats["max_execution_time"] == 30

    def test_all_chains_includes_major_chains(self):
        """ALL_CHAINS covers the indexed EVM chains + solana."""
        for chain in ("ethereum", "bsc", "base", "solana"):
            assert chain in ALL_CHAINS


# ── DataBus provider ──────────────────────────────────────────────────────


class TestDataBusProvider:
    @pytest.mark.asyncio
    async def test_lite_chain_indexer_attack_detection_provider(self):
        """The LOCAL DataBus provider delegates and wraps engine output."""
        from app.domains.databus.providers.chains.lite_chain_indexer_own import (
            _lite_chain_indexer_attack_detection,
        )

        with (
            patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", True),
            patch(
                "app.domains.scanner.engines.attack_detector.run_attack_detection",
                AsyncMock(
                    return_value={
                        "result": "scanned",
                        "events_new": 1,
                        "inserted": 1,
                        "chains_scanned": ["ethereum"],
                    }
                ),
            ),
        ):
            out = await _lite_chain_indexer_attack_detection(chain="ethereum", hours_back=1)

        assert out is not None
        assert out["tier"] == "local"
        assert out["is_local"] is True
        assert out["data"]["result"] == "scanned"

    @pytest.mark.asyncio
    async def test_lite_chain_indexer_attack_detection_provider_skips_when_disabled(self):
        """Provider returns None when PREFER_OWN_DATA is disabled."""
        from app.domains.databus.providers.chains.lite_chain_indexer_own import (
            _lite_chain_indexer_attack_detection,
        )

        with (
            patch("app.domains.databus.providers.chains.lite_chain_indexer_own.PREFER_OWN_DATA", False),
            patch(
                "app.domains.scanner.engines.attack_detector.run_attack_detection",
                AsyncMock(return_value={"result": "scanned", "events_new": 0}),
            ),
        ):
            out = await _lite_chain_indexer_attack_detection(chain="ethereum")
        assert out is None

    def test_provider_registered_in_lazy_map(self):
        """The provider function is discoverable via the DataBus lazy map."""
        from app.domains.databus.providers import _lite_chain_indexer_attack_detection

        assert callable(_lite_chain_indexer_attack_detection)
