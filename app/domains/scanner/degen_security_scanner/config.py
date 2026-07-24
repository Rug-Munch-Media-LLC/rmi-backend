"""
Degen Security Scanner — Configuration
========================================
API keys, program constants, and configuration for the degen scanner.
"""

import os

# ─── API Keys from environment ────────────────────────────────────────────
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")

# Known scam / bundler program IDs
BUNDLER_PROGRAMS: dict[str, str] = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun Bundler",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Launchpad Bundler",
    "MoonCVVNZFSYkqNXP6bxHLPL6QQxMagE3VCigVtg81o": "Moonshot Bundler",
}

# Known mixer / tumbler programs
MIXER_PROGRAMS: dict[str, list[str]] = {
    "Tornado": ["tornado", "mixer", "tumbler"],
    "Cyclone": ["cyclone"],
    "Samourai": ["samourai"],
}
