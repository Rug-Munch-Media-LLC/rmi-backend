"""Scanner configuration — data access mode and backup lookup options.

Part 2 — B2 backup data access options (Scanner)

The scanner supports four data-access modes for historical / backup data:

  ``rpc``         — live RPC queries (default; no archive node needed).
  ``b2_parquet``  — Backblaze B2 parquet lookup (historical data without
                    RPC — the chain-indexer writes hourly parquet snapshots
                    to B2, downloaded via rclone + queried with DuckDB).
  ``clickhouse``  — ClickHouse historical access (when the user runs our
                    indexer; same tables the chain-indexer writes).
  ``auto``        — best mode for the environment:
                      1. ClickHouse if configured (fastest, already indexed)
                      2. B2 parquet if enabled (historical, free)
                      3. RPC fallback (always works, rate-limited)

Set ``SCANNER_DATA_ACCESS_MODE`` to force a mode; the default is ``auto``.
"""

from __future__ import annotations

import os
from typing import Literal

DataAccessMode = Literal["rpc", "b2_parquet", "clickhouse", "auto"]

DATA_ACCESS_MODE: DataAccessMode = os.getenv("SCANNER_DATA_ACCESS_MODE", "auto").lower()  # type: ignore[assignment]

# ── B2 parquet lookup ─────────────────────────────────────────────────
# The chain-indexer (RugMunchMedia/chain-indexer) writes hourly parquet
# snapshots to B2: b2-crypt:chain-data/parquet/{chain}/{YYYY}/{MM}/{DD}/{HH}/
#   {chain}_{YYYY-MM-DD}.parquet        # transfers/events
#   {chain}_dex_{YYYY-MM-DD}.parquet    # DEX swaps
# The remote is an rclone crypt remote (content-encrypted), so DuckDB httpfs
# cannot read it directly — the lookup downloads through rclone (decrypts on
# the fly) and queries the local files with DuckDB.
B2_REMOTE = os.getenv("B2_CHAIN_DATA_REMOTE", "b2-crypt")
B2_BUCKET = os.getenv("B2_CHAIN_DATA_BUCKET", "chain-data")
B2_PATH_PREFIX = os.getenv("B2_CHAIN_DATA_PREFIX", "parquet")
B2_ENABLED = os.getenv("SCANNER_B2_ENABLED", "0").lower() in ("1", "true", "yes")

# ── ClickHouse access ─────────────────────────────────────────────────
# Same ClickHouse the chain-indexer writes (evm_transfers, dex_events,
# deployer_events). ``SCANNER_CLICKHOUSE_ENABLED`` gates the lookup; the
# client itself is resolved lazily via the chain-indexer provider so the
# scanner never hardcodes connection details here.
CLICKHOUSE_ENABLED = os.getenv("SCANNER_CLICKHOUSE_ENABLED", "1").lower() in ("1", "true", "yes")

# ── External archive node / paid RPC (Option 3) ──────────────────────
# When set, ``b2_parquet``/``clickhouse`` modes that need archive data can
# fall back to this paid endpoint for historical calls. Empty = use the
# standard rpc_router endpoints only.
ARCHIVE_RPC_URL = os.getenv("SCANNER_ARCHIVE_RPC_URL", "").strip()


def _clickhouse_configured() -> bool:
    """Best-effort probe: is ClickHouse reachable for this process?"""
    if not CLICKHOUSE_ENABLED:
        return False
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

        return _get_ch_client() is not None
    except Exception:
        return False


def _b2_configured() -> bool:
    """Best-effort probe: is the B2 parquet remote enabled + rclone present?"""
    if not B2_ENABLED:
        return False
    try:
        import shutil

        return shutil.which("rclone") is not None
    except Exception:
        return False


def resolve_data_access_mode() -> DataAccessMode:
    """Resolve the effective data-access mode (auto → best available)."""
    mode = DATA_ACCESS_MODE
    if mode != "auto":
        return mode
    # auto: ClickHouse (fastest, already indexed) → B2 parquet → RPC
    if _clickhouse_configured():
        return "clickhouse"
    if _b2_configured():
        return "b2_parquet"
    return "rpc"


def describe_data_access() -> dict[str, object]:
    """Return the resolved mode + probe results (for /debug + logs)."""
    return {
        "mode": DATA_ACCESS_MODE,
        "resolved": resolve_data_access_mode(),
        "clickhouse_configured": _clickhouse_configured(),
        "b2_enabled": B2_ENABLED,
        "b2_configured": _b2_configured(),
        "archive_rpc_configured": bool(ARCHIVE_RPC_URL),
    }


__all__ = [
    "ARCHIVE_RPC_URL",
    "B2_BUCKET",
    "B2_ENABLED",
    "B2_PATH_PREFIX",
    "B2_REMOTE",
    "CLICKHOUSE_ENABLED",
    "DATA_ACCESS_MODE",
    "DataAccessMode",
    "describe_data_access",
    "resolve_data_access_mode",
]
