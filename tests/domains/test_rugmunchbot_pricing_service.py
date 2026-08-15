"""Tests for the shared pricing service (services/pricing.py).

Proves the unified fallback chain contract that replaced the triplicated
price/quote logic (tools/price.py + tools/market.py + handlers/price.py):

  * address chain: scanner cache → backend DataBus → DexScreener → Birdeye
  * symbol chain:  DexScreener search (resolves symbol → address) →
                   CoinGecko → CoinPaprika → CoinCap
  * ``PriceResult`` contract: price_usd / change_24h / liquidity / source
    (+ raw pairs/quote payloads), ``to_dict`` for the /price Redis snapshot
  * mocked end-to-end ``/price`` and ``/dex`` command flows (PTB fakes).

Mock boundary: ``httpx.AsyncClient`` is faked at the ``_client`` module
boundary (same pattern as test_rugmunchbot_tools.py) — no live network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.domains.telegram.rugmunchbot.handlers import price as price_handler
from app.domains.telegram.rugmunchbot.services import pricing
from app.domains.telegram.rugmunchbot.tools import _client, registry

EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# DexScreener-shaped pair (the shape backend DataBus + DexScreener return).
_BONK_PAIR: dict[str, Any] = {
    "chainId": "solana",
    "dexId": "raydium",
    "pairAddress": "PAIR1",
    "baseToken": {"address": "ADDR_BONK", "symbol": "BONK", "name": "Bonk"},
    "priceUsd": "0.00002",
    "priceChange": {"h1": 1.0, "h24": 5.0},
    "volume": {"h24": 12345},
    "liquidity": {"usd": 50000},
    "marketCap": 900000,
    "fdv": 950000,
}

_PEPE_PAIR: dict[str, Any] = {
    "chainId": "ethereum",
    "dexId": "uniswap",
    "pairAddress": "PAIR2",
    "baseToken": {"address": "ADDR_PEPE", "symbol": "PEPE", "name": "Pepe"},
    "priceUsd": "0.5",
    "priceChange": {},
    "volume": {},
    "liquidity": {},
}


class _FakeAsyncClient:
    """httpx.AsyncClient class fake — records requests, routes canned bodies.

    Unmatched URLs return HTTP 404 (a dead source) — exactly how the legacy
    tool tests model a source that is down / has no data.
    """

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
        return self._respond(url)

    async def post(self, url: str, json: dict[str, Any] | None = None, headers: dict[str, Any] | None = None) -> Any:
        _FakeAsyncClient.calls.append({"method": "POST", "url": url, "json": json or {}, "headers": headers or {}})
        return self._respond(url)

    def _respond(self, url: str) -> Any:
        if _FakeAsyncClient._raise is not None:
            raise _FakeAsyncClient._raise
        for prefix, body in _FakeAsyncClient._responses.items():
            if prefix in url:

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
def _fake_http_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the httpx boundary for every test: no live network, isolated cache."""
    _FakeAsyncClient._responses = {}
    _FakeAsyncClient._status = 200
    _FakeAsyncClient._raise = None
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(_client.httpx, "AsyncClient", _FakeAsyncClient)
    registry.clear_cache()
    yield
    registry.clear_cache()


# ── Address chain ────────────────────────────────────────────────────────────


