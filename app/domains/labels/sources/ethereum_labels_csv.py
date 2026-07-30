"""Ethereum wallet labels CSV source - 51K EVM labels via DuckDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)

CSV_PATH = "/home/dev/rmi/backend/data/wallet-labels-clean/wallet_labels_ethereum.csv"


async def query_ethereum_labels_csv(address: str, chain: str = "ethereum") -> list[Label]:
    """Query the ethereum wallet labels CSV via DuckDB.

    Schema:
      address,chain,source,name,entity_type,threat_group,risk_level,verified
    """

    def _query() -> list[Label]:
        from app.core.duckdb_analytics import DuckDBAnalytics

        try:
            d = DuckDBAnalytics()
            try:
                rows = d.query_parquet(
                    CSV_PATH,
                    """
                    SELECT address, chain, source, name, entity_type, threat_group, risk_level, verified
                    FROM data
                    WHERE LOWER(address) = ?
                    LIMIT 20
                    """,
                    [address.lower()],
                )
                labels = []
                for row in rows:
                    label_value = row.get("name") or row.get("entity_type")
                    if not label_value:
                        continue
                    chain_name = row.get("chain", chain).lower()
                    labels.append(
                        Label(
                            address=row["address"].lower(),
                            chain=chain_name,
                            label_type="entity",
                            label_value=label_value,
                            source=LabelSource.ETHEREUM_LABELS_CSV,
                            confidence=0.85,
                            verified_at=datetime.now(UTC),
                            attributes={
                                "entity_type": row.get("entity_type"),
                                "threat_group": row.get("threat_group"),
                                "risk_level": row.get("risk_level"),
                                "verified": row.get("verified"),
                            },
                        )
                    )
                return labels
            finally:
                d.close()
        except (ValueError, TypeError, KeyError) as e:
            log.warning("ethereum_labels_csv_query_fail address=%s err=%s", address[:10], e)
            raise

    return await asyncio.to_thread(_query)
