"""Per-asset aggregation for the cross-venue liquidation pressure map.

Pure helpers only: the route in ``liqmap_map`` gathers the venue snapshots and
hands them here. The aggregation is the part with real branching, so it lives
in its own module and the route stays a thin orchestrator.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

Assets = dict[str, dict[str, Any]]
Rows = list[dict[str, Any]]

_SYMBOL_RE = re.compile(r"[A-Z]{2,6}")


def _snapshot(value: Any) -> dict[str, Any]:
    """A gathered venue result is a dict or an exception (gather ran with return_exceptions)."""
    return value if isinstance(value, dict) else {}


def _add_hyperliquid(assets: Assets, market: Mapping[str, Any]) -> None:
    """Book Hyperliquid's own market row (OI + funding + mark) against its asset."""
    sym = str(market.get("asset") or market.get("symbol") or "").upper()
    if not sym:
        return
    oi = float(market.get("open_interest_usd") or 0)
    row = assets.setdefault(sym, {"venues": {}, "total_oi_usd": 0.0})
    row["venues"]["Hyperliquid"] = {
        "oi_usd": round(oi),
        "funding": market.get("funding_rate"),
        "mark": market.get("mark_price"),
    }
    row["total_oi_usd"] += oi
    row.setdefault("marks", []).append(float(market.get("mark_price") or 0))


def _add_venue_snapshot(assets: Assets, venue: str, snapshot: Mapping[str, Any]) -> None:
    """Book one venue's snapshot {symbol: values} against the assets it covers."""
    for sym, values in snapshot.items():
        oi = float(values.get("oi_usd") or 0)
        row = assets.setdefault(sym, {"venues": {}, "total_oi_usd": 0.0})
        row["venues"][venue] = {
            "oi_usd": round(oi),
            "funding": values.get("funding"),
            "mark": values.get("mark"),
        }
        row["total_oi_usd"] += oi
        if values.get("mark"):
            row.setdefault("marks", []).append(float(values["mark"]))


def collect_assets(
    hl_markets: Iterable[Mapping[str, Any]] | None,
    venue_snapshots: Iterable[tuple[str, Any]],
) -> Assets:
    """Aggregate every venue's OI into per-asset rows."""
    assets: Assets = {}
    for market in hl_markets or []:
        _add_hyperliquid(assets, market)
    for venue, snapshot in venue_snapshots:
        _add_venue_snapshot(assets, venue, _snapshot(snapshot))
    return assets


def merge_xbt_into_btc(assets: Assets) -> None:
    """XBT is legacy BTC naming (Bitget/KuCoin): fold it into BTC."""
    if "XBT" not in assets or "BTC" not in assets:
        return
    btc = assets["BTC"]
    xbt = assets.pop("XBT")
    btc["total_oi_usd"] += xbt["total_oi_usd"]
    for venue, values in xbt["venues"].items():
        if venue not in btc["venues"]:
            btc["venues"][venue] = values
            btc["total_oi_usd"] = btc.get("total_oi_usd", 0)
    btc.setdefault("marks", []).extend(xbt.get("marks", []))


def _row_for(sym: str, row: Mapping[str, Any], marks: list[float]) -> dict[str, Any]:
    """One map row: weighted mark, 10x liquidation levels and funding spread."""
    mark = sum(marks) / len(marks)
    long10 = mark * (1 - 1 / 10)
    short10 = mark * (1 + 1 / 10)
    fundings = [venue["funding"] for venue in row["venues"].values() if isinstance(venue.get("funding"), (int, float))]
    return {
        "asset": sym,
        "total_oi_usd": round(row["total_oi_usd"]),
        "venue_count": len(row["venues"]),
        "venues": row["venues"],
        "weighted_mark": round(mark, 6 if mark < 1 else 2),
        "long_liq_10x": round(long10, 6 if mark < 1 else 2),
        "short_liq_10x": round(short10, 6 if mark < 1 else 2),
        "funding_spread": round(max(fundings) - min(fundings), 6) if len(fundings) > 1 else None,
        "pressure": round(row["total_oi_usd"]),
    }


def build_rows(assets: Assets) -> Rows:
    """One row per asset that has both marks and OI; indices and malformed keys are skipped."""
    rows: Rows = []
    for sym, row in assets.items():
        if not _SYMBOL_RE.fullmatch(sym):
            continue  # skip indices (NAS100), dated pairs, malformed keys
        marks = [m for m in row.get("marks", []) if m > 0]
        if not marks or row["total_oi_usd"] <= 0:
            continue
        rows.append(_row_for(sym, row, marks))
    return rows


def rank_rows(rows: Rows) -> tuple[Rows, int]:
    """Rank by cascade pressure and attach each row's share of the total OI."""
    rows.sort(key=lambda x: x["pressure"], reverse=True)
    total = sum(r["total_oi_usd"] for r in rows) or 1
    for row in rows:
        row["oi_share_pct"] = round(row["total_oi_usd"] / total * 100, 2)
    return rows, total
