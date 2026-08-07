"""
Multi-Chain Airdrop Engine
==========================
Manages airdrop campaigns across multiple blockchains, including eligibility
checking, distribution, and claim verification.

Backward-compatibility shim: this module now lives in ``app.domains.airdrop.multichain``.
"""

from __future__ import annotations

from app.domains.airdrop.multichain import (
    AirdropTier,
    AntiGamingFilter,
    CrossChainHolder,
    CrossChainIdentityResolver,
    CustomSnapshotLoader,
    MultiChainAirdropCampaign,
    MultiChainAirdropManager,
    MultiChainSnapshotEngine,
    TokenSource,
    WeightedAirdropCalculator,
    create_crm_multichain_airdrop,
    create_crm_v2_airdrop_preset,
    create_one_to_one_airdrop,
)

__all__ = [
    "AirdropTier",
    "AntiGamingFilter",
    "CrossChainHolder",
    "CrossChainIdentityResolver",
    "CustomSnapshotLoader",
    "MultiChainAirdropCampaign",
    "MultiChainAirdropManager",
    "MultiChainSnapshotEngine",
    "TokenSource",
    "WeightedAirdropCalculator",
    "create_crm_multichain_airdrop",
    "create_crm_v2_airdrop_preset",
    "create_one_to_one_airdrop",
]
