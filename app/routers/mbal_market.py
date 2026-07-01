#!/usr/bin/env python3
"""#17 — MBAL 10M-Label Market Maker. Public searchable database of the MBAL dataset
(10M labeled addresses). Free search, paid API. Kaggle: cryptorugmuncher."""

import contextlib
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/mbal", tags=["mbal-market-maker"])

MBAL_DB = os.environ.get("MBAL_DB", str(Path.home() / "rmi/backend/data/mbal.db"))


def _get_db() -> sqlite3.Connection | None:
    """Open MBAL database connection."""
    db_path = Path(MBAL_DB)
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/search/{address}")
async def search_mbal(address: str):
    """Search MBAL database for an address. Returns label if found."""
    conn = _get_db()
    if not conn:
        return {
            "address": address,
            "found": False,
            "database": "unavailable",
            "note": "MBAL database not found. Visit: https://www.kaggle.com/datasets/cryptorugmuncher/mbal-10m-labeled-addresses",
        }

    try:
        cursor = conn.execute(
            "SELECT address, label, category, confidence, source FROM addresses WHERE address = ? LIMIT 1",
            (address.lower(),),
        )
        row = cursor.fetchone()
        if row:
            return {
                "address": address,
                "found": True,
                "label": row["label"],
                "category": row["category"],
                "confidence": row["confidence"],
                "source": row["source"],
                "dataset": "MBAL (Multi-chain Blockchain Address Labels)",
            }
        return {"address": address, "found": False, "database": "available"}
    except sqlite3.Error:
        return {"address": address, "found": False, "database": "error"}
    finally:
        conn.close()


@router.get("/stats")
async def mbal_stats():
    """MBAL dataset statistics."""
    conn = _get_db()
    stats = {
        "dataset": "MBAL — Multi-chain Blockchain Address Labels",
        "total_addresses": 0,
        "categories": [],
        "chains_covered": [],
        "kaggle_url": "https://www.kaggle.com/datasets/cryptorugmuncher/mbal-10m-labeled-addresses",
        "database_available": conn is not None,
    }
    if conn:
        try:
            stats["total_addresses"] = conn.execute("SELECT COUNT(*) FROM addresses").fetchone()[0]
            stats["categories"] = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT category FROM addresses WHERE category IS NOT NULL LIMIT 20"
                ).fetchall()
            ]
            with contextlib.suppress(sqlite3.Error):
                stats["chains_covered"] = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT chain FROM addresses WHERE chain IS NOT NULL LIMIT 20"
                    ).fetchall()
                ]
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    return stats


class BulkSearchRequest(BaseModel):
    addresses: list[str]
    max_results: int = 100


@router.post("/bulk-search")
async def bulk_search_mbal(request: BulkSearchRequest):
    """Search multiple addresses in MBAL. Paid tier (x402)."""
    conn = _get_db()
    if not conn:
        raise HTTPException(503, "MBAL database unavailable")

    results: list[dict] = []
    try:
        for addr in request.addresses[: request.max_results]:
            cursor = conn.execute(
                "SELECT address, label, category, confidence FROM addresses WHERE address = ? LIMIT 1", (addr.lower(),)
            )
            row = cursor.fetchone()
            if row:
                results.append(
                    {
                        "address": addr,
                        "found": True,
                        "label": row["label"],
                        "category": row["category"],
                        "confidence": row["confidence"],
                    }
                )
            else:
                results.append({"address": addr, "found": False})
    except sqlite3.Error:
        raise HTTPException(500, "Database query error")
    finally:
        conn.close()

    return {
        "searched": len(request.addresses[: request.max_results]),
        "found": sum(1 for r in results if r.get("found")),
        "results": results,
    }


@router.get("/category/{category}")
async def search_by_category(category: str, limit: int = Query(20, le=100)):
    """Search MBAL addresses by category (e.g., 'exchange', 'scam', 'mixer')."""
    conn = _get_db()
    if not conn:
        raise HTTPException(503, "MBAL database unavailable")

    try:
        rows = conn.execute(
            "SELECT address, label, category, confidence FROM addresses WHERE category = ? LIMIT ?", (category, limit)
        ).fetchall()
        results = [
            {"address": r["address"], "label": r["label"], "category": r["category"], "confidence": r["confidence"]}
            for r in rows
        ]
        return {"category": category, "count": len(results), "addresses": results}
    except sqlite3.Error:
        raise HTTPException(500, "Database query error")
    finally:
        conn.close()
