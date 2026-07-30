"""Etherscan combined labels CSV source - 51K EVM labels via DuckDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)

CSV_PATH = "/home/dev/rmi/backend/data/etherscan_combined_labels.csv"


async def query_etherscan_csv(address: str, chain: str = "ethereum") -> list[Label]:
    """Query the Etherscan combined labels CSV via DuckDB.

    Schema:
      address,chain,source,name,label_type,label_subtype,project,entity_type,entity_types

    Returns:
        List of labels (usually 1 per matching row).
    """

    def _query() -> list[Label]:
        from app.core.duckdb_analytics import DuckDBAnalytics

        try:
            d = DuckDBAnalytics()
            try:
                rows = d.query_parquet(
                    CSV_PATH,
                    """
                    SELECT address, chain, source, name, label_type, label_subtype, project, entity_type
                    FROM data
                    WHERE LOWER(address) = ?
                    LIMIT 20
                    """,
                    [address.lower()],
                )
                labels = []
                for row in rows:
                    label_value = row.get("name") or row.get("label_type") or row.get("source")
                    if not label_value:
                        continue
                    chain_name = row.get("chain", chain).lower()
                    labels.append(
                        Label(
                            address=row["address"].lower(),
                            chain=chain_name,
                            label_type="entity",
                            label_value=label_value,
                            source=LabelSource.ETHERSCAN_CSV,
                            confidence=0.85,
                            verified_at=datetime.now(UTC),
                            attributes={
                                "raw_label_type": row.get("label_type"),
                                "label_subtype": row.get("label_subtype"),
                                "project": row.get("project"),
                                "entity_type": row.get("entity_type"),
                            },
                        )
                    )
                return labels
            finally:
                d.close()
        except (ValueError, TypeError, KeyError) as e:
            log.warning("etherscan_csv_query_fail address=%s err=%s", address[:10], e)
            raise

    return await asyncio.to_thread(_query)
