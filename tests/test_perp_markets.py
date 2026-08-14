"""Pure unit tests for the perp-market + cross-chain launch module + chat intent.

Covers ``app/domains/markets/perp_markets.py``:
  - ``fetch_hyperliquid_assets``: normalizes a real ``metaAndAssetCtxs``
    response (HYPE perp shape), fail-open → [].
  - ``fetch_sunswap_bsc_launches``: returns normalized Tron/BSC launches from a
    mocked GMGN provider (tron + bsc, deduped, newest first), fail-open → [].
  - ``format_hyperliquid_assets``: renders the numbered 📈 list, sorts by open
    interest, caps at top_n.

And the chat intent wiring in ``app/domains/chat_tools.py``:
  - ``_detect_tool_intent`` returns "perp" for Hyperliquid/funding/OI asks and
    keeps "$HYPE" price queries on "price".
  - ``_exec_perp`` renders from a mocked fetch; None on empty/error.

No network, DB, or LLM calls — the Hyperliquid client and GMGN provider are
mocked.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import _detect_tool_intent, _exec_perp
from app.domains.markets import perp_markets
from app.domains.markets.perp_markets import (
    _age_seconds,
    _default_gmgn_provider,
    _format_price,
    _to_float,
    fetch_hyperliquid_assets,
    fetch_sunswap_bsc_launches,
    format_hyperliquid_assets,
    normalize_launch,
    normalize_perp_asset,
)

# ── Fixtures ───────────────────────────────────────────────────────────


def _meta_asset_ctxs() -> list:
    """A realistic Hyperliquid metaAndAssetCtxs payload (shape verified live)."""
    meta = {
        "universe": [
            {"name": "HYPE", "szDecimals": 5, "maxLeverage": 50},
            {"name": "BTC", "szDecimals": 5, "maxLeverage": 50},
        ],
    }
    ctxs = [
        {
            "markPx": "24.31",
            "prevDayPx": "23.55",
            "funding": "0.0001",  # per-8h rate == 0.01%
            "openInterest": "12834223",  # TOKEN units → x24.31 ≈ $312M notional
            "dayNtlVlm": "120000000",
        },
        {
            "markPx": "97000.5",
            "prevDayPx": "96000",
            "funding": "0.00001234",
            "openInterest": "51546",  # TOKEN units → x97000.5 ≈ $5,000M notional
            "dayNtlVlm": "3000000000",
        },
    ]
    return [meta, ctxs]


def _gmgn_pair(
    *,
    symbol: str,
    address: str,
    launchpad: str,
    created_ms: int,
    liquidity: float = 120000.0,
) -> dict:
    """A GMGN new-pair row (shape from ``GMGNProvider._fetch_new_pairs``)."""
    return {
        "address": address,
        "symbol": symbol,
        "name": f"{symbol} Token",
        "price": 0.0001,
        "liquidity": liquidity,
        "market_cap": 300000,
        "creation_timestamp": created_ms,
        "launchpad": launchpad,
    }


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """Minimal async client double for the Hyperliquid POST info call."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code
        self.last_url: str | None = None
        self.last_json: dict | None = None

    async def post(self, url: str, json: dict | None = None, **kwargs: object) -> _FakeResponse:
        self.last_url = url
        self.last_json = json
        return _FakeResponse(self._payload, self._status_code)

    async def aclose(self) -> None:
        pass


class _FakeGMGN:
    """Minimal async GMGN provider double: canned per-chain new pairs."""

    def __init__(self, pairs: dict[str, list[dict]]) -> None:
        self._pairs = pairs
        self.calls: list[tuple[str, int]] = []

    async def get_new_pairs(self, chain: str, limit: int = 50) -> list[dict]:
        self.calls.append((chain, limit))
        return list(self._pairs.get(chain, []))


def _normalized_perp(**overrides: object) -> dict:
    asset = {
        "symbol": "HYPE",
        "price": 24.31,
        "funding": 0.0001,
        "open_interest": 312_000_000.0,
        "change_24h": 3.23,
    }
    asset.update(overrides)
    return asset


# ── 1. fetch_hyperliquid_assets ────────────────────────────────────────


