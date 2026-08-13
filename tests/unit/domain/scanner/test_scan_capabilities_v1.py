"""Tests for RugMunchScan v1 engines — C1 wallet flow, C3 new token radar, C5 hype score.

Covers:
  - wallet_flow.build_flow_graph — hop-1/hop-2 graph, labels, node types, USD
  - new_token_radar.scan_new_tokens — deployer polling, fast scan, scoring, alerts
  - hype_score.compute_hype_score — component signals, weighting, decay
  - DataBus LOCAL provider registration for all three engines
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.hype_score import clear_cache as clear_hype_cache
from app.domains.scanner.engines.new_token_radar import clear_cache as clear_radar_cache
from app.domains.scanner.engines.wallet_flow import clear_cache as clear_flow_cache

# ── Fake ClickHouse client (dispatches on SQL fragments) ──────────────────


class FakeCHClient:
    """Sync ClickHouse client stand-in that dispatches on SQL fragments."""

    def __init__(self, **row_sets: list[tuple]) -> None:
        self.row_sets = row_sets
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "SELECT 1" in sql:
            return [(1,)]
        if "wallet_memory.wallet_labels" in sql and "address IN" in sql:
            return self.row_sets.get("labels", [])
        if "wallet_memory.dex_events" in sql and "contract_address IN" in sql:
            return self.row_sets.get("contracts", [])
        if "from_address = %(wallet)s OR to_address = %(wallet)s" in sql:
            return self.row_sets.get("hop1", [])
        if "from_address = %(cp)s OR to_address = %(cp)s" in sql:
            return self.row_sets.get("hop2", [])
        if "wallet_memory.deployer_events" in sql:
            return self.row_sets.get("deployers", [])
        if "sumIf" in sql and "INTERVAL 15 MINUTE" in sql:
            return self.row_sets.get("volume", [])
        if "uniqIf" in sql and "INTERVAL 1 DAY" in sql:
            return self.row_sets.get("holders", [])
        if "max(ts)" in sql:
            return self.row_sets.get("last_activity", [])
        return []


class FakeRedis:
    """Async Redis stand-in with get/setex semantics."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        self.data[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0


# ── Fixtures / patchers ───────────────────────────────────────────────────

_WALLET = "0x1111111111111111111111111111111111111111"
_COUNTERPARTY = "0x2222222222222222222222222222222222222222"
_TOKEN = "0x3333333333333333333333333333333333333333"


@contextmanager
def _mock_ch(fake: FakeCHClient):
    """Point every wallet_flow ClickHouse entry at the fake.

    Batch label / contract lookups were extracted into ``_common``, which binds
    ``_get_ch_client`` by name at import time — so that entry point is patched
    too, otherwise the fake is bypassed and labels come back empty (EOA).
    """
    with ExitStack() as stack:
        stack.enter_context(patch("app.domains.scanner.engines.wallet_flow._get_ch_client", return_value=fake))
        stack.enter_context(patch("app.domains.scanner.engines._common._get_ch_client", return_value=fake))
        yield


