"""Wallet labeler — thin adapter over the canonical rugmunch-scanner engine.

Cutover: rmi-backend `app/scoring/wallet_labeler.py` was superseded by
`rugmunch_scanner.scoring.wallet_labeler`. This module exists only to keep the
backward-compatible `app.scoring.wallet_labeler` import path alive for
`app/routers/scoring_router.py` and the `app.scoring` package re-exports.

The scanner package is the single source of truth. All public symbols are
re-exported explicitly; private (`_*`) helpers live in the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.scoring.wallet_labeler import (
    WalletLabel,
    WalletLabeler,
    add_wallet_label,
    get_wallet_labels,
    is_known_good,
    is_known_scammer,
    save_wallet_labels,
    tag_as_good_actor,
    tag_as_scammer,
    update_deployer_profile,
)

__all__ = [
    "WalletLabel",
    "WalletLabeler",
    "add_wallet_label",
    "get_wallet_labels",
    "is_known_good",
    "is_known_scammer",
    "save_wallet_labels",
    "tag_as_good_actor",
    "tag_as_scammer",
    "update_deployer_profile",
]
