"""Backward-compatibility shim — moved to app.domains.scanner.deployer_reputation."""

from __future__ import annotations

from app.domains.scanner.deployer_reputation import (
    KNOWN_PATTERNS as KNOWN_PATTERNS,
    SIGNALS as SIGNALS,
    build_behavioral_fingerprint as build_behavioral_fingerprint,
    logger as logger,
    score_deployer_risk as score_deployer_risk,
)

__all__ = [
    "KNOWN_PATTERNS",
    "SIGNALS",
    "build_behavioral_fingerprint",
    "logger",
    "score_deployer_risk",
]
