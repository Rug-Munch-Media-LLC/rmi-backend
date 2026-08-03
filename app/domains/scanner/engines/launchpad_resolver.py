"""
Launchpad Resolver — Trace Factory Deployments to the Real Creator
==================================================================
Launchpads (pump.fun, bags.fm, moonshot, ...) create tokens through a
factory contract / on-chain program. Our ``deployer_events`` table therefore
records the *factory* address as the deployer, not the real creator EOA.

This module resolves the factory back to the real deployer:

  - **Solana / pump.fun**: the pump.fun program is a well-known program ID.
    Every token mint's first transaction (creation) is sent to the program
    by the real creator, who is the transaction **fee payer**.
  - **EVM / bags.fm**: the factory contract emits the child token; the real
    deployer is ``tx.from`` of the creation transaction.

Resolution strategies per chain:

  * Solana — ``getSignaturesForAddress(mint)`` → oldest signature (creation),
    then ``getTransaction`` (jsonParsed) → ``transaction.message.accountKeys[0]``
    is the fee payer. Confidence 0.9 (fee payer is the creator for pump.fun;
    the program itself can never be the fee payer).
  * EVM — needs the deploy transaction hash. We look it up from
    ``deployer_events`` (``deploy_tx`` column) when the caller did not already
    provide it, then ``eth_getTransactionByHash`` → ``from``. Confidence 1.0
    (``tx.from`` is authoritative for contract creation on EVM).

Registry:
  Known factory/program addresses live in ``LAUNCHPAD_FACTORIES`` (in code) and
  are overridable/extendable via the JSON registry file
  ``launchpad_factories.json`` next to this module. The JSON file is merged on
  top of the in-code dict, so operators can add newly-discovered launchpads
  without a code change.

Caching:
  - Redis ``launchpad:{chain}:{token}`` TTL 1h (async, graceful fallback).
  - In-process mirror (10 min) consistent with the other engines.

Usage:
    from app.domains.scanner.engines.launchpad_resolver import resolve_launchpad_deployer

    result = await resolve_launchpad_deployer(token_address, "solana", factory_address)
    # → {"real_deployer": "...", "launchpad": "pump.fun", "confidence": 0.9, "evidence_tx": "..."}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.core.logging import get_logger
from app.domains.databus.providers.chains.chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
)
from app.domains.scanner.engines.rpc_router import rpc_call

# Legacy module-local names kept so unit tests that patch
# ``launchpad_resolver._redis_get`` / ``_redis_set`` keep intercepting.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Registry ───────────────────────────────────────────────────────────────
# In-code default registry. The JSON file next to this module is merged on
# top at import time so operators can extend it without code changes.
LAUNCHPAD_FACTORIES: dict[str, dict[str, str]] = {
    "solana": {
        # pump.fun program ID — every token is created by sending the initial
        # transaction to this program; the fee payer is the real creator.
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun",
        # Add more Solana launchpads as discovered (e.g. moonshot, pump it).
    },
    "ethereum": {
        # EVM factory addresses for launchpads (bags.fm etc.) — lowercase hex.
    },
    "bsc": {
        # BSC factory addresses
    },
}

_REGISTRY_PATH = Path(__file__).parent / "launchpad_factories.json"


def _load_registry() -> dict[str, dict[str, str]]:
    """Merge the JSON registry file on top of the in-code defaults.

    JSON keys win (so operators can update a launchpad's label or add new
    factories). Missing/corrupt file falls back to the in-code dict.
    """
    merged: dict[str, dict[str, str]] = {chain: dict(factories) for chain, factories in LAUNCHPAD_FACTORIES.items()}
    try:
        if _REGISTRY_PATH.exists():
            raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
            for chain, factories in raw.items():
                if not isinstance(factories, dict):
                    continue
                merged.setdefault(str(chain), {}).update({str(k): str(v) for k, v in factories.items()})
    except (OSError, ValueError) as exc:
        logger.debug("launchpad_registry_load_failed: %s", exc)
    return merged


_REGISTRY: dict[str, dict[str, str]] = _load_registry()


def get_launchpad_registry() -> dict[str, dict[str, str]]:
    """Return the active launchpad registry (chain → factory → name)."""
    return {chain: dict(factories) for chain, factories in _REGISTRY.items()}


def reload_registry() -> dict[str, dict[str, str]]:
    """Reload the JSON registry file (runtime update path)."""
    global _REGISTRY
    _REGISTRY = _load_registry()
    return get_launchpad_registry()


def lookup_launchpad(chain: str, address: str) -> str | None:
    """Return the launchpad name for a factory address, or None.

    Solana addresses are base58 and case-sensitive, so a naive lowercase
    comparison would miss the canonical pump.fun program ID when callers
    pre-lowercase deployer addresses. Matching against our *own* registry is
    safe case-insensitively (the known factories are unique constants), so we
    try exact first, then case-insensitive, for every chain.
    """
    chain_norm = _normalize_chain(chain)
    factories = _REGISTRY.get(chain_norm, {})
    if not factories or not address:
        return None
    direct = factories.get(address)
    if direct is not None:
        return direct
    lowered = address.lower()
    for key, name in factories.items():
        if key.lower() == lowered:
            return name
    return None


# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_TTL_SECONDS = 3600  # 1 hour
REDIS_CACHE_PREFIX = "launchpad"
_RESOLVE_CACHE: dict[str, dict[str, Any]] = {}
_RESOLVE_CACHE_TTL = 600  # 10 minutes in-process mirror
_RESOLVE_CACHE_MAX = 200

# RPC / ClickHouse query guard
_QUERY_TIMEOUT_SECONDS = 10


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def resolve_launchpad_deployer(
    token_address: str,
    chain: str,
    deployer_address: str,
    deploy_tx: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a launchpad factory deployer to the real creator EOA.

    Args:
        token_address: The token/mint/contract address (used as the cache key
            and, on Solana, to locate the creation transaction).
        chain: Chain name (``solana``, ``ethereum``, ``bsc``, ...).
        deployer_address: The address recorded in ``deployer_events`` — when it
            is a known launchpad factory, we trace back to the real deployer.
        deploy_tx: Optional known creation transaction hash. When omitted we
            look it up from ``deployer_events`` (EVM) or derive it from the
            token mint (Solana).

    Returns:
        ``None`` when the deployer is not a known factory or resolution fails.
        Otherwise::

            {
                "real_deployer": "0x...",          # EOA creator
                "launchpad": "pump.fun",           # registry label
                "confidence": 0.9,                 # 0-1 attribution confidence
                "evidence_tx": "0x...",            # creation transaction
                "chain": "solana",
            }
    """
    launchpad = lookup_launchpad(chain, deployer_address)
    if launchpad is None:
        return None

    chain_norm = _normalize_chain(chain)
    token_key = str(token_address or "").strip()
    if not token_key and not deploy_tx:
        logger.debug("launchpad_resolve: missing token_address and deploy_tx", chain=chain_norm)
        return None

    # Cache key: token when available, otherwise the deploy tx (resolution
    # anchored on a creation transaction is deterministic per tx).
    anchor = token_key or f"tx:{deploy_tx}"
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{anchor}"
    cached = await _redis_get(cache_key)
    if cached is not None and isinstance(cached, dict) and cached.get("real_deployer"):
        return cast("dict[str, Any]", cached)

    if cache_key in _RESOLVE_CACHE:
        entry = _RESOLVE_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _RESOLVE_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        if chain_norm == "solana":
            result = await _resolve_solana(token_key, deploy_tx)
        else:
            result = await _resolve_evm(token_key, chain_norm, deploy_tx)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("launchpad_resolve_failed %s: %s", token_key[:16], exc)
        return None

    if result is None:
        return None

    result["launchpad"] = launchpad
    result["chain"] = chain_norm

    await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
    result["_cached_at"] = time.time()
    _RESOLVE_CACHE[cache_key] = result
    if len(_RESOLVE_CACHE) > _RESOLVE_CACHE_MAX:
        _RESOLVE_CACHE.pop(next(iter(_RESOLVE_CACHE)), None)

    logger.info(
        "launchpad_resolved",
        chain=chain_norm,
        launchpad=launchpad,
        token=token_key[:16] or deploy_tx,
        real_deployer=str(result["real_deployer"])[:10],
        confidence=result.get("confidence"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# SOLANA — fee payer of the creation transaction
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_solana(token_address: str, deploy_tx: str | None) -> dict[str, Any] | None:
    """Resolve a Solana launchpad token to its creator.

    The real creator is the fee payer of the token's creation transaction
    (the first signature for the mint). The pump.fun program itself is never
    the fee payer, so this is unambiguous in practice.
    """
    signature = deploy_tx or await _find_solana_creation_tx(token_address)
    if not signature:
        return None

    tx = await _get_solana_transaction(signature)
    if not tx:
        return None

    fee_payer = _extract_fee_payer(tx)
    if not fee_payer:
        return None

    return {
        "real_deployer": fee_payer,
        "confidence": 0.9,
        "evidence_tx": signature,
    }


async def _find_solana_creation_tx(token_address: str) -> str | None:
    """Return the creation signature for a token mint.

    ``getSignaturesForAddress`` returns newest-first, so the oldest signature
    in a bounded window is the creation transaction.
    """
    data = await rpc_call(
        "solana",
        "getSignaturesForAddress",
        [token_address, {"limit": 20}],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    sigs = data.get("result") or []
    if not isinstance(sigs, list) or not sigs:
        return None
    oldest = sigs[-1]
    if isinstance(oldest, dict):
        return oldest.get("signature")
    return str(oldest)


async def _get_solana_transaction(signature: str) -> dict[str, Any] | None:
    """Fetch a Solana transaction in jsonParsed encoding."""
    data = await rpc_call(
        "solana",
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    tx = data.get("result")
    if not isinstance(tx, dict):
        return None
    return tx


def _extract_fee_payer(tx: dict[str, Any]) -> str | None:
    """Extract the fee payer (real creator) from a jsonParsed Solana tx.

    ``message.accountKeys[0]`` is the fee payer; jsonParsed keys may be
    dicts (``{"pubkey": ...}``) or plain strings depending on encoding.
    """
    try:
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        if not account_keys:
            return None
        first = account_keys[0]
        if isinstance(first, dict):
            return first.get("pubkey")
        return str(first)
    except (AttributeError, TypeError, KeyError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# EVM — tx.from of the creation transaction
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_evm(
    token_address: str,
    chain: str,
    deploy_tx: str | None,
) -> dict[str, Any] | None:
    """Resolve an EVM launchpad token to its real deployer (``tx.from``)."""
    tx_hash = deploy_tx
    if not tx_hash:
        tx_hash = await run_sync(_query_deploy_tx, token_address, chain)
    if not tx_hash:
        return None

    data = await rpc_call(
        chain,
        "eth_getTransactionByHash",
        [tx_hash],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    tx = data.get("result")
    if not isinstance(tx, dict):
        return None
    sender = tx.get("from")
    if not sender:
        return None

    return {
        "real_deployer": str(sender).lower(),
        "confidence": 1.0,
        "evidence_tx": tx_hash,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE — deploy_tx lookup fallback
# ═══════════════════════════════════════════════════════════════════════════


def _query_deploy_tx(token_address: str, chain: str) -> str | None:
    """Return the deploy transaction hash for a token from deployer_events."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT argMin(tx_hash, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND lower(token_address) = %(token)s
            LIMIT 1
            """,
            {"chain": chain, "token": token_address.lower()},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_deploy_tx_query_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


def _query_factory_latest_deploy_tx(factory_address: str, chain: str) -> str | None:
    """Return the most recent deploy tx recorded for a factory deployer.

    Used when a caller only has the factory address (no token context), e.g.
    the deployer fingerprint engine. The resolved creator of the most recent
    deployment is the best available attribution.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT tx_hash
            FROM wallet_memory.deployer_events
            WHERE lower(deployer) = %(factory)s
              AND chain = %(chain)s
              AND tx_hash != ''
            ORDER BY block_timestamp DESC
            LIMIT 1
            """,
            {"factory": factory_address.lower(), "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_factory_latest_tx_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _RESOLVE_CACHE,
        _RESOLVE_CACHE_MAX,
        _RESOLVE_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
            "registry_chains": sorted(_REGISTRY),
            "registry_factories": sum(len(v) for v in _REGISTRY.values()),
        },
    )


def clear_cache() -> None:
    """Clear the in-process resolution cache (for testing)."""
    _common_clear_cache(_RESOLVE_CACHE)


# Keep cache/registry monitoring helpers importable for sibling engines.
__all__ = [
    "LAUNCHPAD_FACTORIES",
    "clear_cache",
    "get_cache_stats",
    "get_launchpad_registry",
    "lookup_launchpad",
    "reload_registry",
    "resolve_launchpad_deployer",
]
