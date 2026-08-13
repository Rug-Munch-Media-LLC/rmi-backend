"""Static config constants for liquidity lock verification.

Extracted from ``liquidity_verifier.py`` (degodding split, 2026-08-14).
Env-read values resolved once at import time (unchanged behaviour — the
parent module read them at import too). Re-exported from the parent
module so the verifier class and any importer keep the same names.
"""

from __future__ import annotations

import os

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

# Etherscan-family network configs
ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}

MORALIS_DATA_URL = "https://deep-index.moralis.io/api/v2.2"
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")
