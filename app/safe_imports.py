"""
Safe Module Importer - Handles stub fallbacks for security_intel router
========================================================================

This module wraps security feature imports with graceful fallbacks to stubs
if dependencies aren't available, preventing full router import failures.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def safe_import(name: str, fallback_to_stub: bool = True) -> Any | None:
    """
    Safely import a module, optionally falling back to stub implementation.

    Args:
        name: Module path (e.g., 'web3' or 'app.contract_deepscan')
        fallback_to_stub: If True, returns stub module on ImportError

    Returns:
        Imported module, stub fallback, or None
    """
    try:
        module = __import__(name, fromlist=[""])
        logger.info(f"✓ Loaded: {name}")
        return module
    except ImportError:
        if fallback_to_stub:
            logger.warning(f"⚠ {name} not available, using stub fallback")
            return None
        raise


# ─── STUB CLASSES FOR CRITICAL DEPENDENCIES ───────────────────────


class StubContractDeepscan:
    """Stub contract scanning module."""

    def deep_scan_contract(self, address: str, chain: str = "base") -> dict[str, Any]:
        return {
            "address": address,
            "chain": chain,
            "scanned": False,
            "vulnerabilities": [],
            "checks": [],
            "status": "stub_mode",
        }

    def batch_deep_scan(self, addresses: list[str], chain: str = "base") -> dict[str, Any]:
        results = [self.deep_scan_contract(addr, chain) for addr in addresses]
        return {"contracts": results, "total": len(results)}


class StubCrossChainCorrelator:
    """Stub cross-chain correlation module."""

    class ChainFingerprint:
        def __init__(self, address: str, chain: str) -> None:
            self.address = address
            self.chain = chain

        def get_hash(self) -> str:
            return f"{self.chain}:{self.address}"

    class CrossChainCorrelator:
        def link_wallets(self, addresses: list[str], chains: list[str]) -> dict[str, Any]:
            return {"linked": True, "wallets": addresses, "chains": chains}

        def find_correlations(self, address: str, limit: int = 10) -> dict[str, Any]:
            return {"address": address, "correlations": [], "count": 0}


class StubCryptoGuard:
    """Stub crypto guard module."""

    def check_wallet_reputation(self, address: str) -> dict[str, Any]:
        return {"address": address, "reputation": "unknown", "risk_score": 0, "flags": []}

    def rate_limit_by_tier(self, tier: str) -> dict[str, int]:
        tiers = {"FREE": {"requests_per_hour": 10, "requests_per_day": 100}}
        return tiers.get(tier, {"requests_per_hour": 100, "requests_per_day": 1000})

    def require_crypto_auth(self, request: Any) -> dict[str, Any]:
        return {"authenticated": True}


# ─── STUB INSTANCES ───────────────────────────────────────────────

_stub_deepscan = StubContractDeepscan()
_stub_correlator = StubCrossChainCorrelator()
_stub_correlator_corr = _stub_correlator.CrossChainCorrelator()
_stub_crypto_guard = StubCryptoGuard()

# ─── FALLBACK LOADERS ─────────────────────────────────────────────


def get_contract_deepscan() -> tuple[Any, Any]:
    """Load contract deepscan with fallback to stub."""
    try:
        from app.contract_deepscan import batch_deep_scan, deep_scan_contract

        return deep_scan_contract, batch_deep_scan
    except ImportError:
        logger.warning("contract_deepscan not available, using stub")
        return (_stub_deepscan.deep_scan_contract, _stub_deepscan.batch_deep_scan)


def get_cross_chain_correlator() -> Any:
    """Load cross-chain correlator with fallback to stub."""
    try:
        from app.cross_chain_correlator import CrossChainCorrelator

        return CrossChainCorrelator
    except ImportError:
        logger.warning("cross_chain_correlator not available, using stub")
        return _stub_correlator_corr


def get_crypto_guard() -> tuple[Any, Any]:
    """Load crypto guard with fallback to stub."""
    try:
        from app.crypto_guard import (
            check_wallet_reputation,
            rate_limit_by_tier,
        )

        return check_wallet_reputation, rate_limit_by_tier
    except ImportError:
        logger.warning("crypto_guard not available, using stub")
        return (_stub_crypto_guard.check_wallet_reputation, _stub_crypto_guard.rate_limit_by_tier)


def get_sentinel_manager() -> Any:
    """Load mempool sentinel with fallback to stub."""
    try:
        from app.mempool_sentinel import SentinelManager

        return SentinelManager
    except ImportError:
        logger.warning("mempool_sentinel not available, using stub")
        return lambda: None


def get_wallet_anomaly_detector() -> Any:
    """Load wallet anomaly detector with fallback to stub."""
    try:
        from app.ml_anomaly import WalletAnomalyDetector

        return WalletAnomalyDetector
    except ImportError:
        logger.warning("ml_anomaly not available, using stub")
        return lambda: None


def get_token_anomaly_detector() -> Any:
    """Load token anomaly detector with fallback to stub."""
    try:
        from app.ml_anomaly import TokenMetricAnomalyDetector

        return TokenMetricAnomalyDetector
    except ImportError:
        logger.warning("ml_anomaly not available, using stub")
        return lambda: None
