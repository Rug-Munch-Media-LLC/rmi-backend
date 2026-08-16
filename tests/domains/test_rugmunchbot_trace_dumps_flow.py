"""Tests for the RugMunchBot /dumps and /flow commands (ported from rugmunchbot).

``app.domains.telegram.rugmunchbot.handlers.trace`` hosts the transaction
tracing command family:
  * /trace  — cross-chain fund flow tracing (pre-existing)
  * /dumps  — large sell/dump detection via DexScreener (ported)
  * /flow   — follow-the-money fund flow trace via cross-chain aggregator (ported)

These tests drive the handlers with lightweight fake Telegram objects — no
live bot, no real DB writes, no network (httpx is mocked).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.telegram.rugmunchbot.handlers import trace as trace_mod

VALID_TOKEN = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
VALID_WALLET = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"

DUMP_PAIRS = [
    {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "baseToken": {"name": "Pepe", "symbol": "PEPE"},
        "priceUsd": "0.00001",
        "priceChange": {"h1": -4.5, "h24": -22.1},
        "liquidity": {"usd": 1200000},
        "volume": {"h24": 850000},
        "txns": {"h24": {"buys": 400, "sells": 350}},
    },
    {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "baseToken": {"name": "Pepe", "symbol": "PEPE"},
        "priceUsd": "0.00001",
        "priceChange": {"h1": -1.0, "h24": -5.0},
        "liquidity": {"usd": 500000},
        "volume": {"h24": 200000},
        "txns": {"h24": {"buys": 120, "sells": 100}},
    },
]

FLOW_PROFILE = {
    "entity_id": "ent_525ef124a673a803",
    "primary_address": VALID_WALLET.lower(),
    "primary_chain": "ethereum",
    "addresses": [
        {
            "address": VALID_WALLET.lower(),
            "chain": "ethereum",
            "confidence": 1.0,
            "method": "same_deployer",
            "evidence": "Direct lookup",
        }
    ],
    "total_addresses": 1,
    "total_chains": 1,
    "chains": ["ethereum"],
    "risk_score": 12.0,
    "label": "Vitalik Buterin",
}


class _FakeUser:
    id = 7075336557  # owner id — bypasses tier/rate gates
    username = "tracer"
    first_name = "Tracer"


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> _FakeMessage:
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append((text, kwargs))


class _FakeUpdate:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.effective_user = _FakeUser()


class _FakeContext:
    def __init__(self, args: list[str] | None = None) -> None:
        self.args = list(args or [])
        self.bot = AsyncMock()
        self.bot.send_message = AsyncMock()


def _mock_get_json(*_a: Any, **_k: Any) -> Any:
    raise AssertionError("network should be mocked")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate DB and rate-limit state per test."""
    import app.domains.telegram.rugmunchbot.formatters as fmt_mod

    monkeypatch.setattr(fmt_mod, "OWNER_IDS", {999000111})
    monkeypatch.setattr(trace_mod.db, "get_or_create_user", lambda *a, **k: None)
    monkeypatch.setattr(trace_mod.db, "increment_usage", lambda *a, **k: True)
    monkeypatch.setattr(trace_mod.db, "log_scan", lambda *a, **k: None)
    monkeypatch.setattr(trace_mod.db, "get_user_tier", lambda uid: "hunter")
    monkeypatch.setattr(trace_mod.db, "check_rate_limit", lambda *a, **k: (True, 0, 0))


class TestCmdDumpsUsage:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self) -> None:
        """/dumps with no args prints the usage card and does not hit the network."""
        msg = _FakeMessage()
        ctx = _FakeContext()
        with patch.object(trace_mod.httpx, "AsyncClient", side_effect=_mock_get_json):
            await trace_mod.cmd_dumps(_FakeUpdate(msg), ctx)

        assert len(msg.replies) == 1
        assert "Dump Detector" in msg.replies[0][0]
        assert "Usage:" in msg.replies[0][0]

    @pytest.mark.asyncio
    async def test_usage_does_not_require_scan_rate(self) -> None:
        """/dumps usage path skips rate limiting entirely (no args → return first)."""
        msg = _FakeMessage()
        ctx = _FakeContext()
        with patch.object(trace_mod.db, "check_rate_limit", side_effect=AssertionError("rate limit hit")):
            await trace_mod.cmd_dumps(_FakeUpdate(msg), ctx)

        assert len(msg.replies) == 1


