"""First-buyers analysis — earliest buyers of a token from OWN indexer data.

2026-08-25 quality push ("top 25 on basic, top 200 on advanced"): ranks the
earliest distinct buyers of a token and classifies each wallet from our own
ClickHouse facts — wallet age at buy time (fresh/new/established) and
same-window bundling. Powers the 🛒 First Buyers block on the bot's basic
card (``limit=25``) and the full breakdown in deep scans (``limit=200``).

Data sources (own lite-chain-indexer ClickHouse, no external APIs):
  * EVM     — ``wallet_memory.evm_transfers`` (token transfers; a buy is the
              wallet's earliest incoming transfer of the token).
  * Solana  — ``wallet_memory.solana_spl_balance_changes`` (signed balance
              deltas; a buy is the earliest positive delta per owner).

Fail-open by contract: ClickHouse unreachable / empty data → ``None`` so the
scan never degrades because of this engine. Sync queries run via
``asyncio.to_thread`` and are bounded by ``max_execution_time``.
"""

from __future__ import annotations

import asyncio
import statistics
from datetime import UTC
from typing import Any

from app.domains.scanner.engines._indexer_port import ch_client as _get_ch_client
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

# Time-box for every query (ClickHouse max_execution_time, seconds).
_QUERY_TIMEOUT_S = 10
# A launch "bundle" = ≥3 first-buys inside the same block-second window.
_BUNDLE_WINDOW_S = 2
_BUNDLE_MIN_WALLETS = 3
# Wallet-age buckets relative to the wallet's first-ever incoming transfer.
_FRESH_MAX_AGE_S = 24 * 3600  # born <24h before its first buy of this token
_NEW_MAX_AGE_S = 7 * 24 * 3600  # born <7d before its first buy


