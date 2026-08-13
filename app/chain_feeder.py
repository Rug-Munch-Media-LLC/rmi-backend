"""
Chain Data Feeder - Pre-loads wallet clustering engine with real on-chain data.
Uses Helius (primary) with QuickNode fallback. Rate-limited + cached.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.chain_cache import get_chain_cache
from app.chain_client import get_chain_client
from app.core.logging import get_logger
from app.domains.wallet.clustering import Transaction, get_clustering_engine

logger = get_logger(__name__)


async def feed_wallet_transactions(wallet: str, limit: int = 50) -> int:
    """Fetch real transactions for a wallet and load into clustering engine.
    Returns number of transactions loaded. Cached for 5 min."""
    cache = get_chain_cache()
    cached = await cache.get("feed_wallet", wallet, limit)
    if cached is not None:
        return cached

    client = get_chain_client()
    engine = get_clustering_engine()

    # Get recent signatures
    sigs = await cache.get("signatures", wallet, limit)
    if sigs is None:
        sigs = await client.get_signatures(wallet, limit=min(limit, 50))
        await cache.set("signatures", sigs, wallet, limit, ttl=120)

    if not sigs:
        await cache.set("feed_wallet", 0, wallet, limit)
        return 0

    # Get parsed transactions (batch)
    tx_hashes = [sig for sig in (s.get("signature") for s in sigs[:25]) if sig]
    txs = await cache.get("transactions_batch", *tx_hashes[:5])
    if txs is None:
        txs = await client.get_transactions(tx_hashes[:25])
        await cache.set("transactions_batch", txs, *tx_hashes[:5], ttl=120)

    count = 0
    for sig_data in sigs:
        sig = sig_data.get("signature")
        if not sig:
            continue
        ts = sig_data.get("blockTime")
        dt = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(UTC)

        # Simple transaction from signature data
        tx = Transaction(
            signature=sig,
            timestamp=dt,
            from_address=wallet,
            to_address=sig_data.get("to", "unknown"),
            amount=float(sig_data.get("lamport", 0)) / 1e9 if sig_data.get("lamport") else 0,
            token="SOL",  # noqa: S106 — config default
            program="system",
        )
        engine.add_transaction(tx)
        count += 1

    await cache.set("feed_wallet", count, wallet, limit)
    logger.info(f"Fed {count} transactions for {wallet[:12]}...")
    return count


async def get_holders_for_token(token_address: str, limit: int = 20) -> list[str]:
    """Get top token holders. Uses cached Helius token-accounts lookup."""
    cache = get_chain_cache()
    cached = await cache.get("holders", token_address, limit)
    if cached is not None:
        return cached

    client = get_chain_client()
    accounts = await client.get_token_accounts(token_address)

    holders = []
    for acct in accounts[:limit]:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        owner = info.get("owner")
        if owner:
            holders.append(owner)

    await cache.set("holders", holders, token_address, limit, ttl=300)
    return holders


async def get_wallet_balance(wallet: str) -> float:
    """Get SOL balance with caching."""
    cache = get_chain_cache()
    cached = await cache.get("balance", wallet)
    if cached is not None:
        return cached

    client = get_chain_client()
    balance = await client.get_balance(wallet)
    await cache.set("balance", balance, wallet, ttl=300)
    return balance
