"""Backward-compatibility shim — moved to app.domains.scanner.deployer_reputation."""

from __future__ import annotations

from app.domains.scanner.deployer_reputation import (
    KNOWN_PATTERNS,
    SIGNALS,
    Any,
    annotations,
    build_behavioral_fingerprint,
    get_logger,
    logger,
    numpy,
    score_deployer_risk,
)

__all__ = [
    "KNOWN_PATTERNS",
    "SIGNALS",
    "Any",
    "annotations",
    "build_behavioral_fingerprint",
    "get_logger",
    "logger",
    "numpy",
    "score_deployer_risk",
]
