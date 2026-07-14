"""
x402 Facilitator Smart Router
==============================
Routes payments to the best facilitator based on:
1. Chain support - only route to facilitators supporting the chain
2. Token support - match requested token (USDC, USDT, etc.)
3. Health - skip unhealthy facilitators
4. Priority - lower priority number = higher preference
5. Cost - prefer fee-free facilitators when available
6. Settlement speed - prefer instant > deferred

Usage:
    from app.domains.billing.facilitators.router import get_facilitator_router

    router = get_facilitator_router()
    result = await router.verify(payload, chain="base", token="USDC")
"""

from app.core.logging import get_logger
import time
from collections import defaultdict
from typing import Any

from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorRegistry,
    SettlementResult,
    VerificationResult,
    get_registry,
)

logger = get_logger("facilitator_router")


class FacilitatorRouter:
    """
    Smart router for multi-facilitator x402 payment verification.

    Routes requests to the best available facilitator with automatic
    fallback when the primary fails or is unhealthy.

    Selection factors (in order):
        1. Chain match (must support the requested chain)
        2. Token match (must support the requested token)
        3. Health status (skip unhealthy)
        4. Settlement type preference: fee_free > instant > deferred
        5. Priority score (lower = higher priority)
        6. Recent success rate (prefer proven facilitators)
    """

    def __init__(self, registry: FacilitatorRegistry | None = None):
        self._registry = registry or get_registry()
        self._health_cache: dict[str, tuple[bool, float]] = {}  # name → (healthy, timestamp)
        self._success_counts: dict[str, int] = defaultdict(int)
        self._failure_counts: dict[str, int] = defaultdict(int)
        self._health_ttl: int = 60  # seconds before re-checking health

    @property
    def registry(self) -> FacilitatorRegistry:
        return self._registry

    # ── Health Management ──────────────────────────────────────

    async def check_health(self, facilitator: Facilitator) -> bool:
        """Check facilitator health with caching."""
        name = facilitator.name
        cached = self._health_cache.get(name)
        now = time.time()

        if cached and (now - cached[1]) < self._health_ttl:
            return cached[0]

        try:
            healthy = await facilitator.health()
            self._health_cache[name] = (healthy, now)
            return healthy
        except Exception as e:
            logger.warning(f"Health check failed for {name}: {e}")
            self._health_cache[name] = (False, now)
            return False

    def is_healthy(self, facilitator: Facilitator) -> bool:
        """Check cached health status (no async call)."""
        name = facilitator.name
        cached = self._health_cache.get(name)
        if cached:
            healthy, timestamp = cached
            if (time.time() - timestamp) < self._health_ttl:
                return healthy
        return True  # Assume healthy if not yet checked

    # ── Scoring ────────────────────────────────────────────────

    def _score_facilitator(
        self,
        facilitator: Facilitator,
        chain_key: str,
        token_symbol: str | None = None,
    ) -> float:
        """
        Score a facilitator for a given request.
        Lower score = better match. < 0 = incompatible.
        """
        score = 0.0

        # 1. Chain compatibility (hard filter handled in _get_candidates)
        if not facilitator.supports_chain(chain_key):
            score += 1000  # Penalty for wrong chain

        # 2. Token compatibility
        if token_symbol:
            if facilitator.supports_token(token_symbol, chain_key):
                score -= 50  # Bonus for exact token match
            else:
                score += 500  # Penalty for token mismatch

        # 3. Health
        if not self.is_healthy(facilitator):
            score += 200

        # 4. Settlement type preference
        st = facilitator.settlement_type
        from app.domains.billing.facilitators.base import SettlementType

        if st == SettlementType.FEE_FREE:
            score -= 100  # Strong preference for free
        elif st == SettlementType.INSTANT:
            score -= 50  # Good
        elif st == SettlementType.PRECONFIRMATION:
            score -= 60  # Very fast
        elif st == SettlementType.DEFERRED:
            score += 20  # Acceptable

        # 5. Priority (lower = better)
        score += facilitator.priority * 0.5

        # 6. Recent reliability
        total = self._success_counts[facilitator.name] + self._failure_counts[facilitator.name]
        if total > 5:
            success_rate = self._success_counts[facilitator.name] / total
            score -= success_rate * 30  # Bonus for high success rate
            score += (1 - success_rate) * 60  # Penalty for low success rate

        return score

    def _get_candidates(
        self,
        chain_key: str,
        token_symbol: str | None = None,
    ) -> list[Facilitator]:
        """Get facilitator candidates for a request, optionally filtered by token."""
        candidates = self._registry.get_for_chain(chain_key)

        if not candidates:
            logger.warning(f"No facilitator supports chain: {chain_key}")
            return []

        if token_symbol:
            token_candidates = [f for f in candidates if f.supports_token(token_symbol, chain_key)]
            if token_candidates:
                candidates = token_candidates

        return candidates

    # ── Main Verification ──────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        chain_key: str = "base",
        token_symbol: str = "USDC",
        requirements: dict[str, Any] | None = None,
        preferred_facilitator: str | None = None,
    ) -> VerificationResult:
        """
        Verify a payment by routing to the best available facilitator.
        Falls back to next candidate if primary fails.

        Args:
            payload: x402 payment payload (parsed JSON)
            chain_key: Short chain key (e.g. 'base', 'solana', 'tron')
            token_symbol: Token symbol (e.g. 'USDC', 'USDT', 'USDD')
            requirements: Payment requirements dict
            preferred_facilitator: Optional facilitator name to try first

        Returns:
            VerificationResult - verified=True on success
        """
        candidates = self._get_candidates(chain_key, token_symbol)

        if not candidates:
            return VerificationResult(
                verified=False,
                reason=f"No facilitator available for {chain_key}/{token_symbol}",
                chain=chain_key,
                token=token_symbol,
            )

        # Sort by score (best first)
        scored = [(self._score_facilitator(f, chain_key, token_symbol), f) for f in candidates]

        # If preferred facilitator specified, bump it to first
        if preferred_facilitator:
            for i, (_s, f) in enumerate(scored):
                if f.name == preferred_facilitator:
                    scored[i] = (-9999, f)  # Force first
                    break

        scored.sort(key=lambda x: x[0])

        logger.debug(
            f"Facilitator candidates for {chain_key}/{token_symbol}: "
            + ", ".join(f"{f.name}({s:.1f})" for s, f in scored[:5])
        )

        errors = []
        for score, facilitator in scored:
            if score >= 1000:  # Incompatible, skip
                continue

            # Async health check (respects cache)
            healthy = await self.check_health(facilitator)
            if not healthy:
                logger.debug(f"Skipping unhealthy facilitator: {facilitator.name}")
                continue

            try:
                result = await facilitator.verify(payload, requirements)

                if result.verified:
                    self._success_counts[facilitator.name] += 1
                    logger.info(
                        f"Payment verified via {facilitator.name} "
                        f"({chain_key}, {result.token or token_symbol}, "
                        f"amount={result.amount})"
                    )
                    return result
                else:
                    self._failure_counts[facilitator.name] += 1
                    errors.append((facilitator.name, result.reason))
                    logger.debug(f"{facilitator.name} rejected: {result.reason} - trying next")

            except Exception as e:
                self._failure_counts[facilitator.name] += 1
                errors.append((facilitator.name, str(e)))
                logger.warning(f"{facilitator.name} error: {e} - trying next")

        # All facilitators failed
        error_summary = "; ".join(f"{name}: {err}" for name, err in errors)
        return VerificationResult(
            verified=False,
            reason=f"All facilitators failed for {chain_key}/{token_symbol}: {error_summary}",
            chain=chain_key,
            token=token_symbol,
        )

    # ── Settlement ─────────────────────────────────────────────

    async def settle(
        self,
        verification_result: VerificationResult,
        chain_key: str | None = None,
    ) -> SettlementResult:
        """
        Settle a verified payment using the facilitator that verified it.
        """
        facilitator_name = verification_result.facilitator
        if not facilitator_name:
            return SettlementResult(
                settled=False,
                reason="No facilitator in verification result",
                chain=chain_key,
            )

        facilitator = self._registry.get(facilitator_name)
        if not facilitator:
            return SettlementResult(
                settled=False,
                reason=f"Facilitator {facilitator_name} not found in registry",
                chain=chain_key,
            )

        try:
            payment_data = {
                "tx_hash": verification_result.tx_hash,
                "amount": verification_result.amount,
                "chain": verification_result.chain or chain_key,
                "token": verification_result.token,
                "payer": verification_result.payer,
                "settlement_id": verification_result.settlement_id,
            }
            return await facilitator.settle(payment_data)
        except Exception as e:
            return SettlementResult(
                settled=False,
                reason=f"Settlement failed: {e}",
                facilitator=facilitator_name,
                chain=chain_key,
            )

    # ── Stats ──────────────────────────────────────────────────

    def record_success(self, facilitator_name: str) -> None:
        self._success_counts[facilitator_name] += 1

    def record_failure(self, facilitator_name: str) -> None:
        self._failure_counts[facilitator_name] += 1

    def get_stats(self) -> dict[str, Any]:
        """Get routing statistics for dashboard."""
        stats = {}
        for name, facilitator in self._registry._facilitators.items():
            success = self._success_counts.get(name, 0)
            failure = self._failure_counts.get(name, 0)
            total = success + failure
            stats[name] = {
                "priority": facilitator.priority,
                "type": facilitator.facilitator_type.value,
                "settlement": facilitator.settlement_type.value,
                "chains": facilitator.supported_networks.chains,
                "healthy": self.is_healthy(facilitator),
                "success": success,
                "failure": failure,
                "total": total,
                "success_rate": round(success / total, 3) if total > 0 else None,
            }
        return stats


# ── Singleton ───────────────────────────────────────────────────

_router: FacilitatorRouter | None = None


def get_facilitator_router() -> FacilitatorRouter:
    """Get the global facilitator router instance."""
    global _router
    if _router is None:
        _router = FacilitatorRouter()
    return _router
