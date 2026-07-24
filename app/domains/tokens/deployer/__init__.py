"""
Token Deployer - Multi-Chain Token Factory
===========================================
Backward-compatible re-exports from focused submodules.

Split into: models, base, evm, solana, tron, factory, storage.
"""

from __future__ import annotations

from app.domains.tokens.deployer.base import ChainDeployer
from app.domains.tokens.deployer.evm import EVMDeployer
from app.domains.tokens.deployer.factory import TokenDeployerFactory
from app.domains.tokens.deployer.models import DeployParams, TokenDeployment
from app.domains.tokens.deployer.solana import SolanaDeployer
from app.domains.tokens.deployer.storage import DeploymentStorage
from app.domains.tokens.deployer.tron import TronDeployer

# ── Singleton instance ──
_storage: DeploymentStorage | None = None

async def get_storage() -> DeploymentStorage:
    global _storage
    if _storage is None:
        _storage = DeploymentStorage()
    return _storage

__all__ = [
    "ChainDeployer",
    "DeployParams",
    "DeploymentStorage",
    "EVMDeployer",
    "SolanaDeployer",
    "TokenDeployerFactory",
    "TokenDeployment",
    "TronDeployer",
    "get_storage",
]
