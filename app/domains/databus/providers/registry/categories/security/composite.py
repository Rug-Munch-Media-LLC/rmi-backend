"""
Security provider chains — composite multi-provider scan chains.

Chains: tier_comparison, threat_check, contract_scan, sentinel_deep, rugmap.
The three SENTINEL/intel/RAG composites share one provider trio in three
different weight orders, so they are built from a single helper to keep the
orderings impossible to drift. ``tier_comparison`` comes from the optional
``app.databus.data_quality`` module. Split from ``security.py`` (see
``security/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from ....base import Provider, ProviderChain, ProviderTier

logger = get_logger("security")

_THREAT_RAG_DESCRIPTION = "Scraped scam reports / threat intel from pryscraper Postgres"


def build_composite_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build the composite scan chains (tier comparison + the SENTINEL trios)."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_tier_comparison())
    chains.update(_sentinel_composites(g))
    chains.update(_rugmap(g))
    return chains


def _tier_comparison() -> dict[str, ProviderChain]:
    """Competitive tier comparison from ``app.databus.data_quality`` (optional)."""
    try:
        from app.databus.data_quality import get_tier_comparison
    except ImportError as e:
        logger.debug(f"build_security_chains failed: {e}")
        return {}
    return {
        "tier_comparison": ProviderChain(
            "tier_comparison",
            description="Competitive tier comparison",
            providers=[
                Provider(
                    "tier_compare",
                    ProviderTier.LOCAL,
                    get_tier_comparison,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )
    }


def _sentinel_trio(g: dict[str, Any], sentinel_w: float, intel_w: float, rag_w: float) -> list[Provider]:
    """SENTINEL → scraped intel → RAG, at the three requested weights.

    Every provider returns None on failure, so ``ProviderChain.fetch()`` falls
    through to the next one; the per-provider names are the keys the circuit
    breaker and rate limiter key on, so they must stay stable across the
    three composites.
    """
    return [
        Provider(
            "sentinel_scanner",
            ProviderTier.LOCAL,
            g["_passthrough_scanner"],
            weight=sentinel_w,
            rate_limit_rps=2.0,
        ),
        Provider(
            "scraped_intel_local",
            ProviderTier.LOCAL,
            g["_scraped_intel_provider"],
            weight=intel_w,
            is_local=True,
            description=_THREAT_RAG_DESCRIPTION,
        ),
        Provider(
            "local_rag",
            ProviderTier.LOCAL,
            g["_passthrough_rag"],
            weight=rag_w,
        ),
    ]


def _sentinel_composites(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """threat_check, contract_scan, sentinel_deep — same trio, three orders.

    threat_check  : 15/8/5  (scraped intel leads, SENTINEL second)
    contract_scan : 10/8/3  (SENTINEL leads)
    sentinel_deep : 10/8/5  (SENTINEL leads, RAG heavier than contract_scan)
    """
    return {
        "threat_check": ProviderChain(
            data_type="threat_check",
            description="Threat intelligence check — scraped intel → SENTINEL scan → RAG known scams",
            providers=[
                Provider(
                    "scraped_intel_local",
                    ProviderTier.LOCAL,
                    g["_scraped_intel_provider"],
                    weight=15.0,
                    is_local=True,
                    description=_THREAT_RAG_DESCRIPTION,
                ),
                Provider(
                    "sentinel_scanner",
                    ProviderTier.LOCAL,
                    g["_passthrough_scanner"],
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "local_rag",
                    ProviderTier.LOCAL,
                    g["_passthrough_rag"],
                    weight=5.0,
                ),
            ],
        ),
        "contract_scan": ProviderChain(
            data_type="contract_scan",
            description="Deep contract audit — SENTINEL scan → scraped intel → RAG",
            providers=_sentinel_trio(g, 10.0, 8.0, 3.0),
        ),
        "sentinel_deep": ProviderChain(
            data_type="sentinel_deep",
            description="Full threat scan — SENTINEL deep scan → scraped intel → RAG known scams",
            providers=_sentinel_trio(g, 10.0, 8.0, 5.0),
        ),
    }


def _rugmap(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Holder concentration map — ClickHouse holders → DexScreener → Birdeye."""
    return {
        "rugmap": ProviderChain(
            data_type="rugmap",
            description="Holder concentration map — ClickHouse holders → DexScreener holders → Birdeye",
            providers=[
                Provider(
                    "lite_chain_indexer_holders",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_holders"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse evm_transfers aggregation — holder distribution from transfer activity",
                ),
                Provider(
                    "dexscreener_holders",
                    ProviderTier.FREE_API,
                    g["_dexscreener_holders"],
                    weight=8.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "birdeye_overview_api",
                    ProviderTier.FREEMIUM,
                    g["_birdeye_overview"],
                    weight=5.0,
                    rate_limit_rps=2.0,
                    requires_key=True,
                    key_env="BIRDEYE_API_KEY",
                    monthly_quota=50000,
                ),
            ],
        )
    }
