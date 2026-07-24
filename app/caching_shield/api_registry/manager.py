"""
API Registry - Unified API Manager
====================================
UnifiedApiManager singleton, get_api_manager, capacity_report.
"""

from app.core.logging import get_logger

from .config import PROVIDER_REGISTRY
from .keys import KeyPool, discover_keys

logger = get_logger("api_registry")


class UnifiedApiManager:
    """Singleton manager for all API key pools."""

    def __init__(self) -> None:
        self.pools: dict[str, KeyPool] = {}
        self._load()

    def _load(self) -> None:
        pools = discover_keys()
        for provider, keys in pools.items():
            config = PROVIDER_REGISTRY.get(provider)
            if config:
                self.pools[provider] = KeyPool(provider, keys, config)

    def reload(self) -> None:
        self._load()

    async def acquire(self, provider: str):
        pool = self.pools.get(provider)
        if pool:
            return await pool.acquire()
        return None

    def mark_success(self, provider: str, key) -> None:
        pool = self.pools.get(provider)
        if pool:
            pool.mark_success(key)

    def mark_rate_limited(self, provider: str, key) -> None:
        pool = self.pools.get(provider)
        if pool:
            pool.mark_rate_limited(key)

    def mark_error(self, provider: str, key, error: str) -> None:
        pool = self.pools.get(provider)
        if pool:
            pool.mark_error(key, error)

    def get_pool(self, provider: str):
        return self.pools.get(provider)

    def list_providers(self) -> list[str]:
        return sorted(self.pools.keys())

    def capacity_report(self) -> dict:
        """Generate capacity analysis: RPS, quotas, bottlenecks."""
        providers = {}
        total_rps = 0.0
        bottlenecks = []

        for name, pool in self.pools.items():
            cfg = pool.config
            total_keys = len(pool.keys)
            combined_rps = cfg.rate_rps * total_keys
            combined_quota = cfg.monthly_quota * total_keys if cfg.monthly_quota > 0 else None
            total_rps += combined_rps

            status = "OK"
            if combined_rps < 10:
                status = "LOW_RPS"
                bottlenecks.append(f"{name}: only {combined_rps:.0f} RPS across {total_keys} key(s)")
            if combined_quota and combined_quota < 10000:
                status = "LOW_QUOTA"
                bottlenecks.append(
                    f"{name}: only {combined_quota}/mo across {total_keys} key(s)"
                )
            if total_keys == 1 and cfg.rate_rps < 10:
                status = "SINGLE_KEY_LIMITED"
                bottlenecks.append(
                    f"{name}: single key at {cfg.rate_rps} RPS - get more accounts"
                )

            providers[name] = {
                "keys": total_keys,
                "rps_per_key": cfg.rate_rps,
                "combined_rps": combined_rps,
                "monthly_quota": combined_quota,
                "status": status,
                "notes": cfg.notes,
            }

        return {
            "total_providers": len(providers),
            "total_combined_rps": round(total_rps, 1),
            "providers": providers,
            "bottlenecks": bottlenecks,
            "recommendations": _generate_recommendations(bottlenecks, providers),
        }


def _generate_recommendations(bottlenecks: list, providers: dict) -> list:
    """Generate actionable recommendations based on bottlenecks."""
    recs = []

    if any("helius" in b.lower() or "solana_tracker" in b.lower() for b in bottlenecks):
        recs.append("HIGH: Get 2-3 more Helius free accounts (email signup, instant key)")

    if any("solana_tracker" in b.lower() for b in bottlenecks):
        recs.append("HIGH: Get 1-2 more Solana Tracker free accounts (email signup)")

    if any("birdeye" in b.lower() for b in bottlenecks):
        recs.append("MEDIUM: Get 1 more Birdeye free account")

    if any("etherscan" in b.lower() for b in bottlenecks):
        recs.append("MEDIUM: Get 1 more Etherscan free account")

    if any("solscan" in b.lower() for b in bottlenecks):
        recs.append("LOW: Get 1 more Solscan Pro API key if needed for holder data")

    if any("coingecko" in b.lower() for b in bottlenecks):
        recs.append("LOW: CoinGecko Demo tier is generous at 30 RPS - likely sufficient")

    if not recs:
        recs.append("All providers have adequate capacity for current load.")

    return recs


# ═══════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════

_api_manager: UnifiedApiManager | None = None


def get_api_manager() -> UnifiedApiManager:
    global _api_manager
    if _api_manager is None:
        _api_manager = UnifiedApiManager()
    return _api_manager
