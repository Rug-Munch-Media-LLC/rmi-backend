"""
Cross-Chain Trace — Constants
==============================
Bridge contract addresses, entity label priorities, chain mappings.

All data is LOCAL — we OWN the ClickHouse tables. Zero external API calls.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════
# KNOWN BRIDGE CONTRACTS — cross-chain fund flow detection
# ═══════════════════════════════════════════════════════════════════
_BRIDGE_CONTRACTS: dict[str, set[str]] = {
    "ethereum": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x3ee18b2214aff97000d974cf647e7c347e8fa585",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5c7bcd1f0b4e5c2bc6b3c0fc2cd31332c21e9afc",
        "0x3666f603a164d1e1b1f6b8e8e9e6b3f6a7d8e9f6",
        "0x2796317b0ff8538f253012862c06787adfb8ceb6",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
        "0x6b7a87899490ece95443e979ca9485cbe7e71522",
        "0xee9dec2712cce65174b561151701bf54b99c24c8",
        "0x23ddd3e3692d1861ed57ede224608875809e127f",
    },
    "bsc": {
        "0x3a6d8ca21d1cf76f653a67577fa0d27453350dd8",
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x2796317b0ff8538f253012862c06787adfb8ceb6",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
    },
    "polygon": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
        "0x40ec5b33f54e0e8a33a975908c5ba1c14e5bbbdf",
        "0x7ffb3d3a245dc8f1552b2a5b0c1b7fe62f6af7b6",
    },
    "arbitrum": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
        "0x8315177ab297ba92a06054ce80a1b2f14d5b0a46",
        "0x4dbd4fc535ac27206064b68ffcf827b0a60bab3f",
    },
    "base": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
        "0x49048044d57e1c92a77f79988d21fa8faf74e97e",
    },
    "optimism": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
        "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1",
    },
    "avalanche": {
        "0x66a71dcef29a0ffbdbe3c6a460a3b5bc225cd675",
        "0x1a44076050125825900e736c501f859c50fe728c",
        "0x8731d54e9d02c286767d56ac03e8037c07e01e98",
        "0x150f94bb449740f3d8388c00abc98d89e3d3ed27",
        "0x98f3c9e6e3face36baad05fe09d375ef1464288b",
        "0x5427fefa711eff984124bfbb1ab4fbf5e8500bdc",
    },
}

_LABEL_PRIORITY: dict[str, int] = {
    "scam": 100,
    "exploiter": 100,
    "malicious": 100,
    "ponzi": 100,
    "phishing_scam": 100,
    "ransomware": 100,
    "mixer": 90,
    "sanctioned": 90,
    "exchange": 80,
    "bridge": 75,
    "defi": 70,
    "dex": 70,
    "money_laundering": 100,
    "darknet_market": 100,
    "high_risk": 85,
    "mev_bot": 60,
    "lending": 60,
    "gambling": 50,
    "p2p_service": 50,
}

_EVM_CHAINS: set[str] = {
    "ethereum",
    "eth",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "avalanche",
    "fantom",
    "gnosis",
}

_NATIVE_TOKEN_SYMBOLS: dict[str, str] = {
    "ethereum": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "arbitrum": "ETH",
    "optimism": "ETH",
    "base": "ETH",
    "avalanche": "AVAX",
    "fantom": "FTM",
    "gnosis": "xDAI",
}

_CHAIN_ID_MAP: dict[str, str] = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "bnb": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "gnosis": "gnosis",
}
