"""

Lightweight wallet label lookup endpoint for CF Worker enrichment.
Called by Workers to annotate tool responses with label data.
"""

from __future__ import annotations
from typing import Any

import json
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger("labels.lookup")

router = APIRouter(prefix="/api/v1/labels", tags=["wallet-labels"])


class LabelLookupRequest(BaseModel):
    addresses: list[str] = Field(..., min_length=1, max_length=10)
    chain: str = Field(default="ethereum")


@router.post("/lookup")
async def lookup_labels(req: LabelLookupRequest) -> dict:
    """Batch lookup wallet labels for enrichment.

    Called by CF Workers to annotate tool responses with wallet intelligence.
    Returns labels from Redis cache + ClickHouse, with 5min Redis caching.
    """
    try:
        addresses = [a.strip() for a in req.addresses if a.strip()]
        labels: dict[str, list] = {a: [] for a in addresses}

        # Try ClickHouse first
        try:
            from clickhouse_driver import Client

            ch = Client(
                host=os.getenv("CH_HOST", "rmi-clickhouse"),
                port=int(os.getenv("CH_PORT", "9000")),
                user=os.getenv("CH_USER", "default"),
                password=os.getenv("CH_PASSWORD", "") or None,
                settings={"max_execution_time": 3},
            )
            placeholders = ", ".join(["%s"] * len(addresses))
            rows = ch.execute(
                f"SELECT address, label_name, label_category, source, is_sanctioned "  # noqa: S608 — verified bound/allowlisted SQL construction
                f"FROM wallet_memory.wallet_labels "
                f"WHERE address IN ({placeholders}) "
                f"ORDER BY loaded_at DESC LIMIT 50",
                addresses,
            )
            for row in rows:
                addr = row[0]
                labels[addr].append(
                    {
                        "name": row[1] or "",
                        "category": row[2] or "",
                        "source": row[3] or "",
                        "sanctioned": bool(row[4]),
                    }
                )
        except (ImportError, ConnectionError, TimeoutError, OSError) as e:
            logger.debug(f"ClickHouse labels failed, falling back to Redis: {e}")
            # Fallback to Redis
            try:
                import redis as _redis

                r = _redis.Redis(
                    host=os.getenv("REDIS_HOST", "rmi-redis"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
                for addr in addresses:
                    cached = r.get(f"x402:enrich:{addr}")
                    if cached:
                        data = json.loads(cached)
                        labels[addr] = data.get("labels", [])
            except (ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as e:
                logger.debug(f"Redis label fallback failed: {e}")

        # Count labeled addresses
        labeled = sum(1 for v in labels.values() if v)

        return {
            "labels": labels,
            "addresses_checked": len(addresses),
            "addresses_labeled": labeled,
            "total_labels": sum(len(v) for v in labels.values()),
        }

    except Exception as e:
        logger.error(f"Label lookup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
