"""Provider configuration: rate limits, quotas."""

from typing import Any

PROVIDER_CONFIG: dict[str, dict[str, Any]] = {
    "helius": {"rps": 25.0, "burst": 25, "quota": 0},
    "solana_tracker": {"rps": 3.0, "burst": 3, "quota": 2500},
    "birdeye": {"rps": 5.0, "burst": 5, "quota": 0},
    "solscan": {"rps": 5.0, "burst": 5, "quota": 0},
    "moralis": {"rps": 25.0, "burst": 25, "quota": 0},
    "etherscan": {"rps": 5.0, "burst": 5, "quota": 0},
    "alchemy": {"rps": 25.0, "burst": 25, "quota": 0},
    "quicknode": {"rps": 25.0, "burst": 25, "quota": 0},
    "coingecko_pro": {"rps": 30.0, "burst": 30, "quota": 0},
    "coinmarketcap": {"rps": 10.0, "burst": 10, "quota": 0},
    "arkham": {"rps": 5.0, "burst": 5, "quota": 100000},
    "goplus": {"rps": 5.0, "burst": 5, "quota": 0},
    "nansen": {"rps": 5.0, "burst": 5, "quota": 0},
    "dune": {"rps": 5.0, "burst": 5, "quota": 0},
    "openrouter": {"rps": 20.0, "burst": 30, "quota": 0},
    "deepseek": {"rps": 50.0, "burst": 100, "quota": 0},
    "gemini": {"rps": 15.0, "burst": 20, "quota": 0},
    "blockscout": {"rps": 5.0, "burst": 5, "quota": 0},
    "bitquery": {"rps": 5.0, "burst": 5, "quota": 0},
    "lunarcrush": {"rps": 5.0, "burst": 5, "quota": 0},
    "thegraph": {"rps": 5.0, "burst": 5, "quota": 0},
    "apify": {"rps": 5.0, "burst": 5, "quota": 0},
    "getxapi": {"rps": 1.0, "burst": 2, "quota": 100000},
}
