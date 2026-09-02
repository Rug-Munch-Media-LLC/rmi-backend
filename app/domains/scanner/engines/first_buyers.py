"""First-buyers analysis — earliest buyers of a token from OWN indexer data.

2026-08-29 depth push (synced with the private scanner's
``rugmunch_scanner/unified/engines/first_buyers.py``): ranks the earliest
distinct buyers of a token and classifies each wallet from our own
ClickHouse facts — wallet age at buy time (fresh/new/established) and
same-window bundling. Powers the 🛒 First Buyers block on the bot's basic
card (``limit=25``) and the deep 250-wallet breakdown (``limit=250``,
raised from 200 — deep scans go deeper).

Data sources (own lite-chain-indexer ClickHouse + on-demand Solana RPC):
  * EVM     — ``wallet_memory.evm_transfers`` (token transfers; a buy is the
              wallet's earliest incoming transfer of the token).
  * Solana  — ``wallet_memory.solana_spl_balance_changes`` (signed balance
              deltas; a buy is the earliest positive delta per owner).
              2026-08-29 pump.fun path: when the indexer has no rows for the
              mint (pump.fun bonding-curve tokens are NOT in the indexer's
              hardcoded tracked-mint list), falls back to an on-demand RPC
              parse — ``getSignaturesForAddress(mint)`` → ``getTransaction``
              (jsonParsed) → pre/post token-balance deltas. This is the
              exact same signal the indexer would write, captured for ANY
              mint (pump.fun bonding-curve buys, Raydium migrations,
              LaunchLab, Bonk.fun): a buy = earliest positive balance delta
              for the buyer's owner wallet.

Fail-open by contract: ClickHouse unreachable / empty data → ``None`` so the
scan never degrades because of this engine. Sync queries run via
``run_sync`` (asyncio.to_thread) and are bounded by ``max_execution_time``.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from app.domains.scanner.engines._common import _norm_addr, run_sync
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

#: Sample size per scan tier (mirrors ``unified/_phase_helpers.py``):
#: quick/basic scans surface the top 25 earliest buyers; deep scans get the
#: full 250-wallet breakdown (raised from 200 on 2026-08-29 — deep scans go
#: deeper).
FIRST_BUYERS_LIMIT_QUICK = 25
FIRST_BUYERS_LIMIT_DEEP = 250

# On-demand RPC budget (pump.fun path): signatures fetched + txs parsed per
# mint. Bounded so a deep 250-wallet cohort stays within a few seconds.
_RPC_SIG_LIMIT = 300
_RPC_TX_LIMIT = 120

_rpc_call_impl: Any | None = None


def _get_rpc_call() -> Any | None:
    """Lazy-init the Solana RPC callable (fail-open on import errors)."""
    global _rpc_call_impl
    if _rpc_call_impl is not None:
        return _rpc_call_impl
    try:
        from app.domains.scanner.engines.rpc_router import rpc_call

        _rpc_call_impl = rpc_call
        return _rpc_call_impl
    except ImportError:  # pragma: no cover
        logger.debug("first_buyers_solana_rpc_import_failed")
        return None


def _iso(ts: Any) -> str | None:
    """Best-effort datetime → ISO-8601 (UTC). Returns None when unparsable."""
    try:
        if isinstance(ts, str):
            return ts
        if ts is None:
            return None
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            return str(ts.replace(tzinfo=UTC).isoformat())
        return str(ts.isoformat())
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
        return client.execute(
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
    """Earliest N distinct buyers from SPL balance deltas — same row shape.

    Uses the real ``solana_spl_balance_changes`` schema (``delta`` /
    ``token_account`` — see lite-chain-indexer bootstrap + migration 006;
    the legacy ``amount`` column does not exist). A buy is the earliest
    positive ``delta`` per resolved owner wallet.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        return client.execute(
            """
            SELECT
                if(owner != '', owner, token_account) AS buyer,
                min(block_timestamp) AS first_buy_ts,
                sum(toFloat64OrZero(delta)) AS tokens_in
            FROM wallet_memory.solana_spl_balance_changes
            WHERE mint = %(mint)s
              AND toFloat64OrZero(delta) > 0
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
# ON-DEMAND SOLANA RPC PATH (pump.fun first buyers)
# ═══════════════════════════════════════════════════════════════════════════
# The lite-chain-indexer only tracks a hardcoded list of ~10 major Solana
# mints (USDC/USDT/SOL/BONK/...). pump.fun bonding-curve tokens are NOT in
# that list, so ``solana_spl_balance_changes`` has no rows for them. This
# path falls back to on-demand RPC: ``getSignaturesForAddress(mint)`` →
# ``getTransaction`` (jsonParsed) → pre/post token-balance deltas per owner
# (the same signal the indexer's parser would write). Bounded + fail-open.


def _parse_solana_balance_deltas(tx_data: dict[str, Any], mint: str) -> list[tuple[str, int]]:
    """Parse (owner, delta) pairs for a mint from a jsonParsed transaction.

    Mirrors lite-chain-indexer ``parse_solana_transfer_events``: scans
    ``meta.preTokenBalances`` / ``postTokenBalances`` and emits the signed
    balance change for every account of the mint. Returns [(owner, delta)]
    with non-zero deltas only. Pure + unit-tested.
    """
    meta = tx_data.get("meta") or {}
    pre_balances: list[dict[str, Any]] = meta.get("preTokenBalances") or []
    post_balances: list[dict[str, Any]] = meta.get("postTokenBalances") or []
    if not pre_balances and not post_balances:
        return []

    def _idx(bal: dict[str, Any]) -> int:
        try:
            return int(bal.get("accountIndex", -1))
        except (TypeError, ValueError):
            return -1

    pre_map = {_idx(b): b for b in pre_balances if b.get("mint") == mint}
    post_map = {_idx(b): b for b in post_balances if b.get("mint") == mint}

    deltas: list[tuple[str, int]] = []
    for idx in sorted(set(pre_map) | set(post_map)):
        pre = pre_map.get(idx, {})
        post = post_map.get(idx, {})
        owner = str(post.get("owner") or pre.get("owner") or "")
        if not owner:
            continue

        def _amount(bal: dict[str, Any]) -> int:
            try:
                return int((bal.get("uiTokenAmount") or {}).get("amount") or 0)
            except (TypeError, ValueError):
                return 0

        change = _amount(post) - _amount(pre)
        if change != 0:
            deltas.append((owner, change))
    return deltas


def _ts_to_dt(epoch: float) -> Any:
    """Epoch float → tz-aware datetime (or None when 0/unparsable)."""
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError, TypeError):
        return None


async def _rpc_first_buyers_solana(mint: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Earliest distinct buyers via on-demand Solana RPC (pump.fun path).

    Fetches up to ``_RPC_SIG_LIMIT`` recent signatures for the mint, walks
    them oldest→newest, parses pre/post balance deltas, and keeps the first
    positive delta per owner. Returns ``(buyer, first_buy_ts, tokens_in)``
    rows (same shape as the ClickHouse query) or None on any failure.

    Pure-RPC + fail-open: never raises, never blocks longer than the
    per-call timeouts. The walk stops once ``limit`` distinct buyers are
    found (the signature list is newest-first so earlier slots are always
    walked before we break).
    """
    rpc_call = _get_rpc_call()
    if rpc_call is None:
        return None

    try:
        data = await rpc_call(
            "solana",
            "getSignaturesForAddress",
            [mint, {"limit": _RPC_SIG_LIMIT}],
            timeout=_QUERY_TIMEOUT_S,
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("first_buyers_solana_rpc_sigs_failed: %s", str(exc)[:120])
        return None
    if not isinstance(data, dict):
        return None
    sigs = data.get("result") or []
    if not isinstance(sigs, list) or not sigs:
        return None

    # Oldest-first (getSignaturesForAddress returns newest first).
    sigs = [s for s in sigs if isinstance(s, dict) and s.get("signature")]
    sigs = sorted(sigs, key=lambda s: int(s.get("slot") or 0))

    buys: dict[str, dict[str, Any]] = {}  # owner -> {ts, tokens, slot}
    for sig_info in sigs[:_RPC_TX_LIMIT]:
        try:
            tx_data = await rpc_call(
                "solana",
                "getTransaction",
                [sig_info["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                timeout=_QUERY_TIMEOUT_S,
            )
        except Exception as exc:  # fail-open per tx
            logger.debug("first_buyers_solana_rpc_tx_failed: %s", str(exc)[:120])
            continue
        if not isinstance(tx_data, dict) or not isinstance(tx_data.get("result"), dict):
            continue
        tx = tx_data["result"]
        slot = int(sig_info.get("slot") or 0)
        try:
            ts = float(tx.get("blockTime") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        for owner, change in _parse_solana_balance_deltas(tx, mint):
            if change <= 0:
                continue
            cur = buys.get(owner)
            if cur is None or ts < cur["ts"] or (ts == cur["ts"] and slot < cur.get("slot", 0)):
                buys[owner] = {"ts": ts, "tokens": float(change), "slot": slot}
        if len(buys) >= limit:
            break

    if not buys:
        return None
    rows = [
        (owner, _ts_to_dt(info["ts"]), info["tokens"])
        for owner, info in sorted(buys.items(), key=lambda kv: kv[1]["ts"])
    ]
    return rows[:limit]


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION + AGGREGATION (pure, unit-tested)
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


async def get_first_buyers(address: str, chain: str, limit: int = FIRST_BUYERS_LIMIT_QUICK) -> dict[str, Any] | None:
    """Top-N earliest buyers with own-data wallet classification.

    Fail-open: returns ``None`` when ClickHouse is unreachable or the token
    has no indexed buys yet — callers render nothing rather than fabricate.
    Deep scans pass ``limit=FIRST_BUYERS_LIMIT_DEEP`` (250).
    """
    chain_norm = (chain or "").strip().lower()
    if not address or not chain_norm:
        return None
    # Normalize once at entry: EVM addresses must be lowercase to match
    # ClickHouse storage (checksummed input would match 0 rows).
    address = _norm_addr(address, chain_norm)
    if chain_norm == "solana":
        rows = await run_sync(_query_first_buyers_solana, address, limit)
        # pump.fun path: the indexer only tracks a handful of hardcoded
        # mints, so bonding-curve tokens have no balance-change rows. Fall
        # back to on-demand RPC when ClickHouse is empty (fail-open).
        if not rows:
            rows = await _rpc_first_buyers_solana(address, limit)
        births = await run_sync(_query_wallet_births_solana, tuple(str(r[0]) for r in rows or [])) if rows else {}
    else:
        rows = await run_sync(_query_first_buyers_evm, address, chain_norm, limit)
        births = (
            await run_sync(
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


__all__ = [
    "FIRST_BUYERS_LIMIT_DEEP",
    "FIRST_BUYERS_LIMIT_QUICK",
    "_parse_solana_balance_deltas",
    "analyze_first_buyers_rows",
    "get_first_buyers",
]