def _iso(ts: Any) -> str | None:
    """Best-effort datetime → ISO-8601 (UTC). Returns None when unparsable."""
    try:
        if isinstance(ts, str):
            return ts
        if ts is None:
            return None
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            return ts.replace(tzinfo=UTC).isoformat()
        return ts.isoformat()  # type: ignore[union-attr]
    except (ValueError, TypeError, AttributeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (sync, bounded, graceful)
# ═══════════════════════════════════════════════════════════════════════════


def _query_first_buyers_evm(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Earliest N distinct buyers — ``(buyer, first_buy_ts, tokens_in)``."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return client.execute(  # type: ignore[no-any-return]
            """
            SELECT
                to_address AS buyer,
                min(block_timestamp) AS first_buy_ts,
                sum(toFloat64OrZero(value)) AS tokens_in
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s
              AND token_address = %(token)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                     '0x000000000000000000000000000000000000dead')
            GROUP BY buyer
            ORDER BY first_buy_ts ASC
            LIMIT %(limit)s
            """,
            {"chain": chain, "token": token_address, "limit": int(limit)},
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("first_buyers_evm_query_failed: %s", exc)
        return None


def _query_first_buyers_solana(mint: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Earliest N distinct buyers from SPL balance deltas — same row shape."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return client.execute(  # type: ignore[no-any-return]
            """
            SELECT
                owner AS buyer,
                min(block_timestamp) AS first_buy_ts,
                sum(toFloat64OrZero(amount)) AS tokens_in
            FROM wallet_memory.solana_spl_balance_changes
            WHERE mint = %(mint)s
              AND toFloat64OrZero(amount) > 0
              AND owner != ''
            GROUP BY buyer
            ORDER BY first_buy_ts ASC
            LIMIT %(limit)s
            """,
            {"mint": mint, "limit": int(limit)},
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("first_buyers_solana_query_failed: %s", exc)
        return None


def _query_wallet_births_evm(buyers: tuple[str, ...], chain: str) -> dict[str, Any]:
    """First-ever incoming transfer per buyer wallet (EVM) — wallet birth."""
    client = _get_ch_client()
    if client is None or not buyers:
        return {}
    try:
        rows = client.execute(
            """
            SELECT to_address AS wallet, min(block_timestamp) AS birth
            FROM wallet_memory.evm_transfers
            WHERE chain = %(chain)s AND to_address IN %(buyers)s
            GROUP BY wallet
            """,
            {"chain": chain, "buyers": buyers},
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
        return {str(r[0]): r[1] for r in rows}
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_births_evm_failed: %s", exc)
        return {}


def _query_wallet_births_solana(buyers: tuple[str, ...]) -> dict[str, Any]:
    """First SPL activity per owner wallet (Solana approximation of birth)."""
    client = _get_ch_client()
    if client is None or not buyers:
        return {}
    try:
        rows = client.execute(
            """
            SELECT owner AS wallet, min(block_timestamp) AS birth
            FROM wallet_memory.solana_spl_balance_changes
            WHERE owner IN %(buyers)s
            GROUP BY wallet
            """,
            {"buyers": buyers},
            settings={"max_execution_time": _QUERY_TIMEOUT_S},
        )
        return {str(r[0]): r[1] for r in rows}
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("wallet_births_solana_failed: %s", exc)
        return {}


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION + AGGREGATION
# ═══════════════════════════════════════════════════════════════════════════


def _age_bucket(age_at_buy_s: float | None) -> str:
    """Classify a wallet by its age when it bought the token."""
    if age_at_buy_s is None:
        return "unknown"
    if age_at_buy_s < _FRESH_MAX_AGE_S:
        return "fresh"
    if age_at_buy_s < _NEW_MAX_AGE_S:
        return "new"
    return "established"


def _bundle_flags(timestamps: list[Any]) -> set[int]:
    """Indexes of buyers whose first-buy lands in a ≥N-wallet time window."""
    buckets: dict[int, list[int]] = {}
    for idx, ts in enumerate(timestamps):
        try:
            epoch = ts.timestamp() if hasattr(ts, "timestamp") else float(ts)
        except (TypeError, ValueError, OSError):
            continue
        buckets.setdefault(int(epoch // _BUNDLE_WINDOW_S), []).append(idx)
    flagged: set[int] = set()
    for members in buckets.values():
        if len(members) >= _BUNDLE_MIN_WALLETS:
            flagged.update(members)
    return flagged


def analyze_first_buyers_rows(rows: list[tuple[Any, ...]], births: dict[str, Any]) -> dict[str, Any]:
    """Pure classifier over raw query rows → API shape (unit-tested)."""
    buyers: list[dict[str, Any]] = []
    timestamps: list[Any] = []
    for row in rows:
        wallet, ts, tokens = str(row[0] or ""), row[1], row[2]
        if not wallet or ts is None:
            continue
        timestamps.append(ts)
        age: float | None = None
        birth = births.get(wallet)
        if birth is not None:
            try:
                age = abs((ts - birth).total_seconds())
            except (TypeError, AttributeError, ValueError):
                age = None
        buyers.append(
            {
                "wallet": wallet,
                "first_buy_ts": _iso(ts),
                "tokens": float(tokens or 0),
                "wallet_age_bucket": _age_bucket(age),
                "age_at_buy_s": round(age) if age is not None else None,
            }
        )

    bundled_idx = _bundle_flags(timestamps)
    for i, b in enumerate(buyers):
        b["bundled"] = i in bundled_idx

    total = len(buyers) or 1
    fresh = sum(1 for b in buyers if b["wallet_age_bucket"] == "fresh")
    new = sum(1 for b in buyers if b["wallet_age_bucket"] == "new")
    bundled = sum(1 for b in buyers if b["bundled"])
    latencies: list[float] = []
    known_ts = [ts for ts in timestamps if ts is not None]
    if known_ts:
        try:
            launch = min(known_ts)
            latencies = [(ts - launch).total_seconds() for ts in known_ts if hasattr(ts, "timestamp")]
        except (TypeError, ValueError):
            latencies = []
    return {
        "buyers": buyers,
        "aggregates": {
            "unique_buyers": len(buyers),
            "fresh_wallet_pct": round(fresh * 100.0 / total, 1),
            "new_wallet_pct": round((fresh + new) * 100.0 / total, 1),
            "established_pct": round((total - fresh - new) * 100.0 / total, 1),
            "bundled_pct": round(bundled * 100.0 / total, 1),
            "median_latency_s": (round(statistics.median(latencies), 1) if latencies else None),
        },
    }


async def get_first_buyers(address: str, chain: str, limit: int = 25) -> dict[str, Any] | None:
    """Top-N earliest buyers with own-data wallet classification.

    Fail-open: returns ``None`` when ClickHouse is unreachable or the token
    has no indexed buys yet — callers render nothing rather than fabricate.
    """
    chain_norm = (chain or "").strip().lower()
    if not address or not chain_norm:
        return None
    if chain_norm == "solana":
        rows = await asyncio.to_thread(_query_first_buyers_solana, address, limit)
        births = (
            await asyncio.to_thread(_query_wallet_births_solana, tuple(str(r[0]) for r in rows or [])) if rows else {}
        )
    else:
        rows = await asyncio.to_thread(_query_first_buyers_evm, address, chain_norm, limit)
        births = (
            await asyncio.to_thread(
                _query_wallet_births_evm,
                tuple(str(r[0]) for r in rows or []),
                chain_norm,
            )
            if rows
            else {}
        )
    if not rows:
        return None
    result = analyze_first_buyers_rows(list(rows), births)
    result["limit"] = limit
    result["chain"] = chain_norm
    return result
