"""ClickHouse wallet_memory.wallet_labels source — 39.4M labels.

Schema (verified):
  address          String
  chain_id         String
  label_name       String
  label_category   String
  source           String
  is_sanctioned    UInt8 DEFAULT 0
  loaded_at        DateTime DEFAULT now()
  label_subtype    String DEFAULT ''

Chain mapping:
  ClickHouse stores chain_id as String. Values include numeric EVM chain IDs
  ("1"=ethereum, "56"=bsc, "137"=polygon, "42161"=arbitrum, "10"=optimism,
   "8453"=base, "43114"=avalanche) and string names ("solana", "bitcoin", "tron").

Usage:
    from app.domains.labels.sources.clickhouse_labels import query_clickhouse_labels
    labels = await query_clickhouse_labels("0x...", "ethereum")
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.labels.models import Label, LabelSource

log = get_logger(__name__)

# ── Chain name → ClickHouse chain_id mapping ─────────────────────────
# ClickHouse stores chain_id as String. We map our internal chain names
# to the formats used in the wallet_labels table.
_CHAIN_TO_CH_ID: dict[str, list[str]] = {
    "ethereum": ["1", "ethereum", "eth"],
    "solana": ["solana", "101", "SOL"],
    "bsc": ["56", "bsc", "bnb"],
    "polygon": ["137", "polygon", "matic"],
    "arbitrum": ["42161", "arbitrum"],
    "optimism": ["10", "optimism"],
    "base": ["8453", "base"],
    "avalanche": ["43114", "avalanche", "avax"],
    "fantom": ["250", "fantom", "ftm"],
    "gnosis": ["100", "gnosis", "xdai"],
    "tron": ["tron", "trx", "-2"],
    "bitcoin": ["bitcoin", "btc", "-1"],
}

# Reverse: ClickHouse chain_id → our chain name
_CH_ID_TO_CHAIN: dict[str, str] = {}
for chain_name, ch_ids in _CHAIN_TO_CH_ID.items():
    for ch_id in ch_ids:
        _CH_ID_TO_CHAIN[ch_id] = chain_name

# ── Cached ClickHouse client (lazy) ──────────────────────────────────
_ch_client: Any = None


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client. Returns None if unavailable."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD", "")
    ch_db = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass or None,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=10,
        )
        _ch_client.execute("SELECT 1")
        log.info("ClickHouse label source connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        log.warning("ClickHouse label source unavailable: %s", e)
        _ch_client = None
        return None


async def query_clickhouse_labels(address: str, chain: str = "ethereum") -> list[Label]:
    """Query ClickHouse wallet_memory.wallet_labels for an address.

    Tries exact chain match first (using chain_id mapping), then falls
    back to any-chain match for the address. Results are cached in Redis
    by the caller (FederatedLabelAPI).

    Args:
        address: Wallet address to look up (0x... / base58 / etc.)
        chain: Chain name (ethereum, solana, bsc, etc.)

    Returns:
        List of Label objects found in ClickHouse. Empty list on miss/error.
    """

    # Lazy-connect inside asyncio to_thread since clickhouse_driver is sync
    def _query() -> list[Label]:
        client = _get_ch_client()
        if client is None:
            return []

        ch_ids = _CHAIN_TO_CH_ID.get(chain.lower(), [chain.lower()])

        # Phase 1: try chain-specific query with all possible chain_id formats
        # clickhouse_driver supports IN tuple with dict params
        rows: list[tuple] = []
        try:
            rows = client.execute(
                """
                SELECT address, chain_id, label_name, label_category,
                       source, is_sanctioned, loaded_at, label_subtype
                FROM wallet_memory.wallet_labels
                WHERE address = %(addr)s
                  AND chain_id IN %(ch_ids)s
                ORDER BY loaded_at DESC
                LIMIT 20
                """,
                {
                    "addr": address.lower(),
                    "ch_ids": tuple(ch_ids),
                },
            )
        except (ValueError, RuntimeError, OSError) as exc:
            log.debug(
                "ClickHouse chain-specific query failed for %s on %s: %s",
                address[:12],
                chain,
                exc,
            )

        # Phase 2: try any-chain match for the address
        if not rows:
            try:
                rows = client.execute(
                    """
                    SELECT address, chain_id, label_name, label_category,
                           source, is_sanctioned, loaded_at, label_subtype
                    FROM wallet_memory.wallet_labels
                    WHERE address = %(addr)s
                    ORDER BY loaded_at DESC
                    LIMIT 20
                    """,
                    {"addr": address.lower()},
                )
            except (ValueError, RuntimeError, OSError) as exc:
                log.debug("ClickHouse any-chain query failed: %s", exc)
                return []

        # Convert to Label objects
        labels: list[Label] = []
        for row in rows:
            raw_chain_id = row[1] if len(row) > 1 else ""
            chain_name = _CH_ID_TO_CHAIN.get(raw_chain_id, raw_chain_id)

            label_name = (row[2] or "").strip()
            label_category = (row[3] or "").strip()
            source = (row[4] or "").strip()
            is_sanctioned = bool(row[5]) if len(row) > 5 else False
            loaded_at = row[6] if len(row) > 6 else None
            label_subtype = (row[7] or "").strip() if len(row) > 7 else ""

            if not label_name:
                continue

            # Map label_category → label_type
            label_type = _category_to_type(label_category, label_subtype)

            labels.append(
                Label(
                    address=row[0].lower(),
                    chain=chain_name,
                    label_type=label_type,
                    label_value=label_name,
                    source=LabelSource.CLICKHOUSE,
                    confidence=0.95,  # ClickHouse labels are curated imports
                    verified_at=(
                        loaded_at.replace(tzinfo=UTC)
                        if loaded_at and not loaded_at.tzinfo
                        else (loaded_at if loaded_at else datetime.now(UTC))
                    ),
                    attributes={
                        "chain_id": raw_chain_id,
                        "category": label_category,
                        "subtype": label_subtype,
                        "source_raw": source,
                        "is_sanctioned": is_sanctioned,
                    },
                )
            )

        return labels

    return await asyncio_to_thread(_query)


def _category_to_type(category: str, subtype: str = "") -> str:
    """Map ClickHouse label_category to our label_type enum."""
    cat = (category or "").lower()
    sub = (subtype or "").lower()

    if cat in ("entity", "organization", "exchange", "cex"):
        return "entity"
    if cat in ("scam", "phishing", "hack", "exploit", "fraud"):
        return "category"
    if cat in ("sanction", "sanctioned", "ofac"):
        return "risk"
    if cat in ("defi", "protocol", "dapp"):
        return "entity"
    if cat in ("mev", "bot", "flashbot"):
        return "category"
    if cat in ("bridge", "cross-chain"):
        return "entity"
    if cat in ("deployer", "creator", "factory"):
        return "entity"
    if cat in ("wallet", "personal", "user"):
        return "tag"
    if sub:
        return "tag"
    return "tag"


async def asyncio_to_thread(fn, *args, **kwargs):
    """Run sync function in thread pool (avoids import at top level)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)
