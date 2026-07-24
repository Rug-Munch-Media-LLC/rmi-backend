"""
RIP — Main Predictor Class
Orchestrates the 7 signal analyzers and produces the final RIP score.
"""
import asyncio
import os
import re
import time
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.domains.detectors.rug_imminence_predictor_package.signals import (
    analyze_bundle_pattern,
    analyze_contract_risk,
    analyze_deployer_risk,
    analyze_liquidity_migration,
    analyze_lp_health,
    analyze_smart_money_flow,
    analyze_social_velocity,
    generate_recommendations,
    generate_warnings,
)
from app.domains.detectors.rug_imminence_predictor_package.types import (
    DEFAULT_WEIGHTS,
    SCORE_THRESHOLDS,
    SIGNAL_NAMES,
    VALID_CHAINS,
    RIPResult,
    SignalCategory,
)

logger = get_logger(__name__)

_KNOWN_LP_LOCKERS: list[str] | None = None

KNOWN_LP_LOCKERS_DEFAULT = [
    "0x407993575c91ce7643a4d4cCACc9A98c36eE1BBE",
    "0x663a5c229c09b049e36dCc11a9B0d4a8Eb9db214",
    "0x74de5d12FC0fC274C1b0C6E9F58E60e0b4B7eCb1",
    "0xE2fE530C047f2d85298b07D9333C05737f1435FB",
    "0x6b8DA0E0E1d7b8B0E0E1d7b8B0E0E1d7b8B0E0E1d",
    "0x4e59b44847b379578588920cA78FbF26c0B4956C",
    "0xD152f549545093347A162Dce210e7293f1452150",
]


def _get_known_lp_lockers() -> list[str]:
    """Get configured LP lockers from env, or use well-known defaults."""
    global _KNOWN_LP_LOCKERS
    if _KNOWN_LP_LOCKERS is not None:
        return _KNOWN_LP_LOCKERS
    env_lockers = os.getenv("RIP_KNOWN_LP_LOCKERS", "")
    if env_lockers.strip():
        _KNOWN_LP_LOCKERS = [a.strip() for a in env_lockers.split(",") if a.strip()]
        logger.info(f"Loaded {len(_KNOWN_LP_LOCKERS)} LP lockers from environment")
    else:
        _KNOWN_LP_LOCKERS = list(KNOWN_LP_LOCKERS_DEFAULT)
        logger.info("Using default LP lockers list")
    return _KNOWN_LP_LOCKERS


class RugImminencePredictor:
    """Predicts imminent rug pulls by fusing 7 signal categories."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self._verify_weights()
        self._cache: dict[str, RIPResult] = {}
        self._cache_ttl = 300  # 5 minutes
        logger.info(f"RugImminencePredictor initialized with {len(self.weights)} signals")

    def _verify_weights(self) -> None:
        """Ensure weights sum to ~1.0."""
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            logger.warning(f"Signal weights sum to {total:.2f}, not 1.0. Normalizing.")
            for k in self.weights:
                self.weights[k] /= total

    def _cache_key(self, address: str, chain: str) -> str:
        return f"{chain}:{address.lower()}"

    def _get_cached(self, address: str, chain: str) -> RIPResult | None:
        key = self._cache_key(address, chain)
        result = self._cache.get(key)
        if result:
            try:
                expiry = datetime.fromisoformat(result.last_updated)
                if datetime.now(UTC) < expiry:
                    return result
            except (ValueError, TypeError):
                pass
            del self._cache[key]
        return None

    def _set_cache(self, result: RIPResult) -> None:
        key = self._cache_key(result.token_address, result.chain)
        result.last_updated = (datetime.now(UTC) + timedelta(seconds=self._cache_ttl)).isoformat()
        self._cache[key] = result
        if len(self._cache) > 500:
            to_evict = sorted(self._cache.items(), key=lambda x: x[1].last_updated)[:len(self._cache) // 4]
            for k, _ in to_evict:
                del self._cache[k]
            logger.info(f"Cache eviction: removed {len(to_evict)} entries, {len(self._cache)} remaining")

    async def predict(self, address: str, chain: str = "base", force_refresh: bool = False) -> RIPResult:
        """Full rug imminence prediction for a token."""
        address = address.strip()
        chain = chain.strip().lower()

        if chain not in VALID_CHAINS:
            raise ValueError(f"Unsupported chain '{chain}'. Supported: {', '.join(sorted(VALID_CHAINS))}")

        if not re.match(r"^0x[a-fA-F0-9]{40}$", address) and not re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", address):
            raise ValueError(f"Invalid address format: '{address[:20]}...'. Expected 0x-prefixed EVM address or Solana base58.")

        t0 = time.time()

        if not force_refresh:
            cached = self._get_cached(address, chain)
            if cached:
                return cached

        result = RIPResult(token_address=address, chain=chain, last_updated=datetime.now(UTC).isoformat())

        for sig_id, weight in self.weights.items():
            result.signals[sig_id] = SignalCategory(name=SIGNAL_NAMES.get(sig_id, sig_id), weight=weight)

        analyzers = [
            analyze_lp_health(result),
            analyze_deployer_risk(result),
            analyze_smart_money_flow(result),
            analyze_bundle_pattern(result),
            analyze_social_velocity(result),
            analyze_contract_risk(result),
            analyze_liquidity_migration(result),
        ]
        await asyncio.gather(*analyzers, return_exceptions=True)

        result.score = sum(sig.weighted_contribution() for sig in result.signals.values())

        for level, (lo, hi) in SCORE_THRESHOLDS.items():
            if lo <= result.score < hi:
                result.imminence = level
                break

        generate_warnings(result)
        generate_recommendations(result)

        result.scan_duration_ms = (time.time() - t0) * 1000
        self._set_cache(result)
        return result
