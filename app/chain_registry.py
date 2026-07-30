"""
SENTINEL - Multi-Chain Registry & Configuration
================================================
Centralized chain definitions, RPC endpoints, explorer APIs,
and data source configurations for all 13 supported chains.

Chains: solana, ethereum, base, bsc, arbitrum, polygon, avalanche,
        optimism, fantom, linea, zksync, scroll, mantle
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class ChainFamily(Enum):
    """Blockchain family classification."""

    SOLANA = "solana"  # Solana SPL tokens
    EVM = "evm"  # Ethereum Virtual Machine chains
    COSMOS = "cosmos"  # Not yet supported


@dataclass
class ChainConfig:
    """Full configuration for a supported blockchain."""

    name: str  # Short name (e.g. "ethereum", "base")
    chain_id: str  # Chain ID ("1", "8453", "solana")
    family: ChainFamily = ChainFamily.EVM
    display_name: str = ""  # Human name ("Ethereum", "Base")

    # RPC endpoints (primary, fallback, public)
    rpc_endpoints: list[str] = field(default_factory=list)

    # Block explorer API
    explorer_url: str = ""
    explorer_api_url: str = ""
    explorer_api_key_env: str = ""  # Env var for API key

    # Native token
    native_token_symbol: str = "ETH"
    native_token_decimals: int = 18

    # DEX data sources (DexScreener supports all)
    dexscreener_chain_id: str = ""  # Chain ID as DexScreener knows it

    # Known lockers / factory addresses
    known_lockers: dict[str, str] = field(default_factory=dict)

    # CEX hot wallets for this chain
    cex_hot_wallets: dict[str, list[str]] = field(default_factory=dict)

    # Block explorer API key env var
    api_key_env: str = ""

    def get_explorer_api_key(self) -> str:
        """Get the block explorer API key from environment."""
        return os.getenv(self.api_key_env, "")

    @property
    def is_solana(self) -> bool:
        return self.family == ChainFamily.SOLANA

    @property
    def is_evm(self) -> bool:
        return self.family == ChainFamily.EVM


# ─── Chain Registry ────────────────────────────────────────────────

CHAINS: dict[str, ChainConfig] = {
    "solana": ChainConfig(
        name="solana",
        chain_id="solana",
        family=ChainFamily.SOLANA,
        display_name="Solana",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://api.mainnet-beta.solana.com",  # Solana official public
            "https://solana-rpc.publicnode.com",  # PublicNode — free, no auth
            "https://solana.drpc.org",  # dRPC — free tier
            "https://rpc.ankr.com/solana",  # Ankr — free tier
            "https://solana.meowrpc.com",  # MeowRPC — free tier
            "https://solana.blockpi.network/v1/rpc/public",  # BlockPI — free tier
            # ── Keyed endpoints (fallback) ──
            "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy — free tier
            "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius — paid tier
        ],
        explorer_url="https://solscan.io",
        explorer_api_url="https://public-api.solscan.io",
        explorer_api_key_env="SOLSCAN_API_KEY",
        native_token_symbol="SOL",
        native_token_decimals=9,
        dexscreener_chain_id="solana",
        known_lockers={},
        cex_hot_wallets={
            "Binance": [
                "5tzF4VG5DB9R4PJJZdE3EGX6MHcY7K6uAcAFN7b7zoyF",
                "DRpbCBMxVnDK7maPM4Gqt5iQJ3U1QorZ3Nz8g7Aeu9p",
                "9WzDXMPQefAPQgxPaMkr2Fi8nY91fMjJY4kMN7AhN2qh",
                "QC4kUxtjAy5r1LFf6kPHF7GvNACm7UV2BcGmdv7E4Jj",
                "E5jRGgPnG4F7tHSyLQyazL3rbc8zP9G1NYJNx2GV5yRT",
                "8fXs4GQcZGJSJiMxH1L5tNp9R2VkS6wY7Z3A1BqFcD0E",
                "GJRs4FwHtemZ5ZEKxJ8x4D2gN8qFgXSP1M2wQrLmVk7h",
            ],
            "Coinbase": [
                "2AQ7xRF2Jq5k2C8RNqiP95jRskQk91nR6Nj4Q5xqG3x7",
                "9x2YQfGjg1Q7Q7Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1Q1",
            ],
            "OKX": [
                "5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5YK5Y",
                "5uNpdhHDuBoXd7RUAB4SzvqP5vQGrnQJn9f7xVU5RoXL",
            ],
            "Bybit": [
                "FcnWJ9y2xV6AKP5BrVK3hLmTM82QwpE4qXq7NgTsHDBP",
                "AC5RDfQFmDS1deWZos921JfqbQJzuyqqM21f7qNeYLkN",
            ],
            "Kraken": [
                "FWznbcNXWQuHTaweGHAb3k3MMw7gfX7Y9dZH3XPYkbWY",
            ],
            "KuCoin": [
                "BmFdpraXjbNqTGHeQ8QwPXy7sL9VLsKHvkSEWcYMYxVQ",
                "H8sMJSCNmYGCSVbY9u6VEU8SG7Qk2rPqFn3JntVnDpTE",
            ],
            "Gate.io": [
                "u6PJ8DtNzmFZ3rG3TzZioV5EqPbJYExq1En8HVNDjxR",
            ],
            "MEXC": [
                "ASTy5S1R2dq4WHCpVhBMbb4vLgWJdPBF5kG6zMTZBQsg",
            ],
            "Bitget": [
                "Aobp2URFvJk4iCBsiJfLkCxQ89rXHMLDR3MvUtJcNYrF",
            ],
            "Crypto.com": [
                "HyCHiyEqFGt3S1MJCqQyZ2mDn7rFQ8GWLN4Xx6VPoBRk",
            ],
            "HTX": [
                "GR4NVErnTnR3dYxGpVfPm1K9JVfZwGSmujqBBFLVxRzH",
            ],
            "Upbit": [
                "6zcmsePHoGpK6WjK7VGaMhhEu1QSGEdfSWd1qzvJJF5N",
                "3YfFWpQZfC1mJgGht6wWuXLgp6nQsTEtqQtEWQHJzp1d",
            ],
            "Bithumb": [
                "7BZsKJvQLcKHx8LMuN9M8rqpPmNr4qWGM9R8RcwWcTq1",
            ],
            "Bitfinex": [
                "FXxGxnmr58cJSiy4drkLeA41td4QtEby9pRZcxPYvPnN",
            ],
            "BitMEX": [
                "8ZPuSkQC9cETSWpNRuQYJHbZvbKmpPWYyduk8Ns1e9BX",
            ],
            "Korbit": [
                "DQiuRWA57ZyRsG6LEtQNkFV6JUCa6kTEKNfRvGJLQD4",
            ],
        },
        api_key_env="",
    ),
    "ethereum": ChainConfig(
        name="ethereum",
        chain_id="1",
        family=ChainFamily.EVM,
        display_name="Ethereum",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://eth.llamarpc.com",  # LlamaNodes — fast, free
            "https://rpc.ankr.com/eth",  # Ankr — free tier
            "https://eth.drpc.org",  # dRPC — distributed, free
            "https://ethereum-rpc.publicnode.com",  # PublicNode — free, no auth
            "https://1rpc.io/eth",  # 1RPC — privacy-first free
            "https://eth.meowrpc.com",  # MeowRPC — free tier
            "https://rpc.flashbots.net",  # Flashbots — public RPC
            "https://ethereum.blockpi.network/v1/rpc/public",  # BlockPI — free tier
            "https://cloudflare-eth.com",  # Cloudflare — free, reliable
            "https://eth-mainnet.public.blastapi.io",  # BlastAPI — free public
            "https://virginia.rpc.blxrbdn.com",  # BloXroute — US
            "https://uk.rpc.blxrbdn.com",  # BloXroute — EU
            "https://singapore.rpc.blxrbdn.com",  # BloXroute — Asia
            # ── Keyed endpoints (fallback) ──
            "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy — free tier
            "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura — free tier
        ],
        explorer_url="https://etherscan.io",
        explorer_api_url="https://api.etherscan.io/api",
        explorer_api_key_env="ETHERSCAN_API_KEY",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="ethereum",
        known_lockers={
            "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
            "0xe2fE530C047f2d85298b07D9333C05737f1435fB": "Team Finance",
            "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
        },
        cex_hot_wallets={
            "Binance": [
                "0x28C6c06298d514Db089934071355E5743bf21d60",
                "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",
                "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d",
                "0xF977814e90dA44bFA03b6295A0616a897441aceC",
                "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",
                "0x4976A4A02f38326660D17bf34b431dC6e2eb2327",
                "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F",
                "0x5a52E96BAcdaBb82fd05763E25335261B270Efcb",
                "0x9696f59E4d72E237BE84fFD425DCaD154Bf96976",
                "0x61189Da79177950A7272c88c6058B96D4Bcd6bE2",
            ],
            "Coinbase": [
                "0x503828976D22510aad0201ac7EC88293211D23Da",
                "0xdD2F458a4b46251B1dB5E2ed56bDDf8DB4Ed7E8e",
                "0xbf3EEfAC1073b93a2639fB1eD4aB7bC997d8732E",
                "0x71660c4005BA85C37ccec55d0C4493E3Fe753dBf",
                "0xA9D1e08C7793af67e9d92fe308d5697FB81d3E43",
                "0x3CD751E6b0078Be39333c1fbFDeC3a2b87Ab9C82",
            ],
            "OKX": [
                "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",
                "0x236F9F97e0E62388479bf9E5BA4889E46B0273c3",
                "0x8a612aB4cC1CBf5b37Bd6a0E8A0AcE5AF0e5B00F",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
                "0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFC73",
            ],
            "Kraken": [
                "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
                "0x0A869d79a7052C7f1b55a8EbAbbEa3420f0D1E13",
                "0xE853c56864A2ebe4576a807D26Fdc4A0adA51919",
                "0x267be1C1D684F78cb4F6a176C4911b741E4Ffdc0",
            ],
            "KuCoin": [
                "0x2B5634C42055806a59e9107ED44D43c426E58258",
                "0x679040D7223FdE9363AA2cF68B4C8A7394cc83C5",
            ],
            "Gate.io": [
                "0x0D0707963952f2fBA59dD06f2b425ace40b492Fe",
                "0x7793cD85c11A924478cF358d2b5DeC8C4f41acB0",
            ],
            "Bitfinex": [
                "0x876EabF441B2EE5B5b0554Fd502a8E0600950cFa",
                "0x1151314c646Ce4E0eFD76d1aF4760aE66a2Fe30e",
                "0x742d35Cc6634C0532925a3b844Bc9e7596bBDEdD",
            ],
            "Bitget": [
                "0x0639556F03714A74a5fEEaF5736a4A64f70Df206",
                "0x97dE9E01a1c95d3D09D9b29333dF4e5A50A1B61d",
            ],
            "MEXC": [
                "0x75e89d5979E4f6Fba9F97c104c2F0AFB3F1dcB88",
                "0x3CC936b795A188F0e246cBB2D74C5Bd190aeCF18",
            ],
            "HTX": [
                "0xeB2d2F1b8c558a40207669291Fda468E50c8a0Bb",
                "0x1871e9B1C47c0FF6FeA5bAAcB3A04F8068D60E7e",
            ],
            "Crypto.com": [
                "0x6262998Ced04146fA42253a5C0AF90CA02dfd2A3",
                "0x46340b20830761efd32832A74d7169B29FEB9758",
            ],
            "Gemini": [
                "0xd24400ae8BfEBb18cA49Be86258a3C749cf46853",
                "0x61EDCDf5bb737ADffE5043706e7C5bb1f1a56eEA",
                "0x07ee55aA48Bb72DCc597523F4A2632811C1bE3a4",
            ],
            "Korbit": [
                "0xF977814e90dA44bFA03b6295A0616a897441aceC",
            ],
            "Bittrex": [
                "0x94b1B6eB67A7D14b2ad2Ee93FeBA5457c5451eF2",
                "0x66F820a414680B5bcda5eAFc3d1F3C99b9F9B11f",
            ],
            "Bitstamp": [
                "0x00bdb5699745f5b860228c8f939abF1b9Ae374eD",
                "0x1522900B6daFac587d499a862861C0869Be6E428",
            ],
            "Upbit": [
                "0x390De26d772D2e2005C6d1D24afC902baC3ad635",
                "0x5e032243d507C743b061Ef021E2eC7bfA1Ec5277",
            ],
            "OKEx": [
                "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",
            ],
            "BinanceUS": [
                "0x61189Da79177950A7272c88c6058B96D4Bcd6bE2",
            ],
            "Poloniex": [
                "0x32Be343B94f860124dC4fEe278FDCBD38C102D88",
                "0x209c4784AB1E8183Cf58cA33cb740dcbF2fD3bF7",
            ],
            "Bithumb": [
                "0x44E9d0Bc5f3F4Bc54d9F6c1bd9fA09fD0a1e633c",
            ],
            "Coinone": [
                "0x167a9333BF582556f35Bd4d16a7E80a9525A9c3B",
            ],
            "LBank": [
                "0x1205E4F0D2f0225Fd4b3d8d7C1C9c7b9A1B3D5F7",
            ],
            "BitMart": [
                "0xE93381fB4c4F14bDa253907b18faD305D799241a",
            ],
            "WooX": [
                "0x0d83F81c79E8e93b30C4f5e9a4bDf4D3C5E7F9A1",
            ],
        },
        api_key_env="ETHERSCAN_API_KEY",
    ),
    "base": ChainConfig(
        name="base",
        chain_id="8453",
        family=ChainFamily.EVM,
        display_name="Base",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://base.llamarpc.com",  # LlamaNodes Base
            "https://rpc.ankr.com/base",  # Ankr Base
            "https://base.drpc.org",  # dRPC Base
            "https://base-rpc.publicnode.com",  # PublicNode Base
            "https://1rpc.io/base",  # 1RPC Base
            "https://base.meowrpc.com",  # MeowRPC Base
            "https://base.blockpi.network/v1/rpc/public",  # BlockPI Base
            "https://mainnet.base.org",  # Base official public
            # ── Keyed endpoints (fallback) ──
            "https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Base
        ],
        explorer_url="https://basescan.org",
        explorer_api_url="https://api.basescan.org/api",
        explorer_api_key_env="BASESCAN_API_KEY",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="base",
        known_lockers={
            "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
            "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
        },
        cex_hot_wallets={
            "Coinbase": [
                "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A",
                "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
            ],
            "Binance": [
                "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A",
            ],
            "OKX": [
                "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
            ],
        },
        api_key_env="BASESCAN_API_KEY",
    ),
    "bsc": ChainConfig(
        name="bsc",
        chain_id="56",
        family=ChainFamily.EVM,
        display_name="BSC",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://binance.llamarpc.com",  # LlamaNodes BSC
            "https://rpc.ankr.com/bsc",  # Ankr BSC
            "https://bsc.drpc.org",  # dRPC BSC
            "https://bsc-rpc.publicnode.com",  # PublicNode BSC
            "https://1rpc.io/bnb",  # 1RPC BSC
            "https://bsc.meowrpc.com",  # MeowRPC BSC
            "https://bsc.blockpi.network/v1/rpc/public",  # BlockPI BSC
            "https://bsc-dataseed.binance.org",  # Binance official
            "https://bsc-dataseed1.binance.org",  # Binance dataseed 1
            "https://bsc-dataseed2.binance.org",  # Binance dataseed 2
            "https://bsc-dataseed3.binance.org",  # Binance dataseed 3
            "https://bsc-dataseed1.defibit.io",  # Defibit dataseed
            "https://bsc-dataseed2.defibit.io",  # Defibit dataseed 2
            "https://bsc-dataseed1.ninicoin.io",  # Ninicoin dataseed
            "https://bsc-dataseed2.ninicoin.io",  # Ninicoin dataseed 2
            "https://bsc-mainnet.public.blastapi.io",  # BlastAPI BSC
            # ── Keyed endpoints (fallback) ──
            "https://bsc-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy BSC
        ],
        explorer_url="https://bscscan.com",
        explorer_api_url="https://api.bscscan.com/api",
        explorer_api_key_env="BSCSCAN_API_KEY",
        native_token_symbol="BNB",
        native_token_decimals=18,
        dexscreener_chain_id="bsc",
        known_lockers={
            "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
            "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
            "0x2D8E3580C6E428d4EaA069198D0B76ae0f98e43a": "Mudra",
        },
        cex_hot_wallets={
            "Binance": [
                "0xB38e8c17e38363aF6EbdCb3dAE12e0243582891D",
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "MEXC": ["0x51e6D27FA57373d8d4C256231241053a70Cb1d93"],
            "OKX": [
                "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3",
            ],
            "KuCoin": [
                "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
            ],
            "Gate.io": [
                "0x2A4EdF32D2b0ce008FeEB8EE2dA93B2dF34c3a4D",
            ],
            "Bybit": [
                "0xE2Fc0d06Fe01466fDDb948Cf38A8Ee98f36793B0",
            ],
            "Bitget": [
                "0x6B6a3E94FEb2B5FaF40bfD71B39C4D6A4E6F8e7c",
            ],
        },
        api_key_env="BSCSCAN_API_KEY",
    ),
    "arbitrum": ChainConfig(
        name="arbitrum",
        chain_id="42161",
        family=ChainFamily.EVM,
        display_name="Arbitrum",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://arbitrum.llamarpc.com",  # LlamaNodes Arbitrum
            "https://rpc.ankr.com/arbitrum",  # Ankr Arbitrum
            "https://arbitrum.drpc.org",  # dRPC Arbitrum
            "https://arbitrum-one-rpc.publicnode.com",  # PublicNode Arbitrum
            "https://1rpc.io/arb",  # 1RPC Arbitrum
            "https://arbitrum.meowrpc.com",  # MeowRPC Arbitrum
            "https://arbitrum.blockpi.network/v1/rpc/public",  # BlockPI Arbitrum
            "https://arb1.arbitrum.io/rpc",  # Offchain Labs official
            # ── Keyed endpoints (fallback) ──
            "https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Arbitrum
        ],
        explorer_url="https://arbiscan.io",
        explorer_api_url="https://api.arbiscan.io/api",
        explorer_api_key_env="ARBISCAN_API_KEY",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="arbitrum",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
                "0xF977814e90dA44bFA03b6295A0616a897441aceC",
            ],
            "Coinbase": [
                "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "Kraken": [
                "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
            ],
        },
        api_key_env="ARBISCAN_API_KEY",
    ),
    "polygon": ChainConfig(
        name="polygon",
        chain_id="137",
        family=ChainFamily.EVM,
        display_name="Polygon",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://polygon.llamarpc.com",  # LlamaNodes Polygon
            "https://rpc.ankr.com/polygon",  # Ankr Polygon
            "https://polygon.drpc.org",  # dRPC Polygon
            "https://polygon-rpc.com",  # Polygon official public
            "https://1rpc.io/matic",  # 1RPC Polygon
            "https://polygon.meowrpc.com",  # MeowRPC Polygon
            "https://polygon.blockpi.network/v1/rpc/public",  # BlockPI Polygon
            "https://polygon-bor-rpc.publicnode.com",  # PublicNode Polygon
            "https://polygon-mainnet.public.blastapi.io",  # BlastAPI Polygon
            # ── Keyed endpoints (fallback) ──
            "https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Polygon
        ],
        explorer_url="https://polygonscan.com",
        explorer_api_url="https://api.polygonscan.com/api",
        explorer_api_key_env="POLYGONSCAN_API_KEY",
        native_token_symbol="MATIC",
        native_token_decimals=18,
        dexscreener_chain_id="polygon",
        cex_hot_wallets={
            "Binance": [
                "0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245",
                "0xF977814e90dA44bFA03b6295A0616a897441aceC",
            ],
            "Coinbase": [
                "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
            "KuCoin": [
                "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
            ],
            "Gate.io": [
                "0x2A4EdF32D2b0ce008FeEB8EE2dA93B2dF34c3a4D",
            ],
        },
        api_key_env="POLYGONSCAN_API_KEY",
    ),
    "avalanche": ChainConfig(
        name="avalanche",
        chain_id="43114",
        family=ChainFamily.EVM,
        display_name="Avalanche",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://avalanche.llamarpc.com",  # LlamaNodes Avalanche
            "https://rpc.ankr.com/avalanche",  # Ankr Avalanche
            "https://avalanche.drpc.org",  # dRPC Avalanche
            "https://avalanche-c-chain-rpc.publicnode.com",  # PublicNode Avalanche
            "https://1rpc.io/avax/c",  # 1RPC Avalanche C-Chain
            "https://avalanche.meowrpc.com",  # MeowRPC Avalanche
            "https://avalanche.blockpi.network/v1/rpc/public",  # BlockPI Avalanche
            "https://api.avax.network/ext/bc/C/rpc",  # Avalanche official
            # ── Keyed endpoints (fallback) ──
            "https://avalanche-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura Avalanche
        ],
        explorer_url="https://snowtrace.io",
        explorer_api_url="https://api.snowtrace.io/api",
        explorer_api_key_env="SNOWTRACE_API_KEY",
        native_token_symbol="AVAX",
        native_token_decimals=18,
        dexscreener_chain_id="avalanche",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
                "0xF977814e90dA44bFA03b6295A0616a897441aceC",
            ],
            "Coinbase": [
                "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
            "KuCoin": [
                "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
            ],
        },
        api_key_env="SNOWTRACE_API_KEY",
    ),
    "optimism": ChainConfig(
        name="optimism",
        chain_id="10",
        family=ChainFamily.EVM,
        display_name="Optimism",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://optimism.llamarpc.com",  # LlamaNodes Optimism
            "https://rpc.ankr.com/optimism",  # Ankr Optimism
            "https://optimism.drpc.org",  # dRPC Optimism
            "https://optimism-rpc.publicnode.com",  # PublicNode Optimism
            "https://1rpc.io/op",  # 1RPC Optimism
            "https://optimism.meowrpc.com",  # MeowRPC Optimism
            "https://optimism.blockpi.network/v1/rpc/public",  # BlockPI Optimism
            "https://mainnet.optimism.io",  # Optimism official
            # ── Keyed endpoints (fallback) ──
            "https://opt-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Optimism
        ],
        explorer_url="https://optimistic.etherscan.io",
        explorer_api_url="https://api-optimistic.etherscan.io/api",
        explorer_api_key_env="ETHERSCAN_API_KEY",  # Shares Etherscan key
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="optimism",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "Coinbase": [
                "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
        },
        api_key_env="ETHERSCAN_API_KEY",
    ),
    "fantom": ChainConfig(
        name="fantom",
        chain_id="250",
        family=ChainFamily.EVM,
        display_name="Fantom",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://rpc.ankr.com/fantom",  # Ankr Fantom
            "https://fantom.drpc.org",  # dRPC Fantom
            "https://fantom-rpc.publicnode.com",  # PublicNode Fantom
            "https://1rpc.io/ftm",  # 1RPC Fantom
            "https://fantom.meowrpc.com",  # MeowRPC Fantom
            "https://rpc.fantom.network",  # Fantom official
            "https://fantom-mainnet.public.blastapi.io",  # BlastAPI Fantom
            "https://fantom.blockpi.network/v1/rpc/public",  # BlockPI Fantom
        ],
        explorer_url="https://ftmscan.com",
        explorer_api_url="https://api.ftmscan.com/api",
        explorer_api_key_env="FTMSCAN_API_KEY",
        native_token_symbol="FTM",
        native_token_decimals=18,
        dexscreener_chain_id="fantom",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "KuCoin": [
                "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
            ],
        },
        api_key_env="FTMSCAN_API_KEY",
    ),
    "linea": ChainConfig(
        name="linea",
        chain_id="59144",
        family=ChainFamily.EVM,
        display_name="Linea",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://rpc.linea.build",  # Linea official
            "https://linea-rpc.publicnode.com",  # PublicNode Linea
            "https://linea.drpc.org",  # dRPC Linea
            "https://1rpc.io/linea",  # 1RPC Linea
            "https://linea.blockpi.network/v1/rpc/public",  # BlockPI Linea
            "https://linea.meowrpc.com",  # MeowRPC Linea
            # ── Keyed endpoints (fallback) ──
            "https://linea-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura Linea
        ],
        explorer_url="https://lineascan.build",
        explorer_api_url="https://api.lineascan.build/api",
        explorer_api_key_env="",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="linea",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
        },
        api_key_env="",
    ),
    "zksync": ChainConfig(
        name="zksync",
        chain_id="324",
        family=ChainFamily.EVM,
        display_name="zkSync Era",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://mainnet.era.zksync.io",  # zkSync official
            "https://zksync-era.blockpi.network/v1/rpc/public",  # BlockPI zkSync
            "https://zksync.drpc.org",  # dRPC zkSync
            "https://1rpc.io/zksync-era",  # 1RPC zkSync Era
            "https://zksync.meowrpc.com",  # MeowRPC zkSync
            # ── Keyed endpoints (fallback) ──
            "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy zkSync
        ],
        explorer_url="https://explorer.zksync.io",
        explorer_api_url="https://block-explorer-api.mainnet.zksync.io/api",
        explorer_api_key_env="",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="zksync",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
        },
        api_key_env="",
    ),
    "scroll": ChainConfig(
        name="scroll",
        chain_id="534352",
        family=ChainFamily.EVM,
        display_name="Scroll",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://rpc.scroll.io",  # Scroll official
            "https://scroll-rpc.publicnode.com",  # PublicNode Scroll
            "https://scroll.drpc.org",  # dRPC Scroll
            "https://1rpc.io/scroll",  # 1RPC Scroll
            "https://scroll.blockpi.network/v1/rpc/public",  # BlockPI Scroll
            # ── Keyed endpoints (fallback) ──
            "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Scroll
        ],
        explorer_url="https://scrollscan.com",
        explorer_api_url="https://api.scrollscan.com/api",
        explorer_api_key_env="",
        native_token_symbol="ETH",
        native_token_decimals=18,
        dexscreener_chain_id="scroll",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "OKX": [
                "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
            ],
        },
        api_key_env="ETHERSCAN_API_KEY",
    ),
    "mantle": ChainConfig(
        name="mantle",
        chain_id="5000",
        family=ChainFamily.EVM,
        display_name="Mantle",
        rpc_endpoints=[
            # ── Free public endpoints (priority) ──
            "https://rpc.mantle.xyz",  # Mantle official
            "https://mantle-rpc.publicnode.com",  # PublicNode Mantle
            "https://mantle.drpc.org",  # dRPC Mantle
            "https://1rpc.io/mantle",  # 1RPC Mantle
            "https://mantle.blockpi.network/v1/rpc/public",  # BlockPI Mantle
            # ── Keyed endpoints (fallback) ──
            "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Mantle
        ],
        explorer_url="https://mantlescan.xyz",
        explorer_api_url="https://api.mantlescan.xyz/api",
        explorer_api_key_env="",
        native_token_symbol="MNT",
        native_token_decimals=18,
        dexscreener_chain_id="mantle",
        cex_hot_wallets={
            "Binance": [
                "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            ],
            "Bybit": [
                "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            ],
        },
        api_key_env="",
    ),
}

# ─── Convenience functions ─────────────────────────────────────────


def get_chain(chain_name: str) -> ChainConfig | None:
    """Get chain config by name (case-insensitive)."""
    return CHAINS.get(chain_name.lower())


def get_chain_family(chain_name: str) -> ChainFamily:
    """Determine if a chain is Solana, EVM, or other."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.family if cfg else ChainFamily.EVM


