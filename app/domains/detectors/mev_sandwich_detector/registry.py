"""Known MEV bot registry."""
from __future__ import annotations

from typing import Any

KNOWN_MEV_BOTS: dict[str, dict[str, Any]] = {
    "0x0000000000007f150bd6f54c40a34d7c3d5e9f56": {"name": "EigenPhi Bot 1", "chain": "ethereum", "tags": ["sandwich", "arbitrage"]},
    "0x000000000000084e91743124a982076c59f10084": {"name": "Flashbots Searcher 1", "chain": "ethereum", "tags": ["arbitrage", "backrun"]},
    "0x00000000000041ebd394c64c0f0b7d164a3c6b11": {"name": "Jaredfromsubway", "chain": "ethereum", "tags": ["sandwich", "MEV"]},
    "0x000000000000d3b2b88b0b2d6d88bfb0a54d392": {"name": "PancakeBunny Searcher", "chain": "bsc", "tags": ["sandwich", "bsc"]},
    "0x0000000000000a24dd410c9b6b2f5f5f4a5d1e3f": {"name": "BSC MEV Bot 1", "chain": "bsc", "tags": ["sandwich", "bsc"]},
    "0x00000000000001f4d12a5c4d0f3c2e3b4a5f6e7d": {"name": "Arbitrum Searcher", "chain": "arbitrum", "tags": ["arbitrage", "backrun"]},
}
