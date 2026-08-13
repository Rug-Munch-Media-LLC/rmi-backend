"""Perp-market + cross-chain launch module — Hyperliquid & GMGN (Tron/BSC).

Surfaces Hyperliquid perpetual-futures data (price, funding, open interest,
24h change) and recent SunSwap (Tron) / Four.meme (BSC) token launches for the
RugMunchBot chat layer (``app/domains/chat_tools.py`` ``_exec_perp``).

Data sources
------------
Hyperliquid public info API (no auth, no key):
    POST https://api.hyperliquid.xyz/info  {"type": "metaAndAssetCtxs"}
The response is ``[meta, assetCtxs]``: ``meta.universe`` lists the perp assets
(``name``) and ``assetCtxs`` is a parallel list of per-asset context dicts
(``markPx``, ``prevDayPx``, ``funding``, ``openInterest``). Each normalized
asset dict is ``{symbol, price, funding, open_interest, change_24h}`` where
``funding`` is the raw per-8h rate (0.0001 == 0.01%) and ``open_interest`` is
USD notional. No auth.

Tron/BSC launches via GMGN (free public API, no key):
    ``GMGNProvider.get_new_pairs("tron")`` / ``get_new_pairs("bsc")``
GMGN's ``/defi/quotation/v1/rank/{chain}/pump`` rank endpoint covers the
tron/bsc chain slugs (see ``app/databus/gmgn_provider.py`` ``_normalize_chain``):
Tron new pairs are SunPump/SunSwap launches, BSC new pairs are Four.meme /
pancake launches. GMGN is Cloudflare-protected from some egress IPs (observed
403 from this network); the GMGN provider's own fallback chain and this
module's fail-open ``[]`` keep the bot functional when GMGN is unreachable.

All fetch functions are fail-open: they never raise, returning ``[]`` on any
network/HTTP/parse error so the chat executor can fall through to RAG/LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# Timeout for the whole Hyperliquid fetch (the chat dispatcher wraps executors
# in an 8s cap, so keep this comfortably under it).
_FETCH_TIMEOUT = 6.0

# GMGN chain slugs for the SunSwap (Tron) and Four.meme (BSC) launch feeds.
_TRON_CHAIN = "tron"
_BSC_CHAIN = "bsc"
_LAUNCH_FETCH_LIMIT = 25


def _to_float(value: Any) -> float | None:
    """Best-effort float conversion (NaN guard, dict-safe for funding)."""
    if isinstance(value, dict):
        # Hyperliquid occasionally nests funding under {"funding": ...}.
        value = value.get("funding") or value.get("value")
    if value is None:
        return None
    try:
        f = float(value)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def _age_seconds(created_at: Any, now_ts: int | None = None) -> int:
    """Age of a launch in seconds from a ms-epoch timestamp; 0 when unknown."""
    ts = _to_float(created_at)
    if ts is None or ts <= 0:
        return 0
    now_ms = now_ts if now_ts is not None else int(datetime.now(UTC).timestamp() * 1000)
    return max(0, int((now_ms - ts) / 1000))


def normalize_perp_asset(meta_asset: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one Hyperliquid perp (universe entry + asset ctx) pair.

    Returns ``{symbol, price, funding, open_interest, change_24h}`` or None
    when the asset has no symbol or no usable mark price. ``funding`` is the
    raw per-8h rate (0.0001 == 0.01%). Hyperliquid reports ``openInterest`` in
    TOKEN units — we convert to USD notional (``oi_tokens * mark_px``) so the
    formatter's ``OI $xM`` is meaningful (verified live: BTC 40,229 tokens x
    $63,562 ≈ $2.56B).
    """
    if not isinstance(meta_asset, dict) or not isinstance(ctx, dict):
        return None
    symbol = str(meta_asset.get("name") or "").strip()
    if not symbol:
        return None
    mark_px = _to_float(ctx.get("markPx"))
    if mark_px is None:
        return None
    prev_day_px = _to_float(ctx.get("prevDayPx"))
    change_24h = ((mark_px - prev_day_px) / prev_day_px * 100.0) if prev_day_px and prev_day_px > 0 else 0.0
    oi_tokens = _to_float(ctx.get("openInterest")) or 0.0
    return {
        "symbol": symbol,
        "price": mark_px,
        "funding": _to_float(ctx.get("funding")) or 0.0,
        "open_interest": round(oi_tokens * mark_px, 2),
        "change_24h": round(change_24h, 2),
    }


def normalize_launch(pair: dict[str, Any], chain: str) -> dict[str, Any] | None:
    """Normalize a GMGN new-pair row to the launch shape.

    Returns ``{symbol, chain, address, liquidity, age_seconds, launchpad}``
    (plus ``created_at`` for ordering) or None when address/symbol are missing.
    """
    if not isinstance(pair, dict):
        return None
    address = str(pair.get("address") or "").strip()
    symbol = str(pair.get("symbol") or "").strip()
    if not address or not symbol:
        return None
    return {
        "symbol": symbol,
        "chain": chain,
        "address": address,
        "liquidity": _to_float(pair.get("liquidity")) or 0.0,
        "age_seconds": _age_seconds(pair.get("creation_timestamp")),
        "created_at": pair.get("creation_timestamp"),
        "launchpad": str(pair.get("launchpad") or "").strip(),
        "source": "gmgn",
    }


