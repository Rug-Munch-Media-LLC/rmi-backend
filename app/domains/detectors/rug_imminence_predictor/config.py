"""Configuration and constants for rug imminence prediction."""
from __future__ import annotations
import os
from app.core.logging import get_logger
from app.domains.detectors.rug_imminence_predictor.models import ImminenceLevel

logger = get_logger(__name__)

DEFAULT_WEIGHTS = {
    "lp_health": 0.25,
    "deployer_risk": 0.20,
    "smart_money_flow": 0.15,
    "bundle_pattern": 0.15,
    "social_velocity": 0.10,
    "contract_risk": 0.10,
    "liquidity_migration": 0.05,
}

SIGNAL_NAMES = {
    "lp_health": "LP Health",
    "deployer_risk": "Deployer Risk",
    "smart_money_flow": "Smart Money Flow",
    "bundle_pattern": "Bundle Pattern",
    "social_velocity": "Social Velocity",
    "contract_risk": "Contract Risk",
    "liquidity_migration": "Liquidity Migration",
}

_KNOWN_LP_LOCKERS: list[str] | None = None

def _get_known_lp_lockers() -> list[str]:
    global _KNOWN_LP_LOCKERS
    if _KNOWN_LP_LOCKERS is not None:
        return _KNOWN_LP_LOCKERS
    env_lockers = os.getenv("RIP_KNOWN_LP_LOCKERS", "")
    if env_lockers.strip():
        _KNOWN_LP_LOCKERS = [a.strip() for a in env_lockers.split(",") if a.strip()]
        logger.info(f"Loaded {len(_KNOWN_LP_LOCKERS)} LP lockers from environment")
    else:
        _KNOWN_LP_LOCKERS = [
            "0x407993575c91ce7643a4d4cCACc9A98c36eE1BBE",
            "0x663a5c229c09b049e36dCc11a9B0d4a8Eb9db214",
            "0x74de5d12FC0fC274C1b0C6E9F58E60e0b4B7eCb1",
            "0xE2fE530C047f2d85298b07D9333C05737f1435FB",
            "0x6b8DA0E0E1d7b8B0E0E1d7b8B0E0E1d7b8B0E0E1d",
            "0x4e59b44847b379578588920cA78FbF26c0B4956C",
            "0xD152f549545093347A162Dce210e7293f1452150",
        ]
        logger.info("Using default LP lockers list")
    return _KNOWN_LP_LOCKERS

SCORE_THRESHOLDS = {
    ImminenceLevel.LOW: (0, 30),
    ImminenceLevel.MEDIUM: (30, 55),
    ImminenceLevel.HIGH: (55, 75),
    ImminenceLevel.CRITICAL: (75, 101),
}
