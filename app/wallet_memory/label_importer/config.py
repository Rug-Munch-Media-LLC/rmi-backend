"""Static label importer config — paths, specs, constants."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DATA_DIR: Final = Path(os.getenv("RMI_DATA_DIR", "/app/data"))
LABELS_DIR: Final = DATA_DIR / "wallet-labels"
ETHERSCAN_COMBINED: Final = DATA_DIR / "etherscan_combined_labels.csv"
ETHERSCAN_PHISH_HACK: Final = DATA_DIR / "etherscan_phish_hack.csv"
CLEAN_DIR: Final = DATA_DIR / "wallet-labels-clean"

SOLANA_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/solana-wallet-and-program-labels"
)
BSC_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/bsc-ethereum-address-tags"
)
ETH_EXCHANGE_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/ethereum-exchange-and-dex-labels"
)
ETH_CLUSTER_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/"
    "crypto-wallet-address-labels/main/datasets/ethereum-cluster-exchange-labels"
)
ETH_CONTRACT_LABELS_REPO = (
    "https://raw.githubusercontent.com/ImMike/crypto-wallet-address-labels/main/datasets/ethereum-contract-name-labels"
)
OFAC_URL = "https://raw.githubusercontent.com/OFAC/crypto-sanctions-list/main/crypto-sanctions-list.json"

SOLANA_CSV_SPECS = [
    (
        "solana_cex_labels.csv",
        "solana",
        "address",
        {
            "label_type": "label_type",
            "label_subtype": "label_subtype",
            "name": "address_name",
            "project": "project_name",
        },
    ),
    (
        "solana_dapp_labels.csv",
        "solana",
        "address",
        {
            "name": "dapp_name",
        },
    ),
    (
        "solana_defi_labels.csv",
        "solana",
        "address",
        {
            "name": "protocol_name",
        },
    ),
]

ETH_CSV_SPECS = [
    (
        "phishing_scams.csv",
        "ethereum",
        "address",
        {
            "name": "etherscan_tag",
            "label_type": "etherscan_labels",
        },
    ),
    (
        "etherscan_malicious_labels.csv",
        "ethereum",
        "banned_address",
        {
            "name": "wallet_tag",
            "label_type": "data_source",
        },
    ),
    (
        "malicious_smart_contracts.csv",
        "ethereum",
        "contract_address",
        {
            "name": "contract_tag",
            "label_type": "source",
        },
    ),
]

WALLET_LABELS_DDL = """
CREATE TABLE IF NOT EXISTS wallet_labels (
    address         String,
    chain_id        LowCardinality(String),
    label_name      String DEFAULT '',
    label_category  LowCardinality(String) DEFAULT '',
    label_subtype   String DEFAULT '',
    source          LowCardinality(String) DEFAULT '',
    is_sanctioned   UInt8 DEFAULT 0,
    loaded_at       DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (address, chain_id, source);
"""

_EXCHANGE_WALLET_FILES = [
    ("exchange.csv", "exchange_wallets", "ethereum"),
    ("dex.csv", "dex_wallets", "ethereum"),
    ("icowallets.csv", "ico_wallets", "ethereum"),
    ("mining1.csv", "mining_wallets_part1", "ethereum"),
    ("mining2.csv", "mining_wallets_part2", "ethereum"),
    ("walletapp.csv", "walletapp_wallets", "ethereum"),
]

_CHAIN_ALIASES = {
    "eth": "ethereum",
    "eth_mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "bnb_chain": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "base": "base",
    "sol": "solana",
    "solana": "solana",
}