def _default_gmgn_provider() -> Any:
    """Lazy GMGN singleton — keeps this module importable without GMGN."""
    from app.databus.gmgn_provider import get_gmgn_provider

    return get_gmgn_provider()


async def fetch_hyperliquid_assets(
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch all Hyperliquid perp assets (price/funding/OI/24h change).

    Fail-open: returns ``[]`` on any network/HTTP/parse error. Pass ``client``
    to inject a mock/preconfigured ``httpx.AsyncClient`` (tests); otherwise a
    short-lived client is created and closed internally. No auth.
    """
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
    try:
        resp = await client.post(
            HYPERLIQUID_INFO_URL,
            json={"type": "metaAndAssetCtxs"},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            logger.debug("hyperliquid_http", status=resp.status_code)
            return []
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        meta, ctxs = payload[0], payload[1]
        if not isinstance(meta, dict) or not isinstance(ctxs, list):
            return []
        universe = meta.get("universe") or []
        assets: list[dict[str, Any]] = []
        for i, asset in enumerate(universe):
            ctx = ctxs[i] if i < len(ctxs) and isinstance(ctxs[i], dict) else {}
            normalized = normalize_perp_asset(asset, ctx)
            if normalized is not None:
                assets.append(normalized)
        return assets
    except Exception as e:  # fail-open: never raise to chat
        logger.debug("hyperliquid_fetch_failed", error=str(e)[:120])
        return []
    finally:
        if own_client:
            await client.aclose()


async def fetch_sunswap_bsc_launches(
    client: Any = None,
    limit: int = _LAUNCH_FETCH_LIMIT,
) -> list[dict[str, Any]]:
    """Recent SunSwap (Tron) + Four.meme (BSC) launches via GMGN.

    Pulls ``get_new_pairs`` for the tron and bsc chain slugs and merges them
    into the launch shape ``{symbol, chain, address, liquidity, age_seconds,
    launchpad}``. ``client`` is duck-typed: anything exposing an async
    ``get_new_pairs(chain, limit)`` — defaults to the GMGN singleton. Fail-open
    → ``[]``. No network calls when a mock client is injected.
    """
    provider = client if client is not None else _default_gmgn_provider()
    try:
        launches: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for chain in (_TRON_CHAIN, _BSC_CHAIN):
            raw = await provider.get_new_pairs(chain, limit=limit)
            if not isinstance(raw, list):
                continue
            for p in raw:
                if not isinstance(p, dict):
                    continue
                address = str(p.get("address") or "").strip()
                if not address:
                    continue
                key = (chain, address)
                if key in seen:
                    continue
                seen.add(key)
                normalized = normalize_launch(p, chain)
                if normalized is not None:
                    launches.append(normalized)
        # Newest first: sort by created_at desc (unknowns last).
        launches.sort(key=lambda launch: _to_float(launch.get("created_at")) or 0.0, reverse=True)
        return launches
    except Exception as e:  # fail-open: never raise to chat
        logger.debug("sunswap_bsc_launches_failed", error=str(e)[:120])
        return []


def _format_price(price: float | None) -> str:
    """Compact USD price: one decimal ≥$1k, two decimals ≥$1, four below."""
    if price is None:
        return "?"
    if price >= 1000:
        return f"${price:,.1f}"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.4f}"


def _format_funding(funding: float) -> str:
    """Raw per-8h rate → signed percentage (0.0001 → '+0.01%')."""
    pct = funding * 100.0
    if pct == 0.0:
        return "+0.00%"
    if abs(pct) >= 0.005:
        return f"{pct:+.2f}%"
    return f"{pct:+.4f}%"


def format_hyperliquid_assets(assets: list[dict[str, Any]], top_n: int = 8) -> str:
    """Render a numbered HYPE perp list.

    One line per asset: ``📈 HYPE — $24.3 · funding +0.01% · OI $312M · +3.2%``.
    Sorted by open interest (biggest perp markets first), capped at ``top_n``.
    Returns ``""`` for an empty input.
    """
    if not assets:
        return ""
    valid = [a for a in assets if isinstance(a, dict)]
    valid.sort(key=lambda a: float(a.get("open_interest") or 0.0), reverse=True)
    lines: list[str] = []
    for i, asset in enumerate(valid[:top_n], start=1):
        symbol = str(asset.get("symbol") or "?").strip()
        price = _format_price(_to_float(asset.get("price")))
        funding = _format_funding(_to_float(asset.get("funding")) or 0.0)
        oi = _to_float(asset.get("open_interest")) or 0.0
        oi_str = f"${oi / 1_000_000:,.0f}M" if oi >= 1_000_000 else f"${oi:,.0f}"
        change = _to_float(asset.get("change_24h"))
        change_str = f"{change:+.1f}%" if change is not None else "?%"
        lines.append(f"{i}. 📈 {symbol} — {price} · funding {funding} · OI {oi_str} · {change_str}")
    return "\n".join(lines)
