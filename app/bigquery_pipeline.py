"""
BigQuery Wallet Analytics Pipeline
====================================
Streams wallet labels, scan results, and embedding usage to BigQuery.
All usage counts against free tier (1TB queries/month - we'll use <1%).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.gcloud_manager import get_gcloud

logger = get_logger("bigquery.pipeline")


async def stream_wallet_labels(labels: list) -> dict:
    """Stream wallet labels to BigQuery rmi_production.wallet_labels."""
    if not labels:
        return {"streamed": 0, "errors": 0}

    gcloud = get_gcloud()
    rows = []
    for w in labels:
        rows.append(
            {
                "address": w.get("address", ""),
                "chain": w.get("chain", "ethereum"),
                "label": w.get("label", ""),
                "persona": w.get("persona", ""),
                "risk_score": float(w.get("risk_score", 0)),
                "tx_count": int(w.get("tx_count", 0)),
                "volume_usd": float(w.get("volume_usd", 0)),
                "last_updated": datetime.now(UTC).isoformat(),
            }
        )

    ok = await gcloud.bigquery_insert("rmi_production", "wallet_labels", rows)
    return {"streamed": len(rows) if ok else 0, "errors": 0 if ok else len(rows)}


async def stream_scan_result(result: dict) -> bool:
    """Stream a single scan result to BigQuery."""
    gcloud = get_gcloud()
    return await gcloud.bigquery_insert(
        "rmi_production",
        "scan_results",
        [
            {
                "token_address": result.get("address", ""),
                "chain": result.get("chain", "ethereum"),
                "risk_level": result.get("risk_level", "unknown"),
                "is_scam": result.get("is_scam", False),
                "score": int(result.get("score", 0)),
                "flags": result.get("flags", []),
                "scan_timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    )


async def stream_embedding_usage(provider: str, task: str, dims: int, count: int = 1) -> None:
    """Log embedding usage for analytics."""
    gcloud = get_gcloud()
    await gcloud.bigquery_insert(
        "rmi_production",
        "embedding_usage",
        [
            {
                "provider": provider,
                "task": task,
                "dims": dims,
                "call_count": count,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    )


async def top_scam_wallets(limit: int = 10) -> list:
    """Query BigQuery for top scam-flagged wallets (parameterized query)."""
    gcloud = get_gcloud()
    return await gcloud.bigquery_query(
        """
        SELECT address, chain, label, risk_score
        FROM rmi_production.wallet_labels
        WHERE risk_score > 0.7
        ORDER BY risk_score DESC
        LIMIT @limit
        """,
        params=[
            {
                "name": "limit",
                "parameterType": {"type": "INT64"},
                "parameterValue": {"value": str(limit)},
            }
        ],
    )


async def daily_scan_stats() -> list:
    """Get today's scan statistics from BigQuery."""
    gcloud = get_gcloud()
    return await gcloud.bigquery_query("""
        SELECT risk_level, COUNT(*) as count
        FROM rmi_production.scan_results
        WHERE scan_timestamp >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), DAY)
        GROUP BY risk_level
        ORDER BY count DESC
    """)
