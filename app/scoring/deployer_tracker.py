"""
Deployer Tracker — Find the REAL Deployer
==========================================
The problem: scanners label PumpFun factory wallets as "deployer."
The REAL deployer is the person who paid for token creation.

SOLUTION per platform:
    1. PumpFun tokens (Solana):
       Trace the SOL transfer that funded token creation →
       find the wallet that sent SOL to PumpFun → THAT is the real deployer.
       Look at the creation transaction's fee payer (the wallet that
       called the PumpFun program) — that wallet is the real creator.

    2. Standard ERC20:
       Check the transaction that created the contract → msg.sender is deployer.

    3. Factory-created tokens (Uniswap, PancakeSwap):
       Trace the createPair transaction → find who called it.

    4. Universal structural detection:
       Uses launchpad_resolver (already built) for factory-aware tracing.

Deployer profiles are persisted in Redis under `deployer:{chain}:{address}`
with lifetime statistics, labeling, and risk history.

Redis keys:
    deployer:profile:{chain}:{address}  — JSON profile
    deployer:tokens:{chain}:{address}   — set of deployed token addresses
    deployer:rugged:{chain}:{address}   — count of rugged tokens
    deployer:last_seen:{chain}:{address} — timestamp
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from app.core.logging import get_logger
from app.core.redis import get_redis_async

logger = get_logger(__name__)

# ── PumpFun program ID (canonical) ──
PUMPFUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# Known factory addresses — checked before tracing
KNOWN_FACTORIES: dict[str, dict[str, str]] = {
    "solana": {
        PUMPFUN_PROGRAM_ID: "pump.fun",
        "BvHotBQkPfJbL2tCB6n4HhWHkeSwE7aH4vQhqVZkHSto": "pump.fun (old)",
    },
    "ethereum": {
        # Uniswap V2 Factory
        "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f": "uniswap_v2",
        # PancakeSwap Factory (BSC)
        "0xca143ce32fe78f1f7019d7d551a6402fc5350c73": "pancakeswap_v2",
        # SushiSwap Factory
        "0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac": "sushiswap_v2",
    },
    "bsc": {
        "0xca143ce32fe78f1f7019d7d551a6402fc5350c73": "pancakeswap_v2",
        "0x0841bd0b734e4f5853f0dd8d7f0412d1e3c7b8b7": "pancakeswap_v3",
    },
}

# Redis TTL: 30 days for deployer profiles
DEPLOYER_PROFILE_TTL = 30 * 86400
# Cache: 1 hour for resolution results
RESOLVE_CACHE_TTL = 3600


@dataclass
class DeployerProfile:
    """Complete deployer profile stored in Redis."""

    address: str
    chain: str
    total_tokens_launched: int = 0
    active_tokens_count: int = 0
    rugged_tokens_count: int = 0
    avg_token_lifespan_days: float = 0.0
    known_labels: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    is_factory: bool = False
    factory_name: str | None = None
    real_deployer_address: str | None = None
    resolution_method: str | None = None
    wallet_age_days: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "chain": self.chain,
            "total_tokens_launched": self.total_tokens_launched,
            "active_tokens_count": self.active_tokens_count,
            "rugged_tokens_count": self.rugged_tokens_count,
            "avg_token_lifespan_days": round(self.avg_token_lifespan_days, 1),
            "known_labels": self.known_labels,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "is_factory": self.is_factory,
            "factory_name": self.factory_name,
            "real_deployer_address": self.real_deployer_address,
            "resolution_method": self.resolution_method,
            "wallet_age_days": round(self.wallet_age_days, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeployerProfile:
        return cls(
            address=data.get("address", ""),
            chain=data.get("chain", ""),
            total_tokens_launched=data.get("total_tokens_launched", 0),
            active_tokens_count=data.get("active_tokens_count", 0),
            rugged_tokens_count=data.get("rugged_tokens_count", 0),
            avg_token_lifespan_days=data.get("avg_token_lifespan_days", 0.0),
            known_labels=data.get("known_labels", []),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
            is_factory=data.get("is_factory", False),
            factory_name=data.get("factory_name"),
            real_deployer_address=data.get("real_deployer_address"),
            resolution_method=data.get("resolution_method"),
            wallet_age_days=data.get("wallet_age_days", 0.0),
        )


def _is_factory_address(chain: str, address: str) -> tuple[bool, str | None]:
    """Check if an address is a known factory/program/launchpad.

    Returns:
        (is_factory, factory_name_or_None)
    """
    chain_lower = chain.lower()
    factories = KNOWN_FACTORIES.get(chain_lower, {})
    if not factories:
        return False, None

    # Direct match
    if address in factories:
        return True, factories[address]

    # Case-insensitive
    addr_lower = address.lower()
    for key, name in factories.items():
        if key.lower() == addr_lower:
            return True, name

    return False, None


# ═══════════════════════════════════════════════════════════════════════════
# RESOLVE — find the REAL deployer, bypassing factory addresses
# ═══════════════════════════════════════════════════════════════════════════


async def resolve_deployer(
    token_address: str,
    chain: str,
    deployer_address: str | None = None,
    deploy_tx: str | None = None,
) -> dict[str, Any]:
    """
    Resolve the REAL deployer for a token, bypassing factory/platform addresses.

    Args:
        token_address: Token/mint address
        chain: Blockchain identifier
        deployer_address: Known/claimed deployer (might be a factory)
        deploy_tx: Known creation transaction hash

    Returns:
        {
            "real_deployer": str,        # The actual creator EOA/wallet
            "claimed_deployer": str,     # Original deployer (could be factory)
            "is_factory": bool,          # True if claimed was a factory
            "factory_name": str | None,  # e.g. "pump.fun"
            "method": str,               # How we resolved
            "confidence": float,         # 0.0-1.0
            "evidence_tx": str | None,   # TX proving the resolution
        }
    """
    chain_norm = chain.lower()
    result: dict[str, Any] = {
        "real_deployer": deployer_address or token_address,
        "claimed_deployer": deployer_address or "",
        "is_factory": False,
        "factory_name": None,
        "method": "passthrough",
        "confidence": 0.5,
        "evidence_tx": None,
    }

    if not deployer_address:
        logger.debug("deployer_resolve_no_address", token=token_address[:12])
        return result

    # ── Step 1: Check if deployer is a known factory ──
    is_factory, factory_name = _is_factory_address(chain_norm, deployer_address)
    if is_factory:
        result["is_factory"] = True
        result["factory_name"] = factory_name

        # ── Step 2: Try built-in launchpad resolver ──
        resolved = await _resolve_via_launchpad_resolver(token_address, chain_norm, deployer_address, deploy_tx)
        if resolved:
            result.update(resolved)
            result["is_factory"] = True
            result["factory_name"] = factory_name
            result["claimed_deployer"] = deployer_address
            logger.info(
                "deployer_resolved_factory",
                factory=factory_name,
                real=result["real_deployer"][:12],
                method=result["method"],
            )
            return result

        # ── Step 3: Try RPC-based trace for Solana PumpFun ──
        if chain_norm == "solana" and factory_name == "pump.fun":
            rpc_result = await _trace_pumpfun_deployer(token_address, deployer_address)
            if rpc_result:
                result.update(rpc_result)
                result["is_factory"] = True
                result["factory_name"] = factory_name
                result["claimed_deployer"] = deployer_address
                return result

        # ── Step 4: EVM factory trace ──
        if chain_norm in ("ethereum", "bsc", "base", "polygon", "arbitrum", "optimism"):
            evm_result = await _trace_evm_factory_deployer(chain_norm, deployer_address, deploy_tx)
            if evm_result:
                result.update(evm_result)
                result["is_factory"] = True
                result["factory_name"] = factory_name
                result["claimed_deployer"] = deployer_address
                return result

        # ── Unresolved factory — return factory itself with low confidence ──
        result["method"] = "factory_unresolved"
        result["confidence"] = 0.2
        return result

    # ── Not a factory — check structural resolution ──
    if chain_norm == "solana":
        # For Solana, fee payer of creation tx is the real deployer
        sol_result = await _trace_solana_deployer(token_address)
        if sol_result and sol_result.get("real_deployer") != deployer_address:
            result.update(sol_result)
            result["claimed_deployer"] = deployer_address
            return result

    return result


async def _resolve_via_launchpad_resolver(
    token_address: str,
    chain: str,
    factory_address: str,
    deploy_tx: str | None = None,
) -> dict[str, Any] | None:
    """Try the existing launchpad_resolver engine."""
    try:
        from app.domains.scanner.engines.launchpad_resolver import (
            resolve_launchpad_deployer,
        )

        resolved = await resolve_launchpad_deployer(
            token_address=token_address,
            chain=chain,
            deployer_address=factory_address,
            deploy_tx=deploy_tx,
        )
        if resolved and resolved.get("real_deployer"):
            return {
                "real_deployer": resolved["real_deployer"],
                "method": f"launchpad_resolver:{resolved.get('method', 'factory')}",
                "confidence": resolved.get("confidence", 0.9),
                "evidence_tx": resolved.get("evidence_tx"),
            }
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("launchpad_resolver_unavailable: %s", exc)
    return None


async def _trace_pumpfun_deployer(
    token_address: str,
    factory_address: str,
) -> dict[str, Any] | None:
    """
    Trace the real PumpFun deployer.

    Strategy:
        For PumpFun tokens, the creation transaction is sent by the real
        deployer. The fee payer = the real creator (not the PumpFun program).
        We look at the earliest transaction for the token mint and extract
        the fee payer.

    Falls back to using helius_tools or direct RPC.
    """
    try:
        # Try Helius first (best Solana RPC)
        try:
            from app.helius_tools import get_token_creation_tx  # type: ignore[attr-defined]

            creation_data = await get_token_creation_tx(token_address)
            if creation_data and creation_data.get("fee_payer"):
                return {
                    "real_deployer": creation_data["fee_payer"],
                    "method": "helius_creation_trace",
                    "confidence": 0.95,
                    "evidence_tx": creation_data.get("tx_signature"),
                }
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("helius_trace_unavailable: %s", exc)

        # Try RPC router
        try:
            from app.domains.scanner.engines.rpc_router import (  # type: ignore[attr-defined]
                get_rpc_url,
                rpc_call,
            )

            url = get_rpc_url("solana")
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [token_address, {"limit": 1}],
            }
            sigs = await rpc_call(url, cast("Any", body))
            if sigs and isinstance(sigs, list) and len(sigs) > 0:
                oldest = sigs[-1]  # oldest signature
                tx_sig = oldest.get("signature", "")
                if tx_sig:
                    tx_body = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [tx_sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    }
                    tx_data = await rpc_call(url, tx_body)
                    if tx_data and isinstance(tx_data, dict):
                        fee_payer = (
                            tx_data.get("transaction", {})
                            .get("message", {})
                            .get("accountKeys", [{}])[0]
                            .get("pubkey", "")
                        )
                        if fee_payer and fee_payer != factory_address:
                            return {
                                "real_deployer": fee_payer,
                                "method": "solana_rpc_fee_payer",
                                "confidence": 0.9,
                                "evidence_tx": tx_sig,
                            }
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("solana_rpc_trace_unavailable: %s", exc)

    except Exception as exc:
        logger.warning("pumpfun_trace_failed", error=str(exc)[:100])

    return None


async def _trace_solana_deployer(
    token_address: str,
) -> dict[str, Any] | None:
    """Trace the real deployer for ANY Solana token via creation tx fee payer."""
    return await _trace_pumpfun_deployer(token_address, "")


async def _trace_evm_factory_deployer(
    chain: str,
    factory_address: str,
    deploy_tx: str | None = None,
) -> dict[str, Any] | None:
    """
    Trace EVM factory deployer.

    For factory contracts, the real deployer is the wallet that called
    createPair / createToken on the factory. We look at the factory's
    transaction history or use the deployer_events table.
    """
    try:
        from app.domains.scanner.engines.rpc_router import get_rpc_url, rpc_call  # type: ignore[attr-defined]

        url = get_rpc_url(chain)

        if deploy_tx:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getTransactionByHash",
                "params": [deploy_tx],
            }
            tx_data = await rpc_call(url, cast("Any", body))
            if tx_data and isinstance(tx_data, dict):
                tx_from = tx_data.get("from", "")
                if tx_from and tx_from.lower() != factory_address.lower():
                    return {
                        "real_deployer": tx_from.lower(),
                        "method": "evm_tx_from",
                        "confidence": 1.0,
                        "evidence_tx": deploy_tx,
                    }
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("evm_factory_trace_failed: %s", exc)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# DEPLOYER PROFILE STORAGE (Redis)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_redis() -> Any | None:
    """Get async Redis client."""
    try:
        return get_redis_async()
    except Exception:
        return None


def _profile_key(chain: str, address: str) -> str:
    return f"deployer:profile:{chain}:{address}"


def _tokens_key(chain: str, address: str) -> str:
    return f"deployer:tokens:{chain}:{address}"


def _rugged_key(chain: str, address: str) -> str:
    return f"deployer:rugged:{chain}:{address}"


def _last_seen_key(chain: str, address: str) -> str:
    return f"deployer:last_seen:{chain}:{address}"


async def get_deployer_profile(chain: str, address: str) -> DeployerProfile | None:
    """Retrieve a deployer's profile from Redis."""
    r = await _get_redis()
    if r is None:
        return None

    try:
        raw = await r.get(_profile_key(chain, address))
        if raw is None:
            return None
        data = json.loads(raw)
        # Also load live counts from Redis sets
        tokens_count = await r.scard(_tokens_key(chain, address)) or 0
        rugged_count = int(await r.get(_rugged_key(chain, address)) or 0)
        data["total_tokens_launched"] = max(data.get("total_tokens_launched", 0), tokens_count)
        data["rugged_tokens_count"] = max(data.get("rugged_tokens_count", 0), rugged_count)
        return DeployerProfile.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.debug("deployer_profile_parse_error: %s", exc)
        return None


