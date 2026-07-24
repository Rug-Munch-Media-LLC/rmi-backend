"""Solana wallet labels CSV source - 103K SOL labels via DuckDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)

CSV_PATH = "/home/dev/rmi/backend/data/wallet-labels-clean/wallet_labels_solana.csv"

async def query_solana_labels_csv(address: str, chain: str = "solana") -> list[Label]:
    """Query the Solana wallet labels CSV via DuckDB.

    Schema (likely): address,source,name,entity_type,threat_group,risk_level

    Only matches if chain == "solana" - returns [] for EVM chains.
    """
    if chain != "solana":
        return []

    def _query() -> list[Label]:
        from app.core.duckdb_analytics import DuckDBAnalytics

        try:
            d = DuckDBAnalytics()
            try:
                rows = d.query_parquet(
                    CSV_PATH,
                    """
                    SELECT *
                    FROM data
                    WHERE LOWER(address) = ?
                    LIMIT 20
                    """,
                    [address.lower()],
                )
                labels = []
                for row in rows:
                    # Field names may differ - try common names
                    label_value = (
                        row.get("name")
                        or row.get("label")
                        or row.get("entity_type")
                        or row.get("source")
                    )
                    if not label_value:
                        continue
                    labels.append(
                        Label(
                            address=row.get("address", address).lower(),
                            chain="solana",
                            label_type="entity",
                            label_value=label_value,
                            source=LabelSource.SOLANA_LABELS_CSV,
                            confidence=0.8,
                            verified_at=datetime.now(UTC),
                            attributes={
                                k: v
                                for k, v in row.items()
                                if k not in ("address", "name", "label") and v
                            },
                        )
                    )
                return labels
            finally:
                d.close()
        except (ConnectionError, TimeoutError, ValueError, TypeError, KeyError) as e :
            log.warning("solana_labels_csv_query_fail address=%s err=%s", address[:10], e)
            raise

    return await asyncio.to_thread(_query)
