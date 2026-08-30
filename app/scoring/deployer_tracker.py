"""Deployer tracker — thin adapter over the canonical rugmunch-scanner engine.

Cutover: rmi-backend `app/scoring/deployer_tracker.py` was superseded by
`rugmunch_scanner.scoring.deployer_tracker`. This module exists only to keep the
backward-compatible `app.scoring.deployer_tracker` import path alive for
`app/routers/scoring_router.py` and the `app.scoring` package re-exports.

The scanner package is the single source of truth. All public symbols are
re-exported explicitly; private (`_*`) helpers live in the scanner package.
"""

from __future__ import annotations

from rugmunch_scanner.scoring.deployer_tracker import (
    DeployerProfile,
    DeployerTracker,
    get_deployer_profile,
    get_real_deployer,
    record_rugged_token,
    resolve_deployer,
    save_deployer_profile,
    update_deployer_profile,
)

__all__ = [
    "DeployerProfile",
    "DeployerTracker",
    "get_deployer_profile",
    "get_real_deployer",
    "record_rugged_token",
    "resolve_deployer",
    "save_deployer_profile",
    "update_deployer_profile",
]
