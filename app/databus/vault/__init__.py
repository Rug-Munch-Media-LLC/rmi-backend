"""DataBus Vault Package - Zero-Plaintext Key Management."""

from app.databus.vault.capacity import capacity_report
from app.databus.vault.config import PROVIDER_CONFIG
from app.databus.vault.core import DataBusVault
from app.databus.vault.loader import VaultKeyLoader
from app.databus.vault.models import KeyState, ManagedKey
from app.databus.vault.pool import VaultKeyPool
from app.databus.vault.registry import KEY_REGISTRY
from app.databus.vault.singleton import get_vault, get_vault_sync, resolve_key

__all__ = [
    # Registry & config
    "KEY_REGISTRY",
    "PROVIDER_CONFIG",
    # Core types
    "DataBusVault",
    "KeyState",
    "ManagedKey",
    "VaultKeyLoader",
    "VaultKeyPool",
    # Analysis
    "capacity_report",
    # Functions
    "get_vault",
    "get_vault_sync",
    "resolve_key",
]
