"""Pure unit tests for the 24h Market Movers pipeline.

No network, no DB. Mocks the market (CoinGecko ``_cg``) and GMGN fetchers
(``get_new_pairs`` / ``get_token_info``). Covers stablecoin exclusion,
near-zero-change exclusion, timeframe field selection, GMGN merge source
marking, and the Telegram renderer's per-token links.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from app.domains.telegram.rugmunchbot.handlers.market import _mover_link, _render_movers_lines
from app.routers.market_intel import providers
from app.routers.market_intel.providers import MOVER_MIN_CHANGE_PCT, _hyperliquid_action, _is_stablecoin
from app.routers.market_intel.routes import overview

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


# Route handlers declare `request: Request` for FastAPI; get_market_movers never
# reads it, so tests pass a typed stand-in (mypy: Any is compatible with Request).
_NO_REQUEST: Any = cast("Any", None)


# ── helpers ──────────────────────────────────────────────────────────────


def _async_ret(value: Any) -> Callable[..., Any]:
    """Wrap a value in an async callable that ignores args and returns it."""

    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value

    return _f


def _cg_row(
    symbol: str,
    name: str,
    cg_id: str,
    change_1h: float = 0.0,
    change_24h: float = 0.0,
    change_7d: float = 0.0,
    price: float = 1.0,
    volume: float = 1_000_000,
) -> dict[str, Any]:
    """A minimal CoinGecko /coins/markets row."""
    return {
        "symbol": symbol,
        "name": name,
        "id": cg_id,
        "market_cap_rank": 1,
        "current_price": price,
        "price_change_percentage_1h_in_currency": change_1h,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": change_7d,
        "total_volume": volume,
        "market_cap": 1_000_000_000,
        "fully_diluted_valuation": 1_000_000_000,
        "image": "",
    }


def _hl_market(symbol: str, change: float, volume: float = 10_000_000) -> dict[str, Any]:
    """A minimal Hyperliquid market dict."""
    return {
        "symbol": symbol,
        "price": 1.0,
        "change_24h": change,
        "volume_24h": volume,
        "open_interest": 1_000_000,
        "funding_rate": 0.01,
        "funding_rate_pct": 1.0,
    }


class _FakeGMGN:
    """Stand-in for app.databus.gmgn_provider.GMGNProvider (no network)."""

    def __init__(self, pairs: list[dict[str, Any]], token_infos: dict[str, dict[str, Any]] | None = None):
        self.pairs = pairs
        self.token_infos = token_infos or {}

    async def get_new_pairs(self, chain: str = "solana", limit: int = 50) -> list[dict[str, Any]]:
        return self.pairs[:limit]

    async def get_token_info(self, address: str, chain: str = "solana") -> dict[str, Any]:
        return self.token_infos.get(address, {})


# ── stablecoin blacklist helper ──────────────────────────────────────────


def test_is_stablecoin_by_symbol_name_and_address() -> None:
    assert _is_stablecoin("USDT")
    assert _is_stablecoin("usdc")  # case-insensitive
    assert _is_stablecoin("CRVUSD")
    assert _is_stablecoin("", "Tether USD")
    assert _is_stablecoin("", "Stablecoin XYZ")
    # EVM address matching is case-insensitive
    assert _is_stablecoin("", "", "0xdAC17F958D2ee523a2206206994597C13D831ec7")
    assert _is_stablecoin("", "", "0xdac17f958d2ee523a2206206994597c13d831ec7")
    # Solana USDC
    assert _is_stablecoin("", "", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    assert not _is_stablecoin("PEPE")
    assert not _is_stablecoin("BTC")
    assert not _is_stablecoin("", "Pepe the Frog", "0x1234")


# ── providers._hyperliquid_action filtering ──────────────────────────────


async def test_hyperliquid_action_excludes_stablecoins_and_flat_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = [
        _hl_market("USDT", 0.01),
        _hl_market("USDC", -0.02),
        _hl_market("BTC", 0.3),  # below threshold — not a mover
        _hl_market("PEPE", 45.0),
        _hl_market("BONK", -20.0),
    ]
    monkeypatch.setattr(providers, "_hyperliquid_markets", _async_ret(markets))
    # `_hyperliquid_action` caches under "hl_action" — bypass cache for purity.
    monkeypatch.setattr(providers, "_cget", lambda key, ttl: None)
    monkeypatch.setattr(providers, "_cset", lambda key, data: None)

    result = await _hyperliquid_action(limit=10)
    assert [m["symbol"] for m in result["top_gainers"]] == ["PEPE"]
    assert [m["symbol"] for m in result["top_losers"]] == ["BONK"]


async def test_hyperliquid_action_keeps_major_when_it_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = [
        _hl_market("USDT", 0.0),
        _hl_market("BTC", 3.5),  # major that actually moved — allowed
        _hl_market("ETH", 0.1),  # flat major — dropped
        _hl_market("PEPE", 30.0),
    ]
    monkeypatch.setattr(providers, "_hyperliquid_markets", _async_ret(markets))
    monkeypatch.setattr(providers, "_cget", lambda key, ttl: None)
    monkeypatch.setattr(providers, "_cset", lambda key, data: None)

    result = await _hyperliquid_action(limit=10)
    assert [m["symbol"] for m in result["top_gainers"]] == ["PEPE", "BTC"]
    assert "ETH" not in [m["symbol"] for m in result["top_gainers"]]


# ── overview.get_market_movers: stablecoin / threshold filtering ─────────


async def test_movers_exclude_stablecoins(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _cg_row("USDT", "Tether", "tether", change_24h=0.01),
        _cg_row("USDC", "USD Coin", "usd-coin", change_24h=-0.02),
        _cg_row("DAI", "Dai Stablecoin", "dai", change_24h=0.05),
        _cg_row("PEPE", "Pepe", "pepe", change_24h=12.5),
        _cg_row("BONK", "Bonk", "bonk", change_24h=-8.2),
    ]
    monkeypatch.setattr(overview, "_cg", _async_ret(rows))
    monkeypatch.setattr(overview, "_gmgn_degen_movers", _async_ret([]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10)
    symbols = {i["symbol"] for i in result["top_gainers"] + result["top_losers"]}
    assert "USDT" not in symbols
    assert "USDC" not in symbols
    assert "DAI" not in symbols
    assert "PEPE" in symbols
    assert "BONK" in symbols


async def test_movers_exclude_near_zero_change(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _cg_row("BTC", "Bitcoin", "bitcoin", change_24h=0.1),  # below threshold
        _cg_row("SOL", "Solana", "solana", change_24h=0.3),  # below threshold
        _cg_row("DOGE", "Dogecoin", "dogecoin", change_24h=1.2),
        _cg_row("SHIB", "Shiba Inu", "shiba-inu", change_24h=-2.1),
    ]
    monkeypatch.setattr(overview, "_cg", _async_ret(rows))
    monkeypatch.setattr(overview, "_gmgn_degen_movers", _async_ret([]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10)
    symbols = {i["symbol"] for i in result["top_gainers"] + result["top_losers"]}
    assert "BTC" not in symbols
    assert "SOL" not in symbols
    assert "DOGE" in symbols
    assert "SHIB" in symbols


async def test_movers_threshold_constant_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tokens exactly at the floor boundary are excluded."""
    rows = [
        _cg_row("PEPE", "Pepe", "pepe", change_24h=MOVER_MIN_CHANGE_PCT - 0.1),
        _cg_row("BONK", "Bonk", "bonk", change_24h=MOVER_MIN_CHANGE_PCT + 0.1),
    ]
    monkeypatch.setattr(overview, "_cg", _async_ret(rows))
    monkeypatch.setattr(overview, "_gmgn_degen_movers", _async_ret([]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10)
    symbols = {i["symbol"] for i in result["top_gainers"] + result["top_losers"]}
    assert "PEPE" not in symbols
    assert "BONK" in symbols


# ── overview.get_market_movers: timeframe field selection ────────────────


async def test_movers_timeframe_field_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _cg_row("PEPE", "Pepe", "pepe", change_1h=5.0, change_24h=1.0, change_7d=-3.0),
        _cg_row("BONK", "Bonk", "bonk", change_1h=1.0, change_24h=2.0, change_7d=9.0),
        _cg_row("DOGE", "Dogecoin", "dogecoin", change_1h=0.6, change_24h=3.0, change_7d=1.0),
    ]
    monkeypatch.setattr(overview, "_cg", _async_ret(rows))
    monkeypatch.setattr(overview, "_gmgn_degen_movers", _async_ret([]))

    r1h = await overview.get_market_movers(request=_NO_REQUEST, limit=10, timeframe="1h")
    assert r1h["timeframe"] == "1h"
    assert r1h["top_gainers"][0]["symbol"] == "PEPE"

    r7d = await overview.get_market_movers(request=_NO_REQUEST, limit=10, timeframe="7d")
    assert r7d["top_gainers"][0]["symbol"] == "BONK"

    r24 = await overview.get_market_movers(request=_NO_REQUEST, limit=10, timeframe="24h")
    assert r24["timeframe"] == "24h"
    assert r24["top_gainers"][0]["symbol"] == "DOGE"


