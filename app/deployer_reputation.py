"""Backward-compatibility shim — moved to app.domains.scanner.deployer_reputation."""

from __future__ import annotations

from app.domains.scanner.deployer_reputation import (
    annotations,
    Any,
    numpy,
    get_logger,
    logger,
    SIGNALS,
    KNOWN_PATTERNS,
    build_behavioral_fingerprint,
    score_deployer_risk,
)


__all__ = [
    "annotations",
    "Any",
    "numpy",
    "get_logger",
    "logger",
    "SIGNALS",
    "KNOWN_PATTERNS",
    "build_behavioral_fingerprint",
    "score_deployer_risk",
]
