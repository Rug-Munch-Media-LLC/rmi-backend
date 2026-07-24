"""Multi-chain airdrop package."""

from app.domains.airdrop.multichain.calculator import WeightedAirdropCalculator
from app.domains.airdrop.multichain.filters import AntiGamingFilter, CrossChainIdentityResolver
from app.domains.airdrop.multichain.loader import CustomSnapshotLoader
from app.domains.airdrop.multichain.manager import (
    MultiChainAirdropManager,
    create_crm_multichain_airdrop,
    create_crm_v2_airdrop_preset,
    create_one_to_one_airdrop,
)
from app.domains.airdrop.multichain.models import (
    AirdropTier,
    CrossChainHolder,
    MultiChainAirdropCampaign,
    TokenSource,
)
from app.domains.airdrop.multichain.snapshot import MultiChainSnapshotEngine

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
