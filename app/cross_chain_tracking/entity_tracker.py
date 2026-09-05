"""
Cross-Chain Entity Tracker — Orchestrator
===========================================
Runs all cross-chain matching strategies and persists results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

from .deployer_matcher import DeployerMatcher

if TYPE_CHECKING:
    from .models import CrossChainProfile

logger = get_logger(__name__)


class CrossChainEntityTracker:
    """Orchestrates all cross-chain entity tracking strategies.

    Strategies run in order of confidence (highest first):
      1. Same-deployer detection (0.95 conf)
      2. EVM address portability (0.90 conf)
      3. Bridge mapping (0.80 conf)
      4. Behavioral fingerprinting (0.70 conf)
      5. Fund flow tracing (0.60 conf)
    """

    def __init__(self) -> None:
        self.deployer_matcher = DeployerMatcher()

    async def resolve(
        self,
        address: str,
        chain: str = "ethereum",
    ) -> CrossChainProfile:
        """Resolve a wallet address to its cross-chain entity profile.

        Uses ALL available signals, starting with highest confidence.
        """
        # Start with deployer matching (if the address is a deployer)
        profile = await self.deployer_matcher.resolve_deployer(address, chain)

        # FIXME(issue): Add EVM address portability expansion
        # FIXME(issue): Add bridge mapping from wallet_memory
        # FIXME(issue): Add behavioral fingerprinting
        # FIXME(issue): Add fund flow tracing

        return profile

    async def find_multi_chain_deployers(
        self,
        min_chains: int = 2,
        chain_filter: list[str] | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Find deployers active on multiple chains."""
        return [
            mcd.model_dump(mode="json")
            for mcd in await self.deployer_matcher.find_multi_chain_deployers(
                min_chains=min_chains,
                chain_filter=chain_filter,
                limit=limit,
            )
        ]

    async def run_batch_pipeline(
        self,
        min_chains: int = 2,
        chain_filter: list[str] | None = None,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Run the full batch pipeline: find + persist cross-chain entities.

        This is the main entry point for daily batch processing.
        """
        return await self.deployer_matcher.batch_persist(
            min_chains=min_chains,
            chain_filter=chain_filter,
            limit=limit,
            dry_run=dry_run,
        )


# ── Singleton ─────────────────────────────────────────────────────────

_tracker: CrossChainEntityTracker | None = None


def get_entity_tracker() -> CrossChainEntityTracker:
    global _tracker
    if _tracker is None:
        _tracker = CrossChainEntityTracker()
    return _tracker
