"""Capacity analysis and recommendations for vault providers."""

from typing import Any

from app.databus.vault.pool import VaultKeyPool


def capacity_report(pools: dict[str, VaultKeyPool]) -> dict[str, Any]:
    """Generate capacity analysis with recommendations."""
    bottlenecks = []
    recommendations = []
    providers = {}

    for name, pool in pools.items():
        total_keys = len(pool.keys)
        combined_rps = pool.rate_rps * total_keys
        combined_quota = pool.monthly_quota * total_keys if pool.monthly_quota > 0 else None
        active = sum(1 for k in pool.keys if k.is_available())

        status = "OK"
        if combined_rps < 10:
            status = "LOW_RPS"
            bottlenecks.append(f"{name}: {combined_rps:.0f} RPS from {total_keys} key(s)")
        if combined_quota and combined_quota < 10000:
            status = "LOW_QUOTA"
            bottlenecks.append(f"{name}: {combined_quota}/mo across {total_keys} key(s)")
        if total_keys == 1 and pool.rate_rps < 10:
            status = "SINGLE_KEY_LIMITED"
            recommendations.append(f"Get 2-3 more {name} free accounts for pool rotation")

        providers[name] = {
            "keys": total_keys,
            "active": active,
            "rps_per_key": pool.rate_rps,
            "combined_rps": combined_rps,
            "monthly_quota": combined_quota,
            "status": status,
        }

    # Auto-recommendations for free tier expansion
    free_tier_accounts = {
        "helius": "https://dev.helius.xyz - 3 free accounts = 75 RPS",
        "moralis": "https://admin.moralis.io - 3 free accounts = 75 RPS",
        "etherscan": "https://etherscan.io/register - multiple free keys",
        "birdeye": "https://birdeye.io - free tier available",
        "solscan": "https://solscan.io - Pro API free tier",
    }
    for prov, info in free_tier_accounts.items():
        if prov in providers and providers[prov]["status"] != "OK":
            recommendations.append(f"FREE: Create more {prov} accounts at {info}")

    return {
        "total_keys": sum(len(p.keys) for p in pools.values()),
        "active_keys": sum(sum(1 for k in p.keys if k.is_available()) for p in pools.values()),
        "providers": providers,
        "bottlenecks": bottlenecks,
        "recommendations": recommendations,
        "free_tier_opportunities": list(free_tier_accounts.keys()),
    }
