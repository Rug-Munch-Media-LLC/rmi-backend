"""Whale / smart-money flow enrichment for the RugMunchBot scan.

Computes compact flow signals so the scan can flag *"whales exiting"* /
*"smart money bought in"*:

    ``whale_flow(address, chain)`` → ::

        {
            "net_flow": float | None,        # whale buy-sell net (USD), sign = direction
            "whale_tx_count": int | None,    # whale-size transfers observed in the window
            "smart_money_bought": bool | None,
            "smart_money_sold": bool | None,
            "evidence": str | None,          # human-readable source summary
        }

    ``whale_signal(result)`` → ``"whales net selling"`` | ``"smart money
    bought in"`` | ``"smart money exiting"`` | ``None`` (pure renderer).

Fail-open by design: any fetcher error (provider down, bad payload, timeout)
yields ``None`` fields — never raises, never blocks the scan it decorates.

Fetchers are injectable test seams with real lazy defaults:

* ``whale_fetcher`` — recent whale buy/sell net flow for the token.
  Default ``fetch_whale_flow_clickhouse`` reads *own* indexed transfers from
  ClickHouse ``wallet_memory.token_transfers`` (the same table the
  ``WhaleMoveProvider`` polls and ``whale_indexer`` backfills from). Raw
  transfers are conservation-neutral, so direction (``net_flow``) is NOT
  derivable from them — the default reports ``whale_tx_count`` + evidence
  and leaves ``net_flow`` None; wire a directional fetcher (e.g.
  chain-indexer labeled flow) to drive the "whales net selling" signal.
* ``smart_money_fetcher`` — smart-money buy/sell direction. Default
  ``fetch_smart_money_gmgn`` calls ``GMGNProvider.get_smart_money`` (real
  public API) and derives direction from activity rows (same convention as
  ``gmgn_enrich``: net of buy/sell counts, ``buy_pressure`` fallback).

Self-contained: no network/DB work happens at import time — the defaults
lazy-import providers inside the function bodies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domains.scanner.engines._logging import get_logger

log = get_logger(__name__)

# Whale window / threshold — mirror WhaleMoveProvider.DEFAULT_MIN_VALUE so the
# ClickHouse query only counts transfers the scanner itself classifies as
# "whale" moves.
_WHALE_WINDOW_HOURS = 24
_WHALE_MIN_VALUE_USD = 10_000.0

# Chain aliases tolerated when matching ClickHouse ``chain_id`` values.
_CHAIN_ALIASES = {
    "solana": "sol",
    "ethereum": "eth",
    "mainnet": "eth",
    "binance": "bsc",
    "bnb": "bsc",
    "matic": "polygon",
    "arb": "arbitrum",
    "op": "optimism",
    "avalanche": "avax",
    "trx": "tron",
}

# Injectable fetcher signatures.
#   whale_fetcher(address, chain) -> {"net_flow": float|None, "whale_tx_count": int|None,
#                                     "evidence": str|None}
#   smart_money_fetcher(address, chain) -> {"bought": bool|None, "sold": bool|None,
#                                           "evidence": str|None}
WhaleFlowFetcher = Callable[[str, str], Awaitable[dict[str, Any]]]
SmartMoneyFetcher = Callable[[str, str], Awaitable[dict[str, Any]]]


# ── Helpers ────────────────────────────────────────────────────────────────


def _to_float(value: Any) -> float | None:
    """Coerce a value to float, returning None for missing/unparseable."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _chain_candidates(chain: str) -> list[str]:
    """Chain values to try when matching ClickHouse ``chain_id`` (exact + alias)."""
    chain = (chain or "").strip().lower()
    if not chain:
        return []
    candidates = [chain, _CHAIN_ALIASES.get(chain)]
    return sorted({c for c in candidates if c})


# ── Default fetchers (real sources, lazy imports, fail-open) ───────────────


