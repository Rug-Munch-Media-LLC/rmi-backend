"""
RugMunch Multi-Factor Token Rating & Deployer Detection
=========================================================
The core intelligence engine: professional token scoring (0-100) across
five weighted categories, real deployer resolution (bypassing factory
addresses), and persistent deployer profiling via Redis.

Modules:
    token_rater       — 0-100 multi-factor token rating algorithm
    deployer_tracker  — Real deployer detection (PumpFun aware)
    wallet_labeler    — Wallet labeling + deployer profile storage

API:
    POST /api/v1/scoring/rate              — Rate any token
    GET  /api/v1/scoring/deployer/{chain}/{address} — Deployer profile
    GET  /api/v1/scoring/wallet/{chain}/{address}   — Wallet labels

Cutover: these three modules are now thin adapters over the canonical
`rugmunch_scanner.scoring` package (the single source of truth). The public
surface is unchanged; only the implementation source moved.
"""

from __future__ import annotations

from rugmunch_scanner.scoring.deployer_tracker import (
    DeployerProfile,
    DeployerTracker,
    get_real_deployer,
    resolve_deployer,
)
from rugmunch_scanner.scoring.token_rater import (
    RatingBreakdown,
    RatingGrade,
    TokenRater,
    TokenRatingResult,
    compute_rating,
)
from rugmunch_scanner.scoring.wallet_labeler import (
    WalletLabel,
    WalletLabeler,
    get_wallet_labels,
    update_deployer_profile,
)

__all__ = [
    "DeployerProfile",
    "DeployerTracker",
    "RatingBreakdown",
    "RatingGrade",
    "TokenRater",
    "TokenRatingResult",
    "WalletLabel",
    "WalletLabeler",
    "compute_rating",
    "get_real_deployer",
    "get_wallet_labels",
    "resolve_deployer",
    "update_deployer_profile",
]