async def save_deployer_profile(profile: DeployerProfile) -> bool:
    """Save or update a deployer profile in Redis."""
    r = await _get_redis()
    if r is None:
        return False

    try:
        key = _profile_key(profile.chain, profile.address)
        data = profile.to_dict()
        data["_updated_at"] = datetime.now(UTC).isoformat()

        await r.setex(key, DEPLOYER_PROFILE_TTL, json.dumps(data))
        await r.setex(_last_seen_key(profile.chain, profile.address), DEPLOYER_PROFILE_TTL, data["_updated_at"])
        return True
    except Exception as exc:
        logger.warning("deployer_profile_save_failed", error=str(exc)[:100])
        return False


async def update_deployer_profile(
    chain: str,
    deployer_address: str,
    *,
    token_address: str | None = None,
    token_rugged: bool = False,
    labels: list[str] | None = None,
    wallet_age_days: float | None = None,
) -> DeployerProfile:
    """
    Update a deployer's profile after scanning a token.

    Increments counts, adds labels, records the token.
    Creates the profile if it doesn't exist.
    """
    r = await _get_redis()

    profile = await get_deployer_profile(chain, deployer_address)
    if profile is None:
        profile = DeployerProfile(
            address=deployer_address,
            chain=chain,
            first_seen=datetime.now(UTC).isoformat(),
        )

    # Increment token counts
    if token_address and r:
        was_new = await r.sadd(_tokens_key(chain, deployer_address), token_address)
        if was_new:
            profile.total_tokens_launched += 1
            profile.active_tokens_count += 1

    if token_rugged:
        if r:
            await r.incr(_rugged_key(chain, deployer_address))
        profile.rugged_tokens_count += 1
        profile.active_tokens_count = max(0, profile.active_tokens_count - 1)

    if labels:
        for label in labels:
            if label not in profile.known_labels:
                profile.known_labels.append(label)

    if wallet_age_days is not None:
        profile.wallet_age_days = wallet_age_days

    profile.last_seen = datetime.now(UTC).isoformat()

    # Check if factory
    is_factory, factory_name = _is_factory_address(chain, deployer_address)
    profile.is_factory = is_factory
    profile.factory_name = factory_name

    await save_deployer_profile(profile)
    return profile


