"""Shim — single source: rugmunch_scanner.scoring. STAGED — do NOT deploy until rpc_router decoupling (see CONSENSOLIDATION-STATUS.md)."""

from rugmunch_scanner.scoring import (  # noqa: F401
    DeployerProfile,
    DeployerTracker,
    RatingBreakdown,
    RatingGrade,
    TokenRater,
    TokenRatingResult,
    WalletLabel,
    WalletLabeler,
    compute_rating,
    get_real_deployer,
    get_wallet_labels,
    resolve_deployer,
    update_deployer_profile,
)
