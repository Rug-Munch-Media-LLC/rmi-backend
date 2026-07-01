"""eth-labels.db source — 115K EVM labels from local SQLite.

Schema (verified):
  accounts(id, chain_id, address, label, name_tag, created_at, updated_at)

chain_id mapping:
  1 = ethereum, 42161 = arbitrum, 137 = polygon, etc.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.domain.labels.models import Label, LabelSource

log = logging.getLogger(__name__)

DB_PATH = "/home/dev/rmi/eth-labels.db"

# chain_id -> chain name mapping (from EVM chains list)
_CHAIN_ID_MAP = {
    1: "ethereum",
    42161: "arbitrum",
    10: "optimism",
    137: "polygon",
    56: "bsc",
    43114: "avalanche",
    250: "fantom",
    100: "gnosis",
}


async def query_eth_labels_db(address: str, chain: str = "ethereum") -> list[Label]:
    """Query the local eth-labels.db SQLite for an address.

    Args:
        address: EVM address (0x...)
        chain: Chain name (only EVM chains in this DB)

    Returns:
        List of labels found (one per chain_id that has a match).
    """
    def _query() -> list[Label]:
        import sqlite3

        try:
            conn = sqlite3.connect(DB_PATH, timeout=2.0)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT chain_id, label, name_tag, updated_at FROM accounts WHERE address = ? LIMIT 50",
                    (address.lower(),),
                )
                labels = []
                for row in cursor:
                    chain_id = row["chain_id"]
                    chain_name = _CHAIN_ID_MAP.get(chain_id, f"chain-{chain_id}")
                    label_value = row["name_tag"] or row["label"]
                    if not label_value:
                        continue
                    labels.append(
                        Label(
                            address=address.lower(),
                            chain=chain_name,
                            label_type="entity",
                            label_value=label_value,
                            source=LabelSource.ETH_LABELS_DB,
                            confidence=0.9,  # eth-labels.db is curated
                            verified_at=_parse_date(row["updated_at"]),
                            attributes={"chain_id": chain_id, "raw_label": row["label"]},
                        )
                    )
                return labels
            finally:
                conn.close()
        except Exception as e:
            log.warning("eth_labels_db_query_fail address=%s err=%s", address[:10], e)
            raise

    return await asyncio.to_thread(_query)


def _parse_date(s: str | None) -> datetime:
    """Parse SQLite date string to datetime."""
    if not s:
        return datetime.now(UTC)
    try:
        # Format: '2024-06-30 21:58:54'
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)
