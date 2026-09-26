"""
Security provider chains — own-data chain-indexer (ClickHouse) cluster.

Chains: lite_chain_indexer_patterns, deployer_reputation,
deployer_fingerprint, whale_detection, volume_authenticity, holder_age,
holder_clusters, attack_detection, new_token_radar.

Every provider here is LOCAL tier over our own ClickHouse indexer (zero
external API cost), so none of these chains wrap themselves in an optional
import. ``token_launches`` is registered from ``token_security.py`` instead,
to preserve the original registration order. Split from ``security.py``
(see ``security/__init__.py``).
"""

from __future__ import annotations

from typing import Any

from ....base import Provider, ProviderChain, ProviderTier


def build_chain_indexer_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Build the nine LOCAL-tier chain-indexer chains (Phases 4 + C2/C3/C6/C7)."""
    chains: dict[str, ProviderChain] = {}
    chains.update(_lite_patterns(g))
    chains.update(_deployer_chains(g))
    chains.update(_flow_chains(g))
    chains.update(_holder_chains(g))
    chains.update(_attack_detection(g))
    chains.update(_new_token_radar(g))
    return chains


def _lite_patterns(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """On-chain behavioral scam patterns (holder concentration, p&d, mixer)."""
    return {
        "lite_chain_indexer_patterns": ProviderChain(
            "lite_chain_indexer_patterns",
            description="On-chain behavioral scam patterns via chain-indexer ClickHouse (holder concentration, pump-and-dump, deployer clusters, mixer interactions)",
            providers=[
                Provider(
                    "lite_chain_indexer_pattern_search",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_pattern_search"],
                    weight=15.0,
                    is_local=True,
                    description="ClickHouse-powered pattern detection for deployer clusters, holder concentration, pump-and-dump, and mixer interactions",
                ),
            ],
        )
    }


def _deployer_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """C7 — deployer reputation (0-100) and behavioral fingerprint."""
    return {
        "deployer_reputation": ProviderChain(
            "deployer_reputation",
            description="Deployer reputation (0-100) from chain-indexer ClickHouse deployer_events (rugpull/honeypot proxies, lifespan, velocity)",
            providers=[
                Provider(
                    "lite_chain_indexer_deployer_reputation",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_deployer_reputation"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse deployer_events — total tokens deployed, rugpull rate, honeypot rate, average token lifespan, velocity",
                ),
            ],
        ),
        "deployer_fingerprint": ProviderChain(
            "deployer_fingerprint",
            description="Deployer behavioral fingerprint (C7 v1) from chain-indexer ClickHouse — risk buckets (NEW/REPEAT_CLEAN/REPEAT_RUG/FARM/PROLIFIC), survival rate, median lifespan, funder analysis",
            providers=[
                Provider(
                    "lite_chain_indexer_deployer_fingerprint",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_deployer_fingerprint"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse deployer_events + dex_events + evm_transfers + wallet_labels — deployment stats, 7-day survival rate, median lifespan, funder profile (CEX/fresh/multiple), evidence",
                ),
            ],
        ),
    }


def _flow_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Whale wallet detection + wash-trading / volume authenticity."""
    return {
        "whale_detection": ProviderChain(
            "whale_detection",
            description="Whale wallet detection from chain-indexer ClickHouse dex_events (large flows, accumulating vs dumping)",
            providers=[
                Provider(
                    "lite_chain_indexer_whale_detect",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_whale_detect"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse dex_events — large transfers >$10K, whale wallets, concentration score, behavior flags",
                ),
            ],
        ),
        "volume_authenticity": ProviderChain(
            "volume_authenticity",
            description="Wash-trading / volume authenticity from chain-indexer ClickHouse (dex_events vs evm_transfers)",
            providers=[
                Provider(
                    "lite_chain_indexer_volume_authenticity",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_volume_authenticity"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse dex_events + evm_transfers — DEX vs transfer volume, wash patterns, authentic score",
                ),
            ],
        ),
    }


def _holder_chains(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """Holder age distribution (Phase 4) + C2 BubbleMaps-style clustering."""
    return {
        "holder_age": ProviderChain(
            "holder_age",
            description="Holder age distribution from chain-indexer ClickHouse evm_transfers (fresh wallet farm / sudden influx / stable)",
            providers=[
                Provider(
                    "lite_chain_indexer_holder_age",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_holder_age"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse evm_transfers — first-seen per holder, age buckets, fresh-wallet-farm detection",
                ),
            ],
        ),
        "holder_clusters": ProviderChain(
            "holder_clusters",
            description="BubbleMaps-style holder clustering — balances from evm_transfers deltas, shared-funder / same-block-buy / bundle cluster passes, wallet_labels entity tags",
            providers=[
                Provider(
                    "lite_chain_indexer_holder_clusters",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_holder_clusters"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse evm_transfers + dex_events + wallet_labels + deployer_events — holder concentration metrics and cluster map (C2)",
                ),
            ],
        ),
    }


def _attack_detection(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """C6 — time-based attack detection (sandwich, pump & dump, flash loans)."""
    return {
        "attack_detection": ProviderChain(
            "attack_detection",
            description="Time-based attack detection from chain-indexer ClickHouse dex_events — sandwich attacks, pump & dumps, and flash loans (zero external APIs)",
            providers=[
                Provider(
                    "lite_chain_indexer_attack_detection",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_attack_detection"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse dex_events — same-block sandwich triads, 24h pump & dump volume/price patterns, same-tx flash-loan repay patterns",
                ),
            ],
        )
    }


def _new_token_radar(g: dict[str, Any]) -> dict[str, ProviderChain]:
    """C3 — new token radar / early warning (ClickHouse-only, alerts included)."""
    return {
        "new_token_radar": ProviderChain(
            "new_token_radar",
            description="New token discovery + early warning from chain-indexer ClickHouse deployer_events — fast scan (honeypot + deployer fingerprint + liquidity), CRITICAL/HIGH/WARNING/INFO scoring, Telegram alerts",
            providers=[
                Provider(
                    "lite_chain_indexer_new_token_radar",
                    ProviderTier.LOCAL,
                    g["_lite_chain_indexer_new_token_radar"],
                    weight=20.0,
                    is_local=True,
                    description="ClickHouse deployer_events — poll new deployments, auto-scan each pair, score risk, alert CRITICAL/HIGH via alert_hooks",
                ),
            ],
        )
    }