async def test_fetch_normalizes_real_hyperliquid_shape() -> None:
    client = _FakeClient(_meta_asset_ctxs())
    assets = await fetch_hyperliquid_assets(client=client)  # type: ignore[arg-type]
    assert len(assets) == 2
    hype = assets[0]
    assert hype["symbol"] == "HYPE"
    assert hype["price"] == pytest.approx(24.31)
    assert hype["funding"] == pytest.approx(0.0001)
    # OI converted from token units → USD notional (~12,834,223 x $24.31).
    assert hype["open_interest"] == pytest.approx(312_000_000.0, rel=1e-3)
    assert hype["change_24h"] == pytest.approx(3.23)  # ((24.31-23.55)/23.55)*100
    btc = assets[1]
    assert btc["symbol"] == "BTC"
    assert btc["open_interest"] == pytest.approx(5_000_000_000.0, rel=1e-3)
    assert btc["change_24h"] == pytest.approx(1.04)  # ((97000.5-96000)/96000)*100


async def test_fetch_posts_meta_and_asset_ctxs() -> None:
    client = _FakeClient(_meta_asset_ctxs())
    await fetch_hyperliquid_assets(client=client)  # type: ignore[arg-type]
    assert client.last_url == "https://api.hyperliquid.xyz/info"
    assert client.last_json == {"type": "metaAndAssetCtxs"}


async def test_fetch_fails_open_on_http_error() -> None:
    client = _FakeClient({}, status_code=500)
    assert await fetch_hyperliquid_assets(client=client) == []  # type: ignore[arg-type]


async def test_fetch_fails_open_on_network_error() -> None:
    client = SimpleNamespace(post=mock.AsyncMock(side_effect=RuntimeError("boom")))
    assert await fetch_hyperliquid_assets(client=client) == []  # type: ignore[arg-type]


async def test_fetch_fails_open_on_malformed_payload() -> None:
    # Non-list payload.
    assert await fetch_hyperliquid_assets(client=_FakeClient({"not": "a list"})) == []  # type: ignore[arg-type]
    # List too short (missing asset ctxs).
    assert await fetch_hyperliquid_assets(client=_FakeClient([{"universe": []}])) == []  # type: ignore[arg-type]
    # Universe entries that are not dicts are skipped, not fatal.
    payload = [{"universe": [None, {"name": "HYPE"}]}, [{"markPx": "0.5"}, {"markPx": "1.0"}]]
    assets = await fetch_hyperliquid_assets(client=_FakeClient(payload))  # type: ignore[arg-type]
    assert len(assets) == 1
    assert assets[0]["symbol"] == "HYPE"


def test_normalize_perp_asset_skips_bad_rows() -> None:
    assert normalize_perp_asset({}, {}) is None
    assert normalize_perp_asset({"name": "X"}, {}) is None  # no markPx
    assert normalize_perp_asset({"name": ""}, {"markPx": "1"}) is None
    good = normalize_perp_asset({"name": "HYPE"}, {"markPx": "24.31", "prevDayPx": "23.55"})
    assert good is not None
    assert good["price"] == pytest.approx(24.31)
    assert good["funding"] == 0.0  # missing funding defaults to 0, not None


# ── 2. fetch_sunswap_bsc_launches ──────────────────────────────────────


async def test_fetch_sunswap_bsc_merges_tron_and_bsc_launches() -> None:
    now_ms = int(time.time() * 1000)
    tron = [_gmgn_pair(symbol="SUNX", address="TAAA", launchpad="SunPump", created_ms=now_ms - 200_000)]
    bsc = [
        _gmgn_pair(symbol="MOON", address="0xbbbb", launchpad="Four.meme", created_ms=now_ms - 100_000),
        _gmgn_pair(symbol="EARLIER", address="0xcccc", launchpad="Four.meme", created_ms=now_ms - 500_000),
    ]
    gmgn = _FakeGMGN({"tron": tron, "bsc": bsc})
    launches = await fetch_sunswap_bsc_launches(client=gmgn, limit=25)
    assert gmgn.calls == [("tron", 25), ("bsc", 25)]
    assert len(launches) == 3
    # Newest first (bsc MOON @ now-100s, then tron SUNX @ now-200s, then bsc EARLIER).
    assert [launch["symbol"] for launch in launches] == ["MOON", "SUNX", "EARLIER"]
    moon = launches[0]
    assert moon["chain"] == "bsc"
    assert moon["address"] == "0xbbbb"
    assert moon["liquidity"] == pytest.approx(120000.0)
    assert moon["launchpad"] == "Four.meme"
    assert 0 <= moon["age_seconds"] <= 500
    sunx = launches[1]
    assert sunx["chain"] == "tron"
    assert sunx["launchpad"] == "SunPump"


