#!/usr/bin/env python3
"""Built-in known-contract directories (Tornado Cash, Uniswap, 1inch, DeFi admin, Gnosis).

Addresses are hardcoded on purpose: they are stable, well-known contracts used
as ground truth for both label ingestion and per-address enrichment lookups.
"""

from __future__ import annotations

# Tornado Cash mixer addresses (Ethereum mainnet)
TORNADO_CASH_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {"address": "0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc", "name": "Tornado.Cash: 0.1 ETH", "category": "mixer"},
        {"address": "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936", "name": "Tornado.Cash: 1 ETH", "category": "mixer"},
        {"address": "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf", "name": "Tornado.Cash: 10 ETH", "category": "mixer"},
        {"address": "0xa160cdab225685da1d56aa342ad8841c3b53f291", "name": "Tornado.Cash: 100 ETH", "category": "mixer"},
        {
            "address": "0xd4b88df4d29f5cedd6857912842cff3b20c8cfa3",
            "name": "Tornado.Cash: 0.1 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0xfd8610d20aa15b7b2e3be39b396a1bc3516c7144",
            "name": "Tornado.Cash: 1 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0x07687e702b410fa43f4cb4af7fa097918ffd2730",
            "name": "Tornado.Cash: 10 ETH (old)",
            "category": "mixer",
        },
        {
            "address": "0x23773e65ed146a459791799d01336db863f8cdc1",
            "name": "Tornado.Cash: 100 ETH (old)",
            "category": "mixer",
        },
    ],
    "bsc": [
        {"address": "0x1e34a77868e19a6647b1f2f47b51ed72dede95dd", "name": "Tornado.Cash: 10 BNB", "category": "mixer"},
        {"address": "0xdf231d99ff8b6c6cbf4e9b9a945cbacef9339178", "name": "Tornado.Cash: 100 BNB", "category": "mixer"},
    ],
}

# Uniswap Universal Router (Ethereum mainnet & L2s)
UNISWAP_ROUTERS: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x3fc91a3afd70395cd496c647d5a6cc9d4b2b7fad",
            "name": "Uniswap: Universal Router v1",
            "category": "dex",
        },
        {
            "address": "0xeF1c6E67703c7BD7107eed8303Fbe6EC2554BF6B",
            "name": "Uniswap: Universal Router v2",
            "category": "dex",
        },
        {
            "address": "0xEf1c6E67703c7BD7107eed8303Fbe6EC2554BF6B",
            "name": "Uniswap: Universal Router v3",
            "category": "dex",
        },
    ],
    "arbitrum": [
        {
            "address": "0x4C60051384bd2d3C01bfc845Cf5F4b44bcbE9de5",
            "name": "Uniswap: Universal Router (Arbitrum)",
            "category": "dex",
        },
    ],
    "optimism": [
        {
            "address": "0x4C60051384bd2d3C01bfc845Cf5F4b44bcbE9de5",
            "name": "Uniswap: Universal Router (Optimism)",
            "category": "dex",
        },
    ],
    "base": [
        {
            "address": "0x198EF79F1f515f02dFE9e3115eD9fC07183f02fC",
            "name": "Uniswap: Universal Router (Base)",
            "category": "dex",
        },
    ],
}

# 1inch Aggregation Router v5+ (across chains)
INCH_ROUTERS: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5",
            "category": "dex",
        },
        {
            "address": "0x1111111254eeb31977f5be12d5c7cee44f6e44e6",
            "name": "1inch: Aggregation Router v6",
            "category": "dex",
        },
    ],
    "bsc": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (BSC)",
            "category": "dex",
        },
    ],
    "polygon": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (Polygon)",
            "category": "dex",
        },
    ],
    "arbitrum": [
        {
            "address": "0x1111111254fb6c44bac0bed2854e76f90643097d",
            "name": "1inch: Aggregation Router v5 (Arbitrum)",
            "category": "dex",
        },
    ],
}

# Known DeFi protocol admin/multisig addresses
DEFI_ADMIN_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0xbe2ab3d3d8f6a32b96414ebbd865d1b4f1a6042b",
            "name": "Uniswap: Timelock (short)",
            "category": "defi_admin",
        },
        {
            "address": "0x1a9c8182c09f50c8318d769245bea52c32be35bc",
            "name": "Uniswap: Timelock (long)",
            "category": "defi_admin",
        },
        {
            "address": "0x10a19e7ee7d7f8a52822f6817de8ea18204f2e4f",
            "name": "Uniswap: Governance",
            "category": "defi_admin",
        },
        {
            "address": "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b",
            "name": "Compound: Timelock",
            "category": "defi_admin",
        },
        {
            "address": "0x6d903f6003cca6255d85cca4d3b5e5146dc33925",
            "name": "Aave: Governance v2",
            "category": "defi_admin",
        },
        {
            "address": "0xee56e2b3d491590b5b31738cc34d5232f378a8d5",
            "name": "Lido: DAO Treasury",
            "category": "defi_admin",
        },
        {
            "address": "0x3e40d73eb977dc6a537af587d48316fee66e9c8c",
            "name": "MakerDAO: Governance",
            "category": "defi_admin",
        },
    ],
}

# Known Gnosis Safe multisigs (prominent)
GNOSIS_SAFE_ADDRESSES: dict[str, list[dict[str, str]]] = {
    "ethereum": [
        {
            "address": "0x7a1c6abdcf83a5821d65d1e1f34360bda27f554c",
            "name": "Gnosis Safe: Treasury",
            "category": "multisig",
        },
    ],
}
