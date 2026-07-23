"""TokenDeployerFactory - creates the right deployer for a given chain."""

import os
from typing import ClassVar

from app.domains.tokens.deployer.base import ChainDeployer
from app.domains.tokens.deployer.evm import EVMDeployer
from app.domains.tokens.deployer.solana import SolanaDeployer
from app.domains.tokens.deployer.tron import TronDeployer


class TokenDeployerFactory:
    """Factory to create the right deployer for a given chain."""

    CHAIN_CONFIG: ClassVar[dict] = {
        "ethereum": {
            "deployer_class": EVMDeployer,
            "rpc_env": "ETH_RPC_URL",
            "key_env": "ETH_DEPLOYER_KEY",
            "default_rpc": "https://eth.llamarpc.com",
        },
        "base": {
            "deployer_class": EVMDeployer,
            "rpc_env": "BASE_RPC_URL",
            "key_env": "BASE_DEPLOYER_KEY",
            "default_rpc": "https://base.llamarpc.com",
        },
        "bsc": {
            "deployer_class": EVMDeployer,
            "rpc_env": "BSC_RPC_URL",
            "key_env": "BSC_DEPLOYER_KEY",
            "default_rpc": "https://bsc-dataseed.binance.org",
        },
        "solana": {
            "deployer_class": SolanaDeployer,
            "rpc_env": "SOLANA_RPC_URL",
            "key_env": "SOLANA_DEPLOYER_KEY",
            "default_rpc": "https://api.mainnet-beta.solana.com",
        },
        "tron": {
            "deployer_class": TronDeployer,
            "rpc_env": "TRON_RPC_URL",
            "key_env": "TRON_DEPLOYER_KEY",
            "default_rpc": "https://api.trongrid.io",
        },
    }

    @classmethod
    def get_deployer(cls, chain: str) -> ChainDeployer:
        """Get deployer for a chain."""
        chain = chain.lower()
        config = cls.CHAIN_CONFIG.get(chain)
        if not config:
            raise ValueError(f"Unsupported chain: {chain}. Supported: {list(cls.CHAIN_CONFIG.keys())}")
        rpc_url = os.getenv(config["rpc_env"], config["default_rpc"])
        private_key = os.getenv(config["key_env"], "")
        if not private_key:
            raise ValueError(f"No deployer key configured for {chain}. Set {config['key_env']} in .env")
        return config["deployer_class"](
            chain if config["deployer_class"] == EVMDeployer else "", private_key, rpc_url
        )

    @classmethod
    def list_supported_chains(cls) -> list[dict[str, str]]:
        """List all supported chains with their status."""
        chains = []
        for chain, config in cls.CHAIN_CONFIG.items():
            has_key = bool(os.getenv(config["key_env"], ""))
            chains.append({
                "chain": chain,
                "configured": has_key,
                "rpc_env": config["rpc_env"],
                "key_env": config["key_env"],
            })
        return chains