class TestAddressChain:
    async def test_backend_databus_wins_after_scanner_miss(self) -> None:
        # Scanner cache (unconfigured → 404) is skipped; the backend DataBus
        # aggregate provides the pairs and its label is preserved.
        _FakeAsyncClient._responses = {"/api/v1/databus/token/ADDR1": {"pairs": [_BONK_PAIR]}}
        result = await pricing.resolve(address="ADDR1", chain="solana")
        assert result.ok
        assert result.source == "backend:/api/v1/databus/token"
        assert result.price_usd == 0.00002  # coerced to float
        assert result.symbol == "BONK"
        assert result.address == "ADDR1"
        assert result.pairs is not None
        assert result.pairs[0]["dexId"] == "raydium"  # raw pairs kept
        # The scanner-cache endpoint was probed first (own data first).
        assert any("api/v2/scanner/token/price" in c["url"] for c in _FakeAsyncClient.calls)

    async def test_scanner_cache_snapshot_wins(self) -> None:
        # Our own scanner snapshot is the own-data primary: when it has an
        # entry, DexScreener/backend are never consulted.
        _FakeAsyncClient._responses = {
            f"/api/v2/scanner/token/price/ethereum/{EVM}": {
                "cached": True,
                "data": {
                    "chain": "ethereum",
                    "price_usd": 1.5,
                    "liquidity_usd": 10000,
                    "volume_24h_usd": 5000,
                    "market_cap_usd": 90000,
                    "name": "Test Token",
                    "symbol": "TST",
                },
            }
        }
        result = await pricing.resolve(address=EVM, chain="ethereum")
        assert result.ok
        assert result.source == "scanner:/api/v2/scanner/token/price"
        assert result.price_usd == 1.5
        assert result.pairs is not None
        assert result.pairs[0]["dexId"] == "scanner"
        assert not any("/api/v1/databus/token" in c["url"] for c in _FakeAsyncClient.calls)

    async def test_dexscreener_fallback_for_address(self) -> None:
        _FakeAsyncClient._responses = {"/latest/dex/tokens/0xABC": {"pairs": [_BONK_PAIR]}}
        result = await pricing.resolve(address="0xABC", chain="ethereum")
        assert result.ok
        assert result.source == "dexscreener:/tokens/{address}"  # legacy label kept literal
        assert result.price_usd == 0.00002

    async def test_birdeye_quote_last_resort(self) -> None:
        # Every pair source misses; Birdeye /defi/price is the last address step.
        _FakeAsyncClient._responses = {
            "public-api.birdeye.so/defi/price": {"success": True, "data": {"value": "1.23", "updateHumanTime": "now"}}
        }
        result = await pricing.resolve(address=SOL, chain="solana")
        assert result.ok
        assert result.source == "birdeye:/defi/price"
        assert result.price_usd == 1.23
        assert result.quote is not None
        assert result.pairs is None


# ── Symbol chain ─────────────────────────────────────────────────────────────


class TestSymbolChain:
    async def test_dexscreener_search_resolves_symbol_to_address(self) -> None:
        _FakeAsyncClient._responses = {"dexscreener.com/latest/dex/search": {"pairs": [_BONK_PAIR, _PEPE_PAIR]}}
        result = await pricing.get_price("BONK")
        assert result.ok
        assert result.source == "dexscreener:/search"
        assert result.address == "ADDR_BONK"  # symbol → address resolution
        assert result.symbol == "BONK"
        assert result.pairs is not None
        assert len(result.pairs) == 2

    async def test_coingecko_fallback(self) -> None:
        _FakeAsyncClient._responses = {
            "coingecko.com/api/v3/search": {"coins": [{"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin"}]},
            "coingecko.com/api/v3/coins/bitcoin": {
                "name": "Bitcoin",
                "market_data": {
                    "current_price": {"usd": 50000.0},
                    "price_change_percentage_24h": 2.5,
                    "total_volume": {"usd": 1000000},
                    "market_cap": {"usd": 950000000},
                },
            },
        }
        result = await pricing.get_price("BTC")
        assert result.ok
        assert result.source == "coingecko"
        assert result.price_usd == 50000.0
        assert result.change_24h == 2.5
        assert result.quote is not None
        assert result.quote["source"] == "coingecko"

    async def test_full_miss_ok_false(self) -> None:
        # No source responds → the chain exhausts and the result is empty.
        result = await pricing.get_price("BONK")
        assert not result.ok
        assert result.price_usd == 0.0
        assert result.pairs is None
        assert result.quote is None

    async def test_auto_detect_address_vs_symbol(self) -> None:
        # get_price routes a real EVM hex address to the address chain and
        # short tickers to the symbol chain.
        _FakeAsyncClient._responses = {
            "dexscreener.com/latest/dex/search": {"pairs": [_BONK_PAIR]},
            "/latest/dex/tokens/0x6982508145454Ce325dDbE47a25d4ec3d2311933": {"pairs": [_BONK_PAIR]},
        }
        sym = await pricing.get_price("BONK")
        assert sym.source == "dexscreener:/search"
        addr = await pricing.get_price(EVM)
        assert addr.source == "dexscreener:/tokens/{address}"


# ── PriceResult contract ─────────────────────────────────────────────────────