class TestCmdDumpsScan:
    @pytest.mark.asyncio
    async def test_dumps_detects_large_sells(self) -> None:
        """/dumps parses DexScreener pairs and reports detected dumps."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_TOKEN])

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"pairs": DUMP_PAIRS}

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, *a: Any, **k: Any) -> _Resp:
                return _Resp()

        with patch.object(trace_mod.httpx, "AsyncClient", _FakeClient):
            await trace_mod.cmd_dumps(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, kwargs = msg.edits[0]
        assert "Dump Detector" in text
        assert "PEPE" in text
        assert "Detected Dumps" in text
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_dumps_no_pairs(self) -> None:
        """/dumps reports when no DEX pairs are found for the address."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_TOKEN])

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"pairs": []}

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, *a: Any, **k: Any) -> _Resp:
                return _Resp()

        with patch.object(trace_mod.httpx, "AsyncClient", _FakeClient):
            await trace_mod.cmd_dumps(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        assert "No trading pairs found" in msg.edits[0][0]

    @pytest.mark.asyncio
    async def test_dumps_backend_unavailable(self) -> None:
        """/dumps surfaces a friendly error when DexScreener is unreachable."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_TOKEN])

        class _Resp:
            status_code = 503

            @staticmethod
            def json() -> dict:
                return {}

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, *a: Any, **k: Any) -> _Resp:
                return _Resp()

        with patch.object(trace_mod.httpx, "AsyncClient", _FakeClient):
            await trace_mod.cmd_dumps(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        assert "DexScreener unavailable" in msg.edits[0][0]


class TestCmdFlowUsage:
    @pytest.mark.asyncio
    async def test_no_args_shows_usage(self) -> None:
        """/flow with no args prints the usage card and does not hit the network."""
        msg = _FakeMessage()
        ctx = _FakeContext()
        with patch.object(trace_mod.httpx, "AsyncClient", side_effect=_mock_get_json):
            await trace_mod.cmd_flow(_FakeUpdate(msg), ctx)

        assert len(msg.replies) == 1
        assert "Fund Flow Tracer" in msg.replies[0][0]
        assert "Usage:" in msg.replies[0][0]


class TestCmdFlowTrace:
    @pytest.mark.asyncio
    async def test_flow_renders_cross_chain_profile(self) -> None:
        """/flow renders the cross-chain aggregator profile with risk + wallets."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])

        class _Resp:
            status_code = 200

            def __init__(self, payload: dict) -> None:
                self._payload = payload

            def json(self) -> dict:
                return self._payload

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, url: str, *a: Any, **k: Any) -> _Resp:
                if "/cross-chain/" in url:
                    return _Resp(FLOW_PROFILE)
                # databus wallet endpoint
                return _Resp({})

        with patch.object(trace_mod.httpx, "AsyncClient", _FakeClient):
            await trace_mod.cmd_flow(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, kwargs = msg.edits[0]
        assert "Fund Flow Trace" in text
        assert "Vitalik Buterin" in text
        assert "Cross-Chain Wallets" in text
        assert "Risk Assessment" in text
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_flow_no_data(self) -> None:
        """/flow reports when the aggregator has no data for the address."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {}

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, *a: Any, **k: Any) -> _Resp:
                return _Resp()

        with patch.object(trace_mod.httpx, "AsyncClient", _FakeClient):
            await trace_mod.cmd_flow(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        assert "No wallet data found" in msg.edits[0][0]


TRACE_FLOW_SUMMARY = {
    "wallet": VALID_WALLET.lower(),
    "chain": "ethereum",
    "hops": 2,
    "in_usd": 3000.0,
    "out_usd": 5000.0,
    "net_usd": 2000.0,
    "in_tx_count": 1,
    "out_tx_count": 1,
    "total_tx_count": 2,
    "usd_unavailable": False,
    "counterparty_in": [
        {
            "address": "0x2222222222222222222222222222222222222222",
            "usd": 3000.0,
            "tx_count": 1,
            "type": "EOA",
            "label": None,
        }
    ],
    "counterparty_out": [
        {
            "address": "0x3333333333333333333333333333333333333333",
            "usd": 5000.0,
            "tx_count": 1,
            "type": "CEX",
            "label": "Binance Hot Wallet",
        }
    ],
    "exchange": {"in_usd": 0.0, "out_usd": 5000.0, "tx_count": 1, "share_pct": 62.5, "share_tx_pct": 50.0},
    "wash": {"reciprocal_pairs": 0, "tx_count": 0, "signal": False},
    "edge_count": 2,
    "node_count": 3,
    "note": "Hop-1 + top-10 hop-2 counterparties; USD via F2 price oracle",
}


class TestCmdTraceOwnData:
    """/trace renders the own ClickHouse fund-flow trace + AI read (fail-open)."""

    def _fake_client(self, wallet_payload: dict, cc_payload: dict) -> type:
        class _Resp:
            status_code = 200

            def __init__(self, payload: dict) -> None:
                self._payload = payload

            def json(self) -> dict:
                return self._payload

        class _FakeClient:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                return None

            async def get(self, url: str, *a: Any, **k: Any) -> _Resp:
                if "/cross-chain/" in url:
                    return _Resp(cc_payload)
                return _Resp(wallet_payload)

        return _FakeClient

    @pytest.mark.asyncio
    async def test_trace_renders_clickhouse_flow_and_ai_read(self) -> None:
        """/trace surfaces the deterministic flow AND the AI read together."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])
        ai_read = (
            "🧠 <b>AI Flow Read</b>\n"
            "Funds were funded by a single EOA and 62% of USD volume exited "
            "to a Binance-labeled wallet — consistent with cashing out."
        )
        with (
            patch.object(trace_mod.httpx, "AsyncClient", self._fake_client({}, {})),
            patch.object(trace_mod, "_fetch_clickhouse_flow", AsyncMock(return_value=TRACE_FLOW_SUMMARY)),
            patch.object(trace_mod, "_render_ai_flow_read", AsyncMock(return_value=ai_read)),
        ):
            await trace_mod.cmd_trace(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, kwargs = msg.edits[0]
        assert "Fund Flow (own chain data)" in text
        assert "Exchange share:" in text
        assert "Binance Hot Wallet" in text
        assert "Top senders" in text
        assert "AI Flow Read" in text
        assert "cashing out" in text
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_trace_ai_read_fails_open_keeps_flow(self) -> None:
        """/trace LLM down → deterministic flow still renders, no AI block."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])
        with (
            patch.object(trace_mod.httpx, "AsyncClient", self._fake_client({}, {})),
            patch.object(trace_mod, "_fetch_clickhouse_flow", AsyncMock(return_value=TRACE_FLOW_SUMMARY)),
            patch.object(trace_mod, "_render_ai_flow_read", AsyncMock(return_value="")),
        ):
            await trace_mod.cmd_trace(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, _kwargs = msg.edits[0]
        assert "Fund Flow (own chain data)" in text
        assert "AI Flow Read" not in text
        assert "Trace Failed" not in text

    @pytest.mark.asyncio
    async def test_trace_fails_open_without_clickhouse(self) -> None:
        """/trace ClickHouse down → falls back to the legacy external path."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])
        wallet_payload = {"funding_source": "Funded by exchange 0x1234", "tx_count": 42}
        with (
            patch.object(trace_mod.httpx, "AsyncClient", self._fake_client(wallet_payload, FLOW_PROFILE)),
            patch.object(trace_mod, "_fetch_clickhouse_flow", AsyncMock(return_value=None)),
        ):
            await trace_mod.cmd_trace(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, _kwargs = msg.edits[0]
        assert "Fund Flow (own chain data)" not in text  # no ClickHouse block
        assert "AI Flow Read" not in text
        assert "Funding Source" in text  # legacy external path still renders
        assert "Cross-Chain Activity" in text

    @pytest.mark.asyncio
    async def test_trace_clickhouse_flow_exception_fails_open(self) -> None:
        """/trace flow-builder crash → legacy path renders, never an error card."""
        msg = _FakeMessage()
        ctx = _FakeContext(args=[VALID_WALLET])
        with (
            patch.object(trace_mod.httpx, "AsyncClient", self._fake_client({}, {})),
            patch.object(
                trace_mod,
                "_fetch_clickhouse_flow",
                AsyncMock(side_effect=RuntimeError("CH down")),
            ),
        ):
            await trace_mod.cmd_trace(_FakeUpdate(msg), ctx)

        assert len(msg.edits) == 1
        text, _kwargs = msg.edits[0]
        assert "Fund Flow (own chain data)" not in text
        assert "Trace Failed" not in text


class TestRegisterTraceCommands:
    def test_registers_dumps_and_flow(self) -> None:
        """/dumps and /flow are wired via register_trace_commands."""
        app = MagicMock()
        trace_mod.register_trace_commands(app)
        assert app.add_handler.call_count == 2

        registered_names = [next(iter(c.args[0].commands)) for c in app.add_handler.call_args_list]
        assert registered_names == ["dumps", "flow"]