async def fetch_whale_flow_clickhouse(address: str, chain: str) -> dict[str, Any]:
    """Default whale fetcher: whale transfers for the token from own ClickHouse.

    Returns ``{"net_flow": None, "whale_tx_count": N, "evidence": ...}``.

    ``net_flow`` (direction) is not derivable from raw transfer rows — token
    transfers are conservation-neutral (every outflow is an inflow), and the
    ClickHouse ``transfer_type`` column records the *asset type* (erc20/spl),
    not buy/sell. Count + evidence only; inject a directional fetcher when the
    "whales net selling" flag is needed.
    """
    if not address or not chain:
        return {"net_flow": None, "whale_tx_count": None, "evidence": ""}
    try:
        from app.domains.scanner.webhooks.providers.utils import get_clickhouse

        ch = get_clickhouse()
        if ch is None:
            return {"net_flow": None, "whale_tx_count": None, "evidence": "clickhouse unavailable"}
        since = (datetime.now(UTC) - timedelta(hours=_WHALE_WINDOW_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
        rows: list[Any] = []
        for chain_id in _chain_candidates(chain):
            rows = ch.execute(
                """
                SELECT from_address, to_address, value_decimal, block_timestamp
                FROM wallet_memory.token_transfers
                WHERE token_address = %(addr)s
                  AND chain_id = %(chain)s
                  AND block_timestamp > %(since)s
                  AND value_decimal > %(min_val)s
                ORDER BY block_timestamp DESC
                LIMIT 200
                """,
                {"addr": address, "chain": chain_id, "since": since, "min_val": _WHALE_MIN_VALUE_USD},
            )
            if rows:
                break
        count = len(rows)
        evidence = (
            f"{count} whale transfer(s) >${_WHALE_MIN_VALUE_USD:,.0f} in last {_WHALE_WINDOW_HOURS}h"
            if count
            else "no whale transfers in window"
        )
        return {"net_flow": None, "whale_tx_count": count, "evidence": evidence}
    except Exception as exc:
        log.warning("whale_flow_fetch_failed error=%s", exc)
        return {"net_flow": None, "whale_tx_count": None, "evidence": ""}


def _smart_money_direction(sm_data: dict[str, Any]) -> tuple[bool | None, bool | None, str]:
    """Derive bought/sold direction from a GMGN smart-money payload.

    Net = Σ(buy - sell) over ``smart_money_activity`` rows (same convention as
    ``gmgn_enrich._net_from_activity``); falls back to ``buy_pressure`` when
    rows carry no counts, then to top-level ``buy_7d``/``sell_7d``. Returns
    ``(bought, sold, evidence)`` — both None when no direction data exists.
    """
    net = 0.0
    saw_rows = False
    activity = sm_data.get("smart_money_activity") or []
    if isinstance(activity, list):
        for row in activity:
            if not isinstance(row, dict):
                continue
            buys = _to_float(row.get("buy_7d", row.get("buy_count", row.get("buy_tx_count"))))
            sells = _to_float(row.get("sell_7d", row.get("sell_count", row.get("sell_tx_count"))))
            if buys is not None and sells is not None:
                net += buys - sells
                saw_rows = True
    if not saw_rows:
        bp = _to_float(sm_data.get("buy_pressure"))
        if bp is not None:
            net = bp
        else:
            b7 = _to_float(sm_data.get("buy_7d"))
            s7 = _to_float(sm_data.get("sell_7d"))
            if b7 is None or s7 is None:
                return None, None, "no smart-money direction data"
            net = b7 - s7
    bought = net > 0
    sold = net < 0
    evidence = f"smart money net {net:+.0f} (7d)" if net else "smart money flat (7d)"
    return bought, sold, evidence


async def fetch_smart_money_gmgn(address: str, chain: str) -> dict[str, Any]:
    """Default smart-money fetcher: GMGN public API via ``get_gmgn_provider()``.

    Returns ``{"bought": bool|None, "sold": bool|None, "evidence": str|None}``.
    """
    if not address or not chain:
        return {"bought": None, "sold": None, "evidence": ""}
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        sm = await get_gmgn_provider().get_smart_money(address, chain)
        if not isinstance(sm, dict) or sm.get("source") == "none":
            return {"bought": None, "sold": None, "evidence": "gmgn unavailable"}
        bought, sold, evidence = _smart_money_direction(sm)
        return {"bought": bought, "sold": sold, "evidence": evidence}
    except Exception as exc:
        log.warning("smart_money_fetch_failed error=%s", exc)
        return {"bought": None, "sold": None, "evidence": ""}


# ── Composition ─────────────────────────────────────────────────────────────


def _compose(whale_data: Any, sm_data: Any) -> dict[str, Any]:
    """Merge fetcher outputs into the public ``whale_flow`` result shape."""
    whale = whale_data if isinstance(whale_data, dict) else {}
    sm = sm_data if isinstance(sm_data, dict) else {}

    net_flow = _to_float(whale.get("net_flow"))
    raw_count = whale.get("whale_tx_count")
    whale_tx_count = int(raw_count) if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool) else None

    bought = sm.get("bought")
    sold = sm.get("sold")
    smart_money_bought = bought if isinstance(bought, bool) else None
    smart_money_sold = sold if isinstance(sold, bool) else None

    evidence_parts: list[str] = []
    if whale.get("evidence"):
        evidence_parts.append(str(whale["evidence"]))
    if sm.get("evidence"):
        evidence_parts.append(str(sm["evidence"]))

    return {
        "net_flow": net_flow,
        "whale_tx_count": whale_tx_count,
        "smart_money_bought": smart_money_bought,
        "smart_money_sold": smart_money_sold,
        "evidence": " | ".join(evidence_parts) if evidence_parts else None,
    }


# ── Public API ──────────────────────────────────────────────────────────────


async def whale_flow(
    address: str,
    chain: str,
    *,
    whale_fetcher: WhaleFlowFetcher | None = None,
    smart_money_fetcher: SmartMoneyFetcher | None = None,
) -> dict[str, Any]:
    """Compute whale/smart-money flow signals for a token (fail-open).

    Returns the public shape above with every field ``None`` on error — never
    raises. ``whale_fetcher`` / ``smart_money_fetcher`` are injectable async
    callables ``(address, chain) -> dict``; defaults are the real lazy
    fetchers in this module.
    """
    empty = {
        "net_flow": None,
        "whale_tx_count": None,
        "smart_money_bought": None,
        "smart_money_sold": None,
        "evidence": None,
    }
    if not address or not chain:
        return empty
    try:
        wf = whale_fetcher or fetch_whale_flow_clickhouse
        smf = smart_money_fetcher or fetch_smart_money_gmgn
        whale_data: Any
        sm_data: Any
        whale_data, sm_data = await asyncio.gather(wf(address, chain), smf(address, chain), return_exceptions=True)
        if isinstance(whale_data, BaseException):
            whale_data = None
        if isinstance(sm_data, BaseException):
            sm_data = None
        return _compose(whale_data, sm_data)
    except Exception as exc:
        log.warning("whale_flow_failed address=%s chain=%s error=%s", address, chain, exc)
        return empty


def whale_signal(result: dict[str, Any] | None) -> str | None:
    """Pure renderer: map a ``whale_flow`` result to a scan flag string.

    Priority (task-defined order — a raw whale sell is the loudest flag):

        1. ``net_flow < 0``          → "whales net selling"
        2. ``smart_money_bought``    → "smart money bought in"
        3. ``smart_money_sold``      → "smart money exiting"
        else                         → None

    Treats missing data (``None`` fields) as no signal.
    """
    if not isinstance(result, dict):
        return None
    net = _to_float(result.get("net_flow"))
    if net is not None and net < 0:
        return "whales net selling"
    if result.get("smart_money_bought") is True:
        return "smart money bought in"
    if result.get("smart_money_sold") is True:
        return "smart money exiting"
    return None


__all__ = [
    "SmartMoneyFetcher",
    "WhaleFlowFetcher",
    "fetch_smart_money_gmgn",
    "fetch_whale_flow_clickhouse",
    "whale_flow",
    "whale_signal",
]