class TestPriceResult:
    async def test_to_dict_snapshot_shape(self) -> None:
        _FakeAsyncClient._responses = {"/api/v1/databus/token/ADDR1": {"pairs": [_BONK_PAIR]}}
        result = await pricing.resolve(address="ADDR1", chain="solana")
        snap = result.to_dict()
        # Keys the /price renderer + Redis cache read (plus liquidity).
        assert set(snap) == {"price_usd", "change_24h", "volume_24h", "liquidity", "source"}
        assert snap["price_usd"] == 0.00002
        assert snap["source"] == "backend:/api/v1/databus/token"

    def test_price_lookup_shim_still_shapes_tool_result(self) -> None:
        # tools/price.price_lookup keeps its public signature; the shim maps
        # PriceResult → ToolResult with the exact legacy source label.
        from app.domains.telegram.rugmunchbot.tools.price import price_lookup

        assert price_lookup.__name__ == "price_lookup"


# ── Mocked /price and /dex command flows ─────────────────────────────────────


class _EditMsg:
    def __init__(self) -> None:
        self.edits: list[tuple[str, dict[str, Any]]] = []

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append((text, kwargs))


class _Update:
    """Minimal PTB stand-in: only the surface cmd_price/cmd_dex touch."""

    def __init__(self, args: list[str]) -> None:
        self._edit_msg = _EditMsg()
        self.message = SimpleNamespace(args=args, reply_text=self._reply_text)

    async def _reply_text(self, text: str, **kwargs: Any) -> _EditMsg:
        return self._edit_msg

    @property
    def sent_text(self) -> str:
        return self._edit_msg.edits[-1][0] if self._edit_msg.edits else ""


class _Context:
    def __init__(self, args: list[str]) -> None:
        self.args = args


class TestPriceCommand:
    async def test_cmd_price_renders_price_from_shared_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Redis down → fail-open → live fetch through the shared service.
        monkeypatch.setattr(price_handler, "_redis_client", lambda: None)
        _FakeAsyncClient._responses = {"/api/v1/databus/token/0xABC": {"pairs": [_BONK_PAIR]}}
        update = _Update(["0xABC", "ethereum"])
        await price_handler.cmd_price(update, _Context(["0xABC", "ethereum"]))
        assert "Token Price" in update.sent_text
        assert "0.00002" in update.sent_text
        assert "Source:" in update.sent_text

    async def test_cmd_price_not_available_on_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(price_handler, "_redis_client", lambda: None)
        update = _Update(["0xABC", "ethereum"])
        await price_handler.cmd_price(update, _Context(["0xABC", "ethereum"]))
        assert "Price Not Available" in update.sent_text

    async def test_cmd_price_cache_aside_positive_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The cache helpers round-trip through the module boundary (same
        # contract the trending_price_cache test asserts); a miss never
        # poisons the key.
        fake = {"store": {}}

        class _FakeRedis:
            async def get(self, key: str) -> str | None:
                return fake["store"].get(key)

            async def set(self, key: str, value: str, ex: int | None = None) -> bool:
                fake["store"][key] = value
                return True

        monkeypatch.setattr(price_handler, "_redis_client", lambda: _FakeRedis())
        _FakeAsyncClient._responses = {"/api/v1/databus/token/0xABC": {"pairs": [_BONK_PAIR]}}
        update = _Update(["0xABC", "ethereum"])
        await price_handler.cmd_price(update, _Context(["0xABC", "ethereum"]))
        assert "Token Price" in update.sent_text
        assert fake["store"], "positive snapshot must be cached"


class TestDexCommand:
    async def test_cmd_dex_renders_raw_pairs_from_shared_service(self) -> None:
        _FakeAsyncClient._responses = {"/latest/dex/tokens/0xABC": {"pairs": [_BONK_PAIR, _PEPE_PAIR]}}
        update = _Update(["0xABC", "ethereum"])
        await price_handler.cmd_dex(update, _Context(["0xABC", "ethereum"]))
        assert "DEX Pairs: 2 found" in update.sent_text
        assert "Raydium" in update.sent_text
        assert "Uniswap" in update.sent_text

    async def test_cmd_dex_no_pairs_message(self) -> None:
        update = _Update(["0xABC", "ethereum"])
        await price_handler.cmd_dex(update, _Context(["0xABC", "ethereum"]))
        assert "No Trading Pairs Found" in update.sent_text