async def record_rugged_token(chain: str, deployer_address: str, token_address: str) -> None:
    """Mark a token as rugged and update the deployer's stats."""
    r = await _get_redis()
    if r:
        await r.srem(_tokens_key(chain, deployer_address), token_address)
        await r.incr(_rugged_key(chain, deployer_address))

    profile = await get_deployer_profile(chain, deployer_address)
    if profile:
        profile.rugged_tokens_count += 1
        profile.active_tokens_count = max(0, profile.active_tokens_count - 1)
        await save_deployer_profile(profile)


async def get_real_deployer(
    token_address: str,
    chain: str,
    claimed_deployer: str | None = None,
) -> dict[str, Any]:
    """
    Convenience: resolve the real deployer and optionally cache.

    Returns full resolution dict with real_deployer, method, confidence, etc.
    """
    # Check cache first
    r = await _get_redis()
    if r:
        cache_key = f"deployer:resolve:{chain}:{token_address}"
        cached = await r.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

    result = await resolve_deployer(
        token_address=token_address,
        chain=chain,
        deployer_address=claimed_deployer,
    )

    # Cache the result
    if r and result.get("real_deployer"):
        await r.setex(
            f"deployer:resolve:{chain}:{token_address}",
            RESOLVE_CACHE_TTL,
            json.dumps(result),
        )

    return result