def is_solana(chain_name: str) -> bool:
    return get_chain_family(chain_name) == ChainFamily.SOLANA


def is_evm(chain_name: str) -> bool:
    return get_chain_family(chain_name) == ChainFamily.EVM


def resolve_chain_id(chain_name: str) -> str:
    """Resolve chain name to numeric or canonical ID."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.chain_id if cfg else chain_name


def list_chains() -> list[str]:
    """List all supported chain names."""
    return list(CHAINS.keys())


def get_chain_display(chain_name: str) -> str:
    """Get human-readable chain display name."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.display_name if cfg else chain_name.title()


def get_explorer_url(chain_name: str, address: str, tx: str = "") -> str:
    """Build explorer URL for an address or transaction."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return ""
    if tx:
        return f"{cfg.explorer_url}/tx/{tx}" if cfg.is_evm else f"{cfg.explorer_url}/tx/{tx}"
    if cfg.is_solana:
        return f"{cfg.explorer_url}/account/{address}"
    return f"{cfg.explorer_url}/address/{address}"


def get_rpc_urls(chain_name: str) -> list[str]:
    """Get list of RPC URLs for a chain (with env vars resolved)."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return []
    urls = []
    for url in cfg.rpc_endpoints:
        # Resolve ${VAR} references from environment
        import re

        resolved = re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), m.group(1)), url)
        urls.append(resolved)
    return urls


def get_explorer_api_config(chain_name: str) -> tuple[str, str]:
    """Get (api_url, api_key) for a chain's block explorer."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return ("", "")
    api_key = ""
    if cfg.api_key_env:
        api_key = os.getenv(cfg.api_key_env, "")
    return (cfg.explorer_api_url, api_key)


def get_cex_wallets(chain_name: str) -> dict[str, list[str]]:
    """Get known CEX hot wallets for a chain."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.cex_hot_wallets if cfg else {}


def get_known_lockers(chain_name: str) -> dict[str, str]:
    """Get known liquidity locker contracts for a chain (keys are lowercase-normalized)."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return {}
    return {k.lower(): v for k, v in cfg.known_lockers.items()}