async def test_movers_6h_falls_back_to_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _cg_row("PEPE", "Pepe", "pepe", change_1h=9.0, change_24h=1.0, change_7d=0.0),
        _cg_row("DOGE", "Dogecoin", "dogecoin", change_1h=0.2, change_24h=4.0, change_7d=0.0),
    ]
    monkeypatch.setattr(overview, "_cg", _async_ret(rows))
    monkeypatch.setattr(overview, "_gmgn_degen_movers", _async_ret([]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10, timeframe="6h")
    assert result["timeframe"] == "6h"
    # 6h is not provided by CoinGecko — ranked by the 24h field.
    assert result["top_gainers"][0]["symbol"] == "DOGE"


# ── overview.get_market_movers: GMGN merge ───────────────────────────────


async def test_gmgn_merge_marks_source(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGMGN(
        pairs=[
            {
                "address": "PEPEPUMP111",
                "chain": "solana",
                "symbol": "PEPEPUMP",
                "name": "Pepe Pump",
                "price": 0.0001,
                "liquidity": 50_000,
                "market_cap": 100_000,
                "created_at": 0,
                "age_seconds": 120,
                "launchpad": "pump",
                "source": "gmgn",
            },
            {
                "address": "USDCADDR",
                "chain": "solana",
                "symbol": "USDC",
                "name": "USD Coin",
                "price": 1.0,
                "liquidity": 1_000_000,
                "age_seconds": 60,
                "source": "gmgn",
            },
        ],
        token_infos={"PEPEPUMP111": {"price_change_24h": 45.0, "volume_24h": 250_000}},
    )
    monkeypatch.setattr("app.databus.gmgn_provider.get_gmgn_provider", lambda: fake)
    monkeypatch.setattr(overview, "_cg", _async_ret([_cg_row("PEPE", "Pepe", "pepe", change_24h=12.0)]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10)

    assert "gmgn" in result["sources"]
    gmgn = [i for i in result["top_gainers"] if i.get("source") == "gmgn"]
    assert gmgn, "GMGN fresh pair should be merged into the gainers feed"
    assert gmgn[0]["symbol"] == "PEPEPUMP"
    assert gmgn[0]["is_new_launch"] is True
    assert gmgn[0]["change_24h"] == 45.0  # enriched via get_token_info
    assert gmgn[0]["volume_24h"] == 250_000.0
    # Stablecoin pair from GMGN must be dropped too.
    symbols = {i["symbol"] for i in result["top_gainers"] + result["top_losers"]}
    assert "USDC" not in symbols


async def test_gmgn_failure_falls_back_to_coingecko(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("gmgn down")

    monkeypatch.setattr("app.databus.gmgn_provider.get_gmgn_provider", _boom)
    monkeypatch.setattr(overview, "_cg", _async_ret([_cg_row("PEPE", "Pepe", "pepe", change_24h=12.0)]))

    result = await overview.get_market_movers(request=_NO_REQUEST, limit=10)
    assert result["sources"] == ["coingecko"]
    assert result["top_gainers"][0]["symbol"] == "PEPE"


# ── renderer: links per token ────────────────────────────────────────────


def test_mover_link_gmgn_uses_dexscreener() -> None:
    item = {"address": "abc123", "chain": "solana", "source": "gmgn"}
    assert _mover_link(item) == "https://dexscreener.com/solana/abc123"


def test_mover_link_coingecko_uses_coin_page() -> None:
    item = {"address": "pepe", "chain": "multi", "source": "coingecko"}
    assert _mover_link(item) == "https://www.coingecko.com/en/coins/pepe"


def test_renderer_links_every_token() -> None:
    gainers = [
        {
            "symbol": "PEPE",
            "name": "Pepe",
            "change_24h": 12.5,
            "price": 0.0001,
            "volume_24h": 1_000_000,
            "address": "pepe",
            "chain": "multi",
            "source": "coingecko",
        },
        {
            "symbol": "DOGE",
            "name": "Dogecoin",
            "change_24h": 5.0,
            "price": 0.12,
            "volume_24h": 5_000_000,
            "address": "dogecoin",
            "chain": "multi",
            "source": "coingecko",
        },
    ]
    losers = [
        {
            "symbol": "BONK",
            "name": "Bonk",
            "change_24h": -8.2,
            "price": 0.00001,
            "volume_24h": 2_000_000,
            "address": "bonk",
            "chain": "multi",
            "source": "coingecko",
        },
    ]
    lines = _render_movers_lines(gainers, losers, "24h", ["coingecko", "gmgn"])
    text = "\n".join(lines)

    # One tap-through link per token (3 tokens) + footer links.
    assert text.count("href=") >= 3
    assert "https://www.coingecko.com/en/coins/pepe" in text
    assert "https://www.coingecko.com/en/coins/dogecoin" in text
    assert "https://www.coingecko.com/en/coins/bonk" in text
    assert "Sources: coingecko, gmgn" in text
    assert "24H Market Movers" in text


def test_renderer_gmgn_entry_gets_new_badge_and_dexscreener_link() -> None:
    gainers = [
        {
            "symbol": "PEPEPUMP",
            "name": "Pepe Pump",
            "change_24h": 45.0,
            "price": 0.0001,
            "volume_24h": 250_000,
            "address": "PEPEPUMP111",
            "chain": "solana",
            "source": "gmgn",
            "is_new_launch": True,
        },
    ]
    lines = _render_movers_lines(gainers, [], "24h", ["gmgn"])
    text = "\n".join(lines)

    assert "(GMGN)" in text
    assert "https://dexscreener.com/solana/PEPEPUMP111" in text