async def test_fetch_sunswap_bsc_dedupes_same_chain_address() -> None:
    pair = _gmgn_pair(symbol="DUP", address="0xdddd", launchpad="Four.meme", created_ms=1_000_000)
    gmgn = _FakeGMGN({"bsc": [pair, dict(pair)]})
    launches = await fetch_sunswap_bsc_launches(client=gmgn)
    assert len(launches) == 1


async def test_fetch_sunswap_bsc_fails_open_on_error() -> None:
    async def boom(chain: str, limit: int = 50) -> list[dict]:
        raise RuntimeError("boom")

    client = SimpleNamespace(get_new_pairs=boom)
    assert await fetch_sunswap_bsc_launches(client=client) == []  # type: ignore[arg-type]


async def test_fetch_sunswap_bsc_fails_open_on_non_list() -> None:
    async def weird(chain: str, limit: int = 50) -> object:
        return {"not": "a list"}

    client = SimpleNamespace(get_new_pairs=weird)
    assert await fetch_sunswap_bsc_launches(client=client) == []  # type: ignore[arg-type]


def test_normalize_launch_skips_bad_rows() -> None:
    assert normalize_launch({}, "bsc") is None
    assert normalize_launch({"address": "0x1"}, "tron") is None  # no symbol
    assert normalize_launch({"symbol": "X"}, "tron") is None  # no address
    good = normalize_launch(
        _gmgn_pair(symbol="OK", address="0x9", launchpad="Four.meme", created_ms=1_000_000),
        "bsc",
    )
    assert good is not None
    assert good["chain"] == "bsc"


def test_age_seconds_deterministic_with_now_ts() -> None:
    assert _age_seconds(None, now_ts=2_000_000) == 0
    assert _age_seconds("bad", now_ts=2_000_000) == 0
    assert _age_seconds(1_900_000, now_ts=2_000_000) == 100
    # Future timestamps clamp to 0 (clock skew), never negative.
    assert _age_seconds(2_100_000, now_ts=2_000_000) == 0


# ── 3. format_hyperliquid_assets ───────────────────────────────────────


def test_format_renders_numbered_lines_sorted_by_oi() -> None:
    assets = [
        _normalized_perp(
            symbol="BTC", price=97_000.5, funding=0.00001234, open_interest=5_000_000_000.0, change_24h=1.04
        ),
        _normalized_perp(),
    ]
    text = format_hyperliquid_assets(assets, top_n=8)
    lines = text.splitlines()
    assert len(lines) == 2
    # BTC has the larger OI → first.
    assert "1. 📈 BTC — $97,000.5 · funding +0.0012% · OI $5,000M · +1.0%" in lines[0]
    assert "2. 📈 HYPE — $24.31 · funding +0.01% · OI $312M · +3.2%" in lines[1]
    assert text.index("1.") < text.index("2.")


def test_format_caps_top_n() -> None:
    assets = [_normalized_perp(symbol=f"A{i}", open_interest=float(i)) for i in range(10)]
    text = format_hyperliquid_assets(assets, top_n=3)
    assert len(text.splitlines()) == 3


def test_format_empty() -> None:
    assert format_hyperliquid_assets([]) == ""


def test_format_handles_missing_fields() -> None:
    text = format_hyperliquid_assets([_normalized_perp(price=None, funding=0.0, open_interest=500.0, change_24h=None)])
    assert "— ? · funding +0.00% · OI $500 · ?%" in text


