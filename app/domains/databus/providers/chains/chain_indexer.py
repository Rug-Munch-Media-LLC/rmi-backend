"""
Chain Indexer ClickHouse provider — reads chain-indexer data directly from ClickHouse.

Provides LOCAL-tier access to:
  - evm_transfers  — 73M+ ERC-20 transfers across 8 EVM chains
  - dex_events     — 47M+ DEX swap/pool events
  - deployer_events — deployer addresses for new token launches
  - solana_spl_balance_changes — 16M+ Solana SPL balance deltas

These are the SAME tables the chain-indexer writes to. The scanner has never
read from them — until now. This provider bridges that gap.

Architecture:
  DataBus fetch("token_transfers", ...) → ProviderChain → chain_indexer provider
  DataBus fetch("token_holders", ...)   → ProviderChain → chain_indexer provider
  DataBus fetch("new_launches", ...)    → ProviderChain → chain_indexer provider
  DataBus fetch("solana_balance_changes", ...) → ProviderChain → chain_indexer provider

All queries run asynchronously via asyncio.to_thread since clickhouse_driver is sync.
Every function returns dict | None following the DataBus provider contract.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.providers.chain_indexer")

# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD", "")
    ch_db = "wallet_memory"

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
        logger.info("Chain-indexer ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Chain-indexer ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ── Async wrapper ─────────────────────────────────────────────────────────


async def _run_sync(fn, *args, **kwargs):
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


# ── CHAIN MAP: normalize chain names to ClickHouse format ─────────────────

_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
}


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to ClickHouse format. Returns '' if unknown."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════


async def _chain_indexer_token_transfers(**kwargs: Any) -> dict | None:
    """Query evm_transfers for token transfer history.

    Parameters (from DataBus fetch kwargs):
      token_address: str — ERC-20 token contract address
      chain: str — chain name (ethereum, bsc, arbitrum, etc.)
      from_block: int — starting block (optional, default 0)
      to_block: int — ending block (optional, default unlimited)
      limit: int — max results (optional, default 100)

    Returns dict with transfer list or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    from_block = int(kwargs.get("from_block", 0))
    to_block = int(kwargs.get("to_block", 0))
    limit = min(int(kwargs.get("limit", 100)), 1000)

    if not token_address:
        return None
    if not chain:
        return None

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "limit": limit,
        }

        where_parts = ["token_address = %(addr)s", "chain = %(chain)s"]
        params["chain"] = chain

        if from_block > 0:
            where_parts.append("block_number >= %(from_block)s")
            params["from_block"] = from_block
        if to_block > 0:
            where_parts.append("block_number <= %(to_block)s")
            params["to_block"] = to_block

        where_clause = " AND ".join(where_parts)

        try:
            return client.execute(
                f"""
                SELECT chain, block_number, tx_hash, from_address, to_address,
                       token_address, value, block_timestamp, log_index,
                       token_symbol, token_name
                FROM wallet_memory.evm_transfers
                WHERE {where_clause}
                ORDER BY block_number DESC, log_index DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("evm_transfers query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    transfers = []
    for row in rows:
        transfers.append(
            {
                "chain": row[0],
                "block_number": row[1],
                "tx_hash": row[2],
                "from_address": row[3],
                "to_address": row[4],
                "token_address": row[5],
                "value": row[6],
                "block_timestamp": row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7]),
                "log_index": row[8],
                "token_symbol": row[9],
                "token_name": row[10],
            }
        )

    return {
        "data": {
            "transfers": transfers,
            "count": len(transfers),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "chain_indexer (ClickHouse evm_transfers)",
        "tier": "local",
        "is_local": True,
    }


async def _chain_indexer_dex_events(**kwargs: Any) -> dict | None:
    """Query dex_events for DEX swap/pool events on a token.

    Parameters:
      token_address: str — token contract address (matched against token0/token1)
      chain: str — chain name
      limit: int — max results (optional, default 50)

    Returns dict with DEX events or None.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not token_address or not chain:
        return None

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
            "limit": limit,
        }

        try:
            return client.execute(
                """
                SELECT type, chain, block_number, block_timestamp, tx_hash,
                       log_index, contract_address, dex, version,
                       token0, token1, sender, recipient,
                       amount0_in, amount1_in, amount0_out, amount1_out,
                       amount0, amount1, whale
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(addr0)s OR token1 = %(addr1)s)
                ORDER BY block_number DESC, log_index DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("dex_events query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    events = []
    for row in rows:
        events.append(
            {
                "type": row[0],
                "chain": row[1],
                "block_number": row[2],
                "block_timestamp": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                "tx_hash": row[4],
                "log_index": row[5],
                "contract_address": row[6],
                "dex": row[7],
                "version": row[8],
                "token0": row[9],
                "token1": row[10],
                "sender": row[11],
                "recipient": row[12],
                "amount0_in": row[13],
                "amount1_in": row[14],
                "amount0_out": row[15],
                "amount1_out": row[16],
                "amount0": row[17],
                "amount1": row[18],
                "whale": bool(row[19]) if len(row) > 19 else False,
            }
        )

    return {
        "data": {
            "dex_events": events,
            "count": len(events),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "chain_indexer (ClickHouse dex_events)",
        "tier": "local",
        "is_local": True,
    }


async def _chain_indexer_deployer_history(**kwargs: Any) -> dict | None:
    """Query deployer_events for deployer address history.

    Parameters:
      address: str — deployer wallet address (deployer field)
      chain: str — chain name (optional)
      limit: int — max results (optional, default 50)

    Returns dict with deployer events or None.
    """
    address = kwargs.get("address", kwargs.get("deployer", "")).lower()
    chain_raw = kwargs.get("chain", "")
    chain = _normalize_chain(chain_raw) if chain_raw else ""
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not address:
        return None

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "deployer": address,
            "limit": limit,
        }

        where_parts = ["deployer = %(deployer)s"]
        if chain:
            where_parts.append("chain = %(chain)s")
            params["chain"] = chain

        where_clause = " AND ".join(where_parts)

        try:
            return client.execute(
                f"""
                SELECT deployer, token_address, chain, block_number,
                       block_timestamp, tx_hash, type, factory
                FROM wallet_memory.deployer_events
                WHERE {where_clause}
                ORDER BY block_number DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("deployer_events query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    events = []
    for row in rows:
        events.append(
            {
                "deployer": row[0],
                "token_address": row[1],
                "chain": row[2],
                "block_number": row[3],
                "block_timestamp": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
                "tx_hash": row[5],
                "type": row[6],
                "factory": row[7],
            }
        )

    return {
        "data": {
            "deployer_events": events,
            "count": len(events),
            "deployer": address,
            "chain": chain or "any",
        },
        "source": "chain_indexer (ClickHouse deployer_events)",
        "tier": "local",
        "is_local": True,
    }


async def _chain_indexer_solana_balance_changes(**kwargs: Any) -> dict | None:
    """Query solana_spl_balance_changes for SPL token balance deltas.

    Parameters:
      token_address: str — SPL token mint address
      limit: int — max results (optional, default 100)

    Returns dict with balance changes or None.
    """
    mint = kwargs.get("token_address", kwargs.get("address", kwargs.get("mint", ""))).lower()
    limit = min(int(kwargs.get("limit", 100)), 1000)

    if not mint:
        return None

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "mint": mint,
            "limit": limit,
        }

        try:
            return client.execute(
                """
                SELECT slot, block_timestamp, tx_hash, token_account, mint,
                       pre_amount, post_amount, delta, decimals,
                       token_symbol, token_name
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(mint)s
                ORDER BY block_timestamp DESC, slot DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("solana_spl_balance_changes query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return None

    changes = []
    for row in rows:
        changes.append(
            {
                "slot": row[0],
                "block_timestamp": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
                "tx_hash": row[2],
                "token_account": row[3],
                "mint": row[4],
                "pre_amount": row[5],
                "post_amount": row[6],
                "delta": row[7],
                "decimals": row[8],
                "token_symbol": row[9],
                "token_name": row[10],
            }
        )

    return {
        "data": {
            "balance_changes": changes,
            "count": len(changes),
            "mint": mint,
        },
        "source": "chain_indexer (ClickHouse solana_spl_balance_changes)",
        "tier": "local",
        "is_local": True,
    }


async def _chain_indexer_holders(**kwargs: Any) -> dict | None:
    """Compute token holder count from evm_transfers unique addresses.

    Parameters:
      token_address: str — ERC-20 token contract address
      chain: str — chain name
      limit: int — max holder entries (optional, default 50)

    Returns dict with top holders or None.

    NOTE: This provides approximate holder metrics by analyzing transfer
    patterns. For exact on-chain balances, use RPC-level sources.
    """
    token_address = kwargs.get("token_address", kwargs.get("address", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    limit = min(int(kwargs.get("limit", 50)), 500)

    if not token_address or not chain:
        return None

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "chain": chain,
            "limit": limit,
        }

        try:
            # Rank addresses by number of incoming transfers (proxy for holder activity)
            return client.execute(
                """
                SELECT to_address, count() AS tx_count,
                       min(block_number) AS first_block,
                       max(block_number) AS last_block
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                GROUP BY to_address
                ORDER BY tx_count DESC
                LIMIT %(limit)s
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("holder query failed: %s", exc)
            return []

    def _count_query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr": token_address,
            "chain": chain,
        }

        try:
            return client.execute(
                """
                SELECT uniq(to_address) AS total_holders
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("holder count query failed: %s", exc)
            return []

    # Run queries sequentially — clickhouse_driver.Client is NOT thread-safe
    rows = await _run_sync(_query)
    if not rows:
        return None

    count_rows = await _run_sync(_count_query)
    total = count_rows[0][0] if count_rows else 0

    holders_list = []
    for row in rows:
        holders_list.append(
            {
                "address": row[0],
                "tx_count": row[1],
                "first_block": row[2],
                "last_block": row[3],
            }
        )

    return {
        "data": {
            "holders": holders_list,
            "total_holders": total,
            "top_holders_count": len(holders_list),
            "token_address": token_address,
            "chain": chain,
        },
        "source": "chain_indexer (ClickHouse evm_transfers aggregation)",
        "tier": "local",
        "is_local": True,
    }