# ── Module-level convenience class ──


class DeployerTracker:
    """High-level deployer tracker that combines resolution + profiling."""

    async def track(
        self,
        token_address: str,
        chain: str,
        deployer_address: str | None = None,
        token_data: dict[str, Any] | None = None,
    ) -> DeployerProfile:
        """Track a deployer: resolve real identity + update profile."""
        # Resolve real deployer
        resolved = await get_real_deployer(token_address, chain, deployer_address)
        real = resolved.get("real_deployer", deployer_address or token_address)
        is_factory = resolved.get("is_factory", False)

        # Build labels
        labels: list[str] = []
        if is_factory:
            labels.append("factory_deployer")
            labels.append(f"factory:{resolved.get('factory_name', 'unknown')}")
        if resolved.get("confidence", 0) > 0.9:
            labels.append("high_confidence_deployer")

        # Extract risk signals from token data
        data = token_data or {}
        is_rug = data.get("is_rug", data.get("rugged", False))
        risk_score = float(data.get("risk_score", data.get("safety_score", 50)) or 50)

        if risk_score < 30:
            labels.append("high_risk_deployer")
        elif risk_score < 50:
            labels.append("medium_risk_deployer")

        wallet_age = float(data.get("wallet_age_days", 0) or 0)

        # Update the real deployer's profile
        profile = await update_deployer_profile(
            chain=chain,
            deployer_address=real,
            token_address=token_address,
            token_rugged=bool(is_rug),
            labels=labels,
            wallet_age_days=wallet_age,
        )

        # If factory, also update the factory profile
        if is_factory and deployer_address and deployer_address != real:
            await update_deployer_profile(
                chain=chain,
                deployer_address=deployer_address,
                token_address=token_address,
                labels=["factory", f"launchpad:{resolved.get('factory_name', 'unknown')}"],
            )

        return profile

    async def get_profile(self, chain: str, address: str) -> DeployerProfile | None:
        """Get a stored deployer profile."""
        return await get_deployer_profile(chain, address)