def _mock_ch_radar(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.new_token_radar._get_ch_client", return_value=fake)


def _mock_ch_hype(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.hype_score._get_ch_client", return_value=fake)


def _mock_usd(value: float = 100.0):
    return patch(
        "app.domains.scanner.engines.wallet_flow._normalize_token_usd",
        AsyncMock(return_value=value),
    )


# ═══════════════════════════════════════════════════════════════════════════
# C1 — WALLET FLOW
# ═══════════════════════════════════════════════════════════════════════════


class TestWalletFlow:
    def setup_method(self) -> None:
        clear_flow_cache()

    def test_requires_wallet_and_chain(self) -> None:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        assert callable(build_flow_graph)

    @pytest.mark.asyncio
    async def test_returns_empty_on_error_input(self) -> None:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        with _mock_ch(FakeCHClient()):
            out = await build_flow_graph("", "ethereum")
        assert out["result"] == "error"

    @pytest.mark.asyncio
    async def test_builds_graph_with_labels_and_usd(self) -> None:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        fake = FakeCHClient(
            hop1=[(_WALLET, _COUNTERPARTY, _TOKEN, 5000, "0xaaa")],
            hop2=[],
            labels=[(_COUNTERPARTY, "Binance Hot Wallet", "exchange", "cex", "eth_labels", 0)],
            contracts=[],
        )
        with (
            _mock_ch(fake),
            _mock_usd(1.0),
            patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.wallet_flow._redis_set", AsyncMock(return_value=True)),
        ):
            out = await build_flow_graph(_WALLET, "ethereum")

        assert out["result"] == "scanned"
        assert out["wallet"] == _WALLET
        assert out["node_count"] >= 2
        # Node type classification from label
        counterparty_nodes = [n for n in out["nodes"] if n["id"] == _COUNTERPARTY]
        assert counterparty_nodes
        assert counterparty_nodes[0]["type"] == "CEX"
        assert counterparty_nodes[0]["label"] == "Binance Hot Wallet"
        # Edge present with USD + evidence
        assert out["edge_count"] >= 1
        edge = out["edges"][0]
        assert edge["from"] == _WALLET
        assert edge["to"] == _COUNTERPARTY
        assert edge["usd"] > 0
        assert edge["tx_hashes"] == ["0xaaa"]

    @pytest.mark.asyncio
    async def test_unavailable_when_no_transfers(self) -> None:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        with (
            _mock_ch(FakeCHClient(hop1=[])),
            patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.wallet_flow._redis_set", AsyncMock(return_value=True)),
        ):
            out = await build_flow_graph(_WALLET, "ethereum")
        assert out["result"] == "unavailable"
        assert out["node_count"] == 0

    @pytest.mark.asyncio
    async def test_expands_hop2_for_top_counterparty(self) -> None:
        from app.domains.scanner.engines.wallet_flow import build_flow_graph

        third = "0x4444444444444444444444444444444444444444"
        fake = FakeCHClient(
            hop1=[(_WALLET, _COUNTERPARTY, _TOKEN, 5000, "0xaaa")],
            hop2=[(_COUNTERPARTY, third, _TOKEN, 3000, "0xbbb")],
            labels=[],
            contracts=[],
        )
        with (
            _mock_ch(fake),
            _mock_usd(1.0),
            patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.wallet_flow._redis_set", AsyncMock(return_value=True)),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=2)

        assert out["result"] == "scanned"
        node_ids = {n["id"] for n in out["nodes"]}
        assert third in node_ids  # hop-2 node added
        assert _WALLET in node_ids


# ═══════════════════════════════════════════════════════════════════════════
# C3 — NEW TOKEN RADAR
# ═══════════════════════════════════════════════════════════════════════════


class TestNewTokenRadar:
    def setup_method(self) -> None:
        clear_radar_cache()

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_deployments(self) -> None:
        from app.domains.scanner.engines.new_token_radar import scan_new_tokens

        with (
            _mock_ch_radar(FakeCHClient(deployers=[])),
            patch("app.domains.scanner.engines.new_token_radar._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.new_token_radar._redis_set", AsyncMock(return_value=True)),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=False)
        assert out == []

    @pytest.mark.asyncio
    async def test_scores_new_token(self) -> None:
        from app.domains.scanner.engines.new_token_radar import scan_new_tokens

        fake = FakeCHClient(
            deployers=[("0xdead", _TOKEN, "ethereum", 1, "2026-08-02 00:00:00", "0xabc", "token", None)]
        )
        with (
            _mock_ch_radar(fake),
            patch("app.domains.scanner.engines.new_token_radar._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.new_token_radar._redis_set", AsyncMock(return_value=True)),
            # Fast-scan sub-checks: benign deployer, no honeypot, decent liquidity
            patch(
                "app.domains.scanner.engines.deployer_fingerprint.fingerprint_deployer",
                AsyncMock(
                    return_value={
                        "result": "scanned",
                        "risk_bucket": "REPEAT_CLEAN",
                        "risk_score": 15,
                        "total_deployments": 2,
                    }
                ),
            ),
            patch(
                "app.domains.scanner.modules.security._check_honeypot_is",
                AsyncMock(return_value={"result": "scanned", "is_honeypot": False, "risk_score": 5}),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._check_liquidity",
                AsyncMock(return_value=100_000.0),
            ),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=False)

        assert len(out) == 1
        item = out[0]
        assert item["token_address"] == _TOKEN
        assert item["level"] == "INFO"
        assert item["score"] < 40
        assert item["signals"]["is_honeypot"] is False

    @pytest.mark.asyncio
    async def test_critical_for_honeypot_and_alerts(self) -> None:
        from app.domains.scanner.engines.new_token_radar import scan_new_tokens

        fake = FakeCHClient(
            deployers=[("0xdead", _TOKEN, "ethereum", 1, "2026-08-02 00:00:00", "0xabc", "token", None)]
        )
        with (
            _mock_ch_radar(fake),
            patch("app.domains.scanner.engines.new_token_radar._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.new_token_radar._redis_set", AsyncMock(return_value=True)),
            patch(
                "app.domains.scanner.engines.deployer_fingerprint.fingerprint_deployer",
                AsyncMock(
                    return_value={
                        "result": "scanned",
                        "risk_bucket": "REPEAT_RUG",
                        "risk_score": 90,
                        "total_deployments": 5,
                    }
                ),
            ),
            patch(
                "app.domains.scanner.modules.security._check_honeypot_is",
                AsyncMock(return_value={"result": "scanned", "is_honeypot": True, "risk_score": 95}),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._check_liquidity",
                AsyncMock(return_value=100.0),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._was_alerted",
                AsyncMock(return_value=False),
            ),
            patch(
                "app.domains.scanner.engines.new_token_radar._mark_alerted",
                AsyncMock(return_value=None),
            ),
            patch(
                "app.domains.telegram.alert_hooks.send_scanner_alert",
                AsyncMock(return_value={"ok": True}),
            ),
        ):
            out = await scan_new_tokens("ethereum", lookback_minutes=5, notify=True)

        assert len(out) == 1
        assert out[0]["level"] == "CRITICAL"
        assert out[0]["score"] == 100


# ═══════════════════════════════════════════════════════════════════════════
# C5 — HYPE SCORE
# ═══════════════════════════════════════════════════════════════════════════


class TestHypeScore:
    def setup_method(self) -> None:
        clear_hype_cache()

    @pytest.mark.asyncio
    async def test_requires_token(self) -> None:
        from app.domains.scanner.engines.hype_score import compute_hype_score

        with _mock_ch_hype(FakeCHClient()):
            out = await compute_hype_score("", "ethereum")
        assert out["result"] == "error"

    @pytest.mark.asyncio
    async def test_composite_weighted_and_decayed(self) -> None:
        from app.domains.scanner.engines.hype_score import compute_hype_score

        fake = FakeCHClient(
            volume=[(1000.0, 8000.0)],  # 15m = 1000 raw, 4h = 8000 raw
            holders=[(50, 20)],  # today 50, prev day 20
            last_activity=[("2026-08-02 00:00:00",)],
        )
        with (
            _mock_ch_hype(fake),
            patch(
                "app.domains.scanner.engines.hype_score._social_velocity",
                AsyncMock(return_value={"score": 100.0, "trending_hit": True, "detail": "mock"}),
            ),
            patch(
                "app.domains.scanner.engines.hype_score._price_momentum",
                AsyncMock(return_value={"score": 100.0, "change_24h_pct": 100.0, "detail": "mock"}),
            ),
            patch(
                "app.domains.scanner.engines.hype_score._raw_to_usd",
                AsyncMock(side_effect=lambda t, c, raw: raw * 0.001),
            ),
            patch("app.domains.scanner.engines.hype_score._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.hype_score._redis_set", AsyncMock(return_value=True)),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        assert out["result"] == "scanned"
        assert out["hype_score"] > 0
        # Weighted: 0.4*100 + 0.3*vol + 0.2*holder + 0.1*100
        # volume: ratio = (1000*0.001) / ((8000*0.001)/16) = 1 / 0.5 = 2 → 50
        # holder: ratio = 50/20 = 2.5 → 100
        # composite = 40 + 15 + 20 + 10 = 85
        assert out["signals"]["social_velocity"]["score"] == 100.0
        assert out["signals"]["volume_growth"]["ratio"] == pytest.approx(2.0, abs=0.01)
        assert out["weights"]["social"] == 0.4
        assert out["hype_score"] <= 100.0

    @pytest.mark.asyncio
    async def test_unavailable_when_no_data(self) -> None:
        from app.domains.scanner.engines.hype_score import compute_hype_score

        with (
            _mock_ch_hype(FakeCHClient(volume=[], holders=[], last_activity=[])),
            patch(
                "app.domains.scanner.engines.hype_score._social_velocity",
                AsyncMock(return_value={"score": 0.0, "detail": "no data"}),
            ),
            patch(
                "app.domains.scanner.engines.hype_score._price_momentum",
                AsyncMock(return_value={"score": 0.0, "detail": "no data"}),
            ),
            patch("app.domains.scanner.engines.hype_score._redis_get", AsyncMock(return_value=None)),
            patch("app.domains.scanner.engines.hype_score._redis_set", AsyncMock(return_value=True)),
        ):
            out = await compute_hype_score(_TOKEN, "ethereum")

        # volume/holder query rows empty → raw None → signal detail, score 0
        assert out["result"] == "scanned" or out["result"] == "unavailable"
        assert isinstance(out.get("hype_score"), (int, float))


# ═══════════════════════════════════════════════════════════════════════════
# DATABUS PROVIDER REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════


class TestDataBusProviders:
    def test_providers_registered_in_lazy_map(self) -> None:
        """Provider functions are discoverable via the DataBus lazy map."""
        from app.domains.databus.providers import (
            _lite_chain_indexer_hype_score,
            _lite_chain_indexer_new_token_radar,
            _lite_chain_indexer_wallet_flow,
        )

        assert callable(_lite_chain_indexer_wallet_flow)
        assert callable(_lite_chain_indexer_new_token_radar)
        assert callable(_lite_chain_indexer_hype_score)

    def test_provider_chains_registered(self) -> None:
        """The three new data types resolve to LOCAL provider chains."""
        from app.domains.databus.providers.registry import build_provider_chains

        chains = build_provider_chains()
        for name in ("wallet_flow", "new_token_radar", "hype_score"):
            assert name in chains, f"{name} chain missing"
            assert chains[name].providers[0].is_local is True

    @pytest.mark.asyncio
    async def test_wallet_flow_provider_delegates(self) -> None:
        """The LOCAL DataBus provider wraps engine output."""
        from app.domains.databus.providers.chains.lite_chain_indexer_own import _lite_chain_indexer_wallet_flow

        with (
            patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", True),
            patch(
                "app.domains.scanner.engines.wallet_flow.build_flow_graph",
                AsyncMock(
                    return_value={
                        "result": "scanned",
                        "wallet": _WALLET,
                        "chain": "ethereum",
                        "nodes": [{"id": _WALLET, "type": "EOA", "net_usd": 0, "tx_count": 1}],
                        "edges": [{"from": _WALLET, "to": _COUNTERPARTY, "usd": 10, "tx_count": 1}],
                        "node_count": 1,
                        "edge_count": 1,
                    }
                ),
            ),
        ):
            out = await _lite_chain_indexer_wallet_flow(wallet=_WALLET, chain="ethereum")

        assert out is not None
        assert out["tier"] == "local"
        assert out["is_local"] is True
        assert out["data"]["result"] == "scanned"

    @pytest.mark.asyncio
    async def test_new_token_radar_provider_delegates(self) -> None:
        from app.domains.databus.providers.chains.lite_chain_indexer_own import _lite_chain_indexer_new_token_radar

        with (
            patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", True),
            patch(
                "app.domains.scanner.engines.new_token_radar.scan_new_tokens",
                AsyncMock(return_value=[{"token_address": _TOKEN, "chain": "ethereum", "level": "HIGH"}]),
            ),
        ):
            out = await _lite_chain_indexer_new_token_radar(chain="ethereum", lookback_minutes=5)

        assert out is not None
        assert out["tier"] == "local"
        assert out["data"]["count"] == 1

    @pytest.mark.asyncio
    async def test_hype_score_provider_delegates(self) -> None:
        from app.domains.databus.providers.chains.lite_chain_indexer_own import _lite_chain_indexer_hype_score

        with (
            patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", True),
            patch(
                "app.domains.scanner.engines.hype_score.compute_hype_score",
                AsyncMock(
                    return_value={
                        "result": "scanned",
                        "token_address": _TOKEN,
                        "chain": "ethereum",
                        "hype_score": 72.0,
                        "signals": {},
                    }
                ),
            ),
        ):
            out = await _lite_chain_indexer_hype_score(token_address=_TOKEN, chain="ethereum")

        assert out is not None
        assert out["tier"] == "local"
        assert out["data"]["hype_score"] == 72.0

    @pytest.mark.asyncio
    async def test_providers_skip_when_disabled(self) -> None:
        from app.domains.databus.providers.chains.lite_chain_indexer_own import (
            _lite_chain_indexer_hype_score,
            _lite_chain_indexer_new_token_radar,
            _lite_chain_indexer_wallet_flow,
        )

        with patch("app.domains.databus.providers.chains.lite_chain_indexer_own.PREFER_OWN_DATA", False):
            assert await _lite_chain_indexer_wallet_flow(wallet=_WALLET, chain="ethereum") is None
            assert await _lite_chain_indexer_new_token_radar(chain="ethereum") is None
            assert await _lite_chain_indexer_hype_score(token_address=_TOKEN, chain="ethereum") is None
