"""
Wallet Factory - Chain Registry
=================================
ChainFamily enum, ChainConfig dataclass, and full SUPPORTED_CHAINS registry.
"""

from __future__ import annotations
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger("wallet_factory")

class ChainFamily:
    """Blockchain families - used for routing to the right generation logic."""
    BITCOIN = "bitcoin"
    EVM = "evm"
    SOLANA = "solana"
    TRON = "tron"
    ED25519 = "ed25519"
    SECP256K1_ALT = "secp256k1_alt"
    RIPPLE = "ripple"
    SUBSTRATE = "substrate"
    COSMOS = "cosmos"
    ALGORAND = "algorand"
    TEZOS = "tezos"
    MONERO = "monero"

@dataclass
class ChainConfig:
    """Configuration for a blockchain network wallet."""

    key: str
    name: str
    family: str
    symbol: str
    address_prefix: str = ""
    address_pattern: str = ""
    address_length: int = 0
    hd_path: str = ""
    testnet_hd_path: str = ""
    slip44: int = 0
    description: str = ""

SUPPORTED_CHAINS: dict[str, ChainConfig] = {
    "btc": ChainConfig(
        "btc", "Bitcoin", ChainFamily.BITCOIN, "BTC",
        hd_path="m/44'/0'/0'/0/0", slip44=0,
        address_pattern="^(1|3|bc1)[a-zA-Z0-9]{25,62}$",
        description="Bitcoin legacy (P2PKH) and SegWit",
    ),
    "btc-segwit": ChainConfig(
        "btc-segwit", "Bitcoin SegWit", ChainFamily.BITCOIN, "BTC",
        hd_path="m/49'/0'/0'/0/0", slip44=0,
        address_pattern="^(3|bc1)[a-zA-Z0-9]{25,62}$",
        description="Bitcoin SegWit (P2SH-P2WPKH)",
    ),
    "btc-native-segwit": ChainConfig(
        "btc-native-segwit", "Bitcoin Native SegWit", ChainFamily.BITCOIN, "BTC",
        hd_path="m/84'/0'/0'/0/0", slip44=0,
        address_pattern="^bc1[a-zA-Z0-9]{39,59}$",
        description="Bitcoin Native SegWit (P2WPKH) - lowest fees",
    ),
    "eth": ChainConfig(
        "eth", "Ethereum", ChainFamily.EVM, "ETH",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Ethereum mainnet",
    ),
    "base": ChainConfig(
        "base", "Base", ChainFamily.EVM, "ETH",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Coinbase Base L2",
    ),
    "polygon": ChainConfig(
        "polygon", "Polygon", ChainFamily.EVM, "POL",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Polygon PoS",
    ),
    "arbitrum": ChainConfig(
        "arbitrum", "Arbitrum", ChainFamily.EVM, "ETH",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Arbitrum One",
    ),
    "optimism": ChainConfig(
        "optimism", "Optimism", ChainFamily.EVM, "ETH",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Optimism",
    ),
    "avalanche": ChainConfig(
        "avalanche", "Avalanche C-Chain", ChainFamily.EVM, "AVAX",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Avalanche C-Chain",
    ),
    "bsc": ChainConfig(
        "bsc", "BNB Smart Chain", ChainFamily.EVM, "BNB",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="BNB Smart Chain",
    ),
    "fantom": ChainConfig(
        "fantom", "Fantom", ChainFamily.EVM, "FTM",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Fantom Opera",
    ),
    "gnosis": ChainConfig(
        "gnosis", "Gnosis Chain", ChainFamily.EVM, "XDAI",
        hd_path="m/44'/60'/0'/0/0", slip44=60,
        address_pattern="^0x[a-fA-F0-9]{40}$",
        description="Gnosis Chain",
    ),
    "sol": ChainConfig(
        "sol", "Solana", ChainFamily.SOLANA, "SOL",
        hd_path="m/44'/501'/0'/0'", slip44=501,
        address_pattern="^[1-9A-HJ-NP-Za-km-z]{32,44}$",
        description="Solana mainnet",
    ),
    "trx": ChainConfig(
        "trx", "TRON", ChainFamily.TRON, "TRX",
        hd_path="m/44'/195'/0'/0/0", slip44=195,
        address_pattern="^T[a-zA-Z0-9]{33}$",
        description="TRON mainnet",
    ),
    "doge": ChainConfig(
        "doge", "Dogecoin", ChainFamily.SECP256K1_ALT, "DOGE",
        hd_path="m/44'/3'/0'/0/0", slip44=3,
        address_pattern="^D[a-zA-Z0-9]{33}$",
        description="Dogecoin - much wow",
    ),
    "ltc": ChainConfig(
        "ltc", "Litecoin", ChainFamily.SECP256K1_ALT, "LTC",
        hd_path="m/44'/2'/0'/0/0", slip44=2,
        address_pattern="^(L|M|ltc1)[a-zA-Z0-9]{25,62}$",
        description="Litecoin - silver to Bitcoin's gold",
    ),
    "bch": ChainConfig(
        "bch", "Bitcoin Cash", ChainFamily.SECP256K1_ALT, "BCH",
        hd_path="m/44'/145'/0'/0/0", slip44=145,
        address_pattern="^(bitcoincash:|q|p)[a-zA-Z0-9]{25,62}$",
        description="Bitcoin Cash",
    ),
    "dash": ChainConfig(
        "dash", "Dash", ChainFamily.SECP256K1_ALT, "DASH",
        hd_path="m/44'/5'/0'/0/0", slip44=5,
        address_pattern="^X[a-zA-Z0-9]{33}$",
        description="Dash - digital cash",
    ),
    "zec": ChainConfig(
        "zec", "Zcash", ChainFamily.SECP256K1_ALT, "ZEC",
        hd_path="m/44'/133'/0'/0/0", slip44=133,
        address_pattern="^(t1|t3|zs1)[a-zA-Z0-9]{25,90}$",
        description="Zcash - privacy-focused",
    ),
    "ada": ChainConfig(
        "ada", "Cardano", ChainFamily.ED25519, "ADA",
        hd_path="m/1852'/1815'/0'/0/0", slip44=1815,
        address_pattern="^addr1[a-zA-Z0-9]{50,}$",
        description="Cardano Shelley",
    ),
    "near": ChainConfig(
        "near", "NEAR Protocol", ChainFamily.ED25519, "NEAR",
        hd_path="m/44'/397'/0'/0'/0'", slip44=397,
        address_pattern="^[a-f0-9]{64}$",
        description="NEAR Protocol",
    ),
    "sui": ChainConfig(
        "sui", "Sui", ChainFamily.ED25519, "SUI",
        hd_path="m/44'/784'/0'/0'/0'", slip44=784,
        address_pattern="^0x[a-fA-F0-9]{64}$",
        description="Sui Network",
    ),
    "aptos": ChainConfig(
        "aptos", "Aptos", ChainFamily.ED25519, "APT",
        hd_path="m/44'/637'/0'/0'/0'", slip44=637,
        address_pattern="^0x[a-fA-F0-9]{64}$",
        description="Aptos",
    ),
    "xrp": ChainConfig(
        "xrp", "Ripple XRP", ChainFamily.RIPPLE, "XRP",
        hd_path="m/44'/144'/0'/0/0", slip44=144,
        address_pattern="^r[a-zA-Z0-9]{24,34}$",
        description="Ripple XRP Ledger",
    ),
    "dot": ChainConfig(
        "dot", "Polkadot", ChainFamily.SUBSTRATE, "DOT",
        hd_path="", slip44=354,
        address_pattern="^1[a-zA-Z0-9]{47}$",
        description="Polkadot",
    ),
    "atom": ChainConfig(
        "atom", "Cosmos Hub", ChainFamily.COSMOS, "ATOM",
        hd_path="m/44'/118'/0'/0/0", slip44=118,
        address_pattern="^cosmos1[a-zA-Z0-9]{38}$",
        description="Cosmos Hub",
    ),
    "algo": ChainConfig(
        "algo", "Algorand", ChainFamily.ALGORAND, "ALGO",
        hd_path="m/44'/283'/0'/0'/0'", slip44=283,
        address_pattern="^[A-Z0-9]{58}$",
        description="Algorand",
    ),
    "xtz": ChainConfig(
        "xtz", "Tezos", ChainFamily.TEZOS, "XTZ",
        hd_path="m/44'/1729'/0'/0'", slip44=1729,
        address_pattern="^(tz1|tz2|tz3)[a-zA-Z0-9]{33}$",
        description="Tezos",
    ),
    "xmr": ChainConfig(
        "xmr", "Monero", ChainFamily.MONERO, "XMR",
        hd_path="", slip44=128,
        address_pattern="^[48][a-zA-Z0-9]{94}$",
        description="Monero - ultimate privacy coin",
    ),
}