# ── 4. Chat intent: perp ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("show me hyperliquid perps", "perp"),
        ("hyperliquid funding rate", "perp"),
        ("what's the open interest on HYPE", "perp"),
        ("perp market overview", "perp"),
        ("hyperliquid market", "perp"),  # perp beats generic market
        ("$HYPE", "price"),  # HYPE token price stays price
        ("price of $HYPE", "price"),
        ("how's the market", "market"),
        ("interesting prediction markets", "prediction"),
        ("bet on hyperliquid", "prediction"),  # prediction checked before perp
        ("what is a honeypot", None),
        ("hello there", None),
        ("", None),
    ],
)
def test_detect_tool_intent_perp(message: str, expected: str | None) -> None:
    assert _detect_tool_intent(message) == expected


# ── 5. _exec_perp ──────────────────────────────────────────────────────


async def test_exec_perp_renders_from_mocked_fetch() -> None:
    assets = [_normalized_perp()]
    with (
        mock.patch.object(perp_markets, "fetch_hyperliquid_assets", new=mock.AsyncMock(return_value=assets)),
    ):
        text = await _exec_perp("hyperliquid funding")
    assert text is not None
    assert "📈 HYPE" in text
    assert "funding +0.01%" in text
    assert "OI $312M" in text


async def test_exec_perp_returns_none_on_empty() -> None:
    with (
        mock.patch.object(perp_markets, "fetch_hyperliquid_assets", new=mock.AsyncMock(return_value=[])),
    ):
        assert await _exec_perp("perps") is None


async def test_exec_perp_fails_open_on_error() -> None:
    with (
        mock.patch.object(
            perp_markets, "fetch_hyperliquid_assets", new=mock.AsyncMock(side_effect=RuntimeError("boom"))
        ),
    ):
        assert await _exec_perp("perps") is None


def test_perp_wired_into_executors() -> None:
    assert "perp" in chat_tools._EXECUTORS
    assert chat_tools._EXECUTORS["perp"] is _exec_perp


# ── 6. Helper / fetch edge cases ─────────────────────────────────────────


def test_to_float_dict_funding_shape() -> None:
    # Hyperliquid occasionally nests funding under a dict.
    assert _to_float({"funding": "0.0001"}) == 0.0001
    assert _to_float({"funding": None, "value": "0.5"}) == 0.5
    assert _to_float({"funding": None, "value": None}) is None
    assert _to_float("nan") is None
    assert _to_float([1]) is None


def test_normalize_launch_rejects_non_dict() -> None:
    assert normalize_launch(None, "bsc") is None
    assert normalize_launch("not-a-dict", "tron") is None


def test_default_gmgn_provider_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = object()
    monkeypatch.setattr("app.databus.gmgn_provider.get_gmgn_provider", lambda: fake)
    assert _default_gmgn_provider() is fake


async def test_fetch_hyperliquid_creates_and_closes_own_client() -> None:
    """Without an injected client: own httpx client created, used, closed."""
    fake_client = _FakeClient(_meta_asset_ctxs())
    with mock.patch.object(perp_markets.httpx, "AsyncClient", return_value=fake_client):
        assets = await fetch_hyperliquid_assets()
    assert len(assets) == 2
    assert fake_client.last_url == "https://api.hyperliquid.xyz/info"


async def test_fetch_hyperliquid_fails_open_on_malformed_pair() -> None:
    # payload[0] not a dict.
    assert await fetch_hyperliquid_assets(client=_FakeClient(["not-meta", "not-ctxs"])) == []  # type: ignore[arg-type]
    # payload[1] not a list.
    assert await fetch_hyperliquid_assets(client=_FakeClient([{"universe": []}, "not-ctxs"])) == []  # type: ignore[arg-type]


async def test_fetch_sunswap_skips_junk_rows() -> None:
    pairs = [
        None,
        {"symbol": "NOPE"},  # no address → skipped
        {"address": "0x1", "symbol": "OK", "launchpad": "Four.meme", "creation_timestamp": 1_000_000},
    ]
    gmgn = _FakeGMGN({"bsc": pairs, "tron": []})
    launches = await fetch_sunswap_bsc_launches(client=gmgn)
    assert len(launches) == 1
    assert launches[0]["symbol"] == "OK"


def test_format_price_sub_one_dollar() -> None:
    assert _format_price(0.5) == "$0.5000"
    assert _format_price(0.0001) == "$0.0001"
    assert _format_price(24.31) == "$24.31"
    assert _format_price(1234.5) == "$1,234.5"
    assert _format_price(None) == "?"
