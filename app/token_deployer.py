"""token_deployer.py - DEPRECATED shim. Use app.domains.tokens.deployer.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/tokens/deployer/.
This shim re-exports the public surface for legacy callers.
"""

from __future__ import annotations
from app.domains.tokens.deployer import *  # noqa: F403
from app.domains.tokens.deployer import (  # noqa: F401
    ChainDeployer,
    DeploymentStorage,
    DeployParams,
    EVMDeployer,
    SolanaDeployer,
    TokenDeployerFactory,
    TokenDeployment,
    TronDeployer,
    _storage,
    get_storage,
    logger,
)
