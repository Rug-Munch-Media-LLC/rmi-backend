"""GET /api/v1/trending/per-chain — one ranked board per chain, components shown.

Unlike ``/trending`` (one global ranking with an optional chain filter), this
returns an independent board per chain, each ranked from its own candidate
pool, with every score component and every guard visible so a demotion can be
audited. Paid placements come back in a separate ``promoted`` array; they never
occupy a position in ``boards[].items``.

Fail-open per chain: a chain whose query fails is named in ``degraded`` with
its error class and the rest of the boards still render.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.trending.per_chain import fetch_chain_rows
from app.trending.per_chain_score import score_components
from app.trending.promoted import load_slots, select_promoted

router = APIRouter(prefix="/trending", tags=["trending"])

PER_CHAIN_WINDOWS = (1, 6, 24)
DEFAULT_CHAINS = ("ethereum", "bsc", "arbitrum", "polygon", "solana")
CANDIDATES = 120


def _item(row: dict[str, Any]) -> dict[str, Any]:
    scored = score_components(row)
    return {
        "chain": row.get("chain"),
        "token": row["token"],
        "symbol": row.get("symbol") or None,
        "placement": "organic",
        "volume_usd": round(float(row.get("vol") or 0.0), 2),
        "swaps": int(row.get("swaps") or 0),
        "traders": int(row.get("traders") or 0),
        "buyers": int(row.get("buyers") or 0),
        "price_change_pct": _pct(row),
        "top10_holder_share": _round(row.get("top10_holder_share")),
        "score": scored["score"],
        "components": scored["components"],
        "guards": scored["guards"],
        "coverage": scored["coverage"],
        "unavailable": scored["unavailable"],
        "last_event_at": row.get("last_ts"),
    }


def _pct(row: dict[str, Any]) -> float | None:
    first, last = row.get("first_px"), row.get("last_px")
    if not first or first <= 0 or last is None:
        return None
    return round((float(last) - float(first)) / float(first) * 100.0, 4)


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)


def _board(chain: str, hours: int, limit: int, degraded: list[str]) -> dict[str, Any]:
    try:
        rows = fetch_chain_rows(chain, hours, CANDIDATES)
    except Exception as exc:  # fail-open: one dead chain cannot blank the page
        degraded.append(f"{chain}:{type(exc).__name__}")
        return {"chain": chain, "items": [], "window_hours": hours, "source": "degraded"}
    items = [dict(_item({**row, "chain": chain})) for row in rows]
    items.sort(key=lambda i: i["score"], reverse=True)
    return {
        "chain": chain,
        "items": items[:limit],
        "window_hours": hours,
        "source": "clickhouse" if items else "empty",
    }


@router.get("/per-chain", summary="Per-chain trending boards")
async def get_per_chain_trending(
    chains: Annotated[
        str | None,
        Query(description="Comma-separated chains; omit for the default five"),
    ] = None,
    window: Annotated[int, Query(description="Lookback window in hours: 1, 6 or 24")] = 24,
    limit: Annotated[int, Query(ge=1, le=25, description="Max items per chain")] = 10,
) -> dict[str, Any]:
    """Return one ranked, explainable board per chain plus the paid slot."""
    if window not in PER_CHAIN_WINDOWS:
        raise HTTPException(
            status_code=422, detail=f"window must be one of {list(PER_CHAIN_WINDOWS)}"
        )
    requested = [c.strip().lower() for c in (chains or "").split(",") if c.strip()] or list(
        DEFAULT_CHAINS
    )
    degraded: list[str] = []
    boards = await asyncio.gather(
        *[
            asyncio.to_thread(_board, chain, int(window), int(limit), degraded)
            for chain in requested
        ]
    )
    slots = load_slots()
    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for board in boards:
        taken, refused = select_promoted(slots, str(board["chain"]))
        promoted.extend(taken)
        rejected.extend(refused)
    return {
        "window_hours": int(window),
        "generated_at": datetime.now(UTC),
        "boards": boards,
        "promoted": promoted,
        "rejected_promotions": rejected,
        "degraded": degraded,
        "source": "clickhouse",
    }
