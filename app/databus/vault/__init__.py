"""DataBus Vault Package - Zero-Plaintext Key Management."""

from app.databus.vault.loader import VaultKeyLoader
from app.databus.vault.models import KeyState, ManagedKey
from app.databus.vault.pool import VaultKeyPool
from app.databus.vault.registry import KEY_REGISTRY, PROVIDER_CONFIG
from app.databus.vault_core import (
    DataBusVault,
    get_vault,
    get_vault_sync,
    resolve_key,
)

__all__ = [
    # Core types
    "DataBusVault",
    "ManagedKey",
    "KeyState",
    "VaultKeyPool",
    "VaultKeyLoader",
    # Registry & config
    "KEY_REGISTRY",
    "PROVIDER_CONFIG",
    # Functions
    "get_vault",
    "get_vault_sync",
    "resolve_key",
]