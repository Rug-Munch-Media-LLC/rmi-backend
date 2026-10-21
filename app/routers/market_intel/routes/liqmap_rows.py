"""Shared parsing for venue liquidation rows.

Every venue reports a symbol, an open interest, a funding rate and a mark
price under its own key names, and every venue has at least one row that does
not parse. One collector plus one mapper per venue keeps that rule in one
place: a malformed row is skipped, never fatal, and one malformed row never
loses the venue.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

Row = tuple[str, dict[str, Any]]


def collect_rows(
    rows: Iterable[Mapping[str, Any]] | None,
    mapper: Any,
) -> dict[str, dict[str, Any]]:
    """Map venue rows into {symbol: values}; unparseable rows are skipped."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        try:
            parsed = mapper(row)
        except (TypeError, ValueError):
            continue
        if not parsed:
            continue
        sym, values = parsed
        out.setdefault(sym, values)
    return out


def _bybit_row(row: Mapping[str, Any]) -> Row | None:
    """Bybit v5 ticker row -> symbol + OI/funding/mark."""
    sym = str(row.get("symbol") or "").replace("USDT", "").replace("USDC", "")
    if not sym:
        return None
    return sym, {
        "oi_usd": float(row.get("open_interest") or 0) * float(row.get("markPrice") or 0),
        "funding": float(row.get("fundingRate") or 0),
        "mark": float(row.get("markPrice") or 0),
    }


def _binance_row(row: Mapping[str, Any]) -> Row | None:
    """Binance premiumIndex row -> symbol + mark/funding (OI comes from other venues)."""
    sym = str(row.get("symbol") or "").replace("USDT", "").replace("USDC", "").replace("PERP", "")
    if not sym:
        return None
    return sym, {
        "oi_usd": 0.0,
        "funding": float(row.get("lastFundingRate") or 0),
        "mark": float(row.get("markPrice") or 0),
    }


def _okx_row(row: Mapping[str, Any]) -> Row | None:
    """OKX v5 open-interest row -> symbol + OI/mark (no funding on this endpoint)."""
    inst = str(row.get("instId") or "")  # e.g. BTC-USDT-SWAP
    sym = inst.split("-")[0] if inst else ""
    if not sym:
        return None
    return sym, {
        "oi_usd": float(row.get("oiUsd") or 0),
        "funding": None,
        "mark": float(row.get("instPx") or 0) or None,
    }


def _bitget_row(row: Mapping[str, Any]) -> Row | None:
    """Bitget v2 ticker row -> symbol + OI(USD)/funding/mark."""
    sym = str(row.get("symbol") or "").replace("USDT", "")
    if not sym:
        return None
    return sym, {
        "oi_usd": float(row.get("holdingAmount") or 0) * float(row.get("markPrice") or 0),
        "funding": float(row.get("fundingRate") or 0),
        "mark": float(row.get("markPrice") or 0),
    }


def _gate_row(row: Mapping[str, Any]) -> Row | None:
    """Gate.io v4 contract row -> symbol + OI(USD)/funding/mark."""
    sym = str(row.get("name") or "").replace("_USDT", "").replace("_USDC", "")
    if not sym:
        return None
    mark = float(row.get("mark_price") or 0)
    return sym, {
        "oi_usd": float(row.get("open_interest_usd") or 0),
        "funding": float(row.get("funding_rate") or 0),
        "mark": mark,
    }


def _mexc_row(row: Mapping[str, Any]) -> Row | None:
    """MEXC contract ticker row -> symbol + funding/mark.

    holdVol is in CONTRACTS (size varies per market) -- using it as coin OI
    inflated BTC to $48T. Funding + mark only until contract sizes come from
    /detail.
    """
    sym = str(row.get("symbol") or "").replace("_USDT", "")
    if not sym:
        return None
    mark = float(row.get("lastPrice") or 0)
    return sym, {
        "oi_usd": 0.0,
        "funding": float(row.get("fundingRate") or 0),
        "mark": mark,
    }


def _kucoin_row(row: Mapping[str, Any]) -> Row | None:
    """KuCoin active-contract row -> symbol + OI(USD)/funding/mark."""
    sym = str(row.get("baseCurrency") or "")
    if not sym:
        return None
    mark = float(row.get("markPrice") or 0)
    oi_coins = float(row.get("openInterest") or 0)  # in coins
    lot = float(row.get("lotSize") or 1) or 1.0
    multiplier = float(row.get("multiplier") or 1) or 1.0
    return sym, {
        "oi_usd": oi_coins * mark * multiplier * lot,
        "funding": float(row.get("fundingFeeRate") or 0),
        "mark": mark,
    }
