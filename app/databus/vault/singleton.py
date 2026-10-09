"""Singleton access functions for DataBusVault."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.databus.vault.core import DataBusVault

_vault: DataBusVault | None = None


def _make_vault() -> DataBusVault:
    """Import the vault lazily - core imports this module, so a top-level import would cycle."""
    from app.databus.vault.core import DataBusVault as _Vault

    return _Vault()


async def get_vault() -> DataBusVault:
    global _vault
    if _vault is None:
        _vault = _make_vault()
        await _vault.load()
    return _vault


def get_vault_sync() -> DataBusVault:
    """Synchronous access - vault must already be loaded."""
    global _vault
    if _vault is None:
        _vault = _make_vault()
    return _vault


def resolve_key(key_name: str) -> str:
    """Module-level key resolver: vault pool first, env as documented fallback.

    Unlike ``get_vault_sync()`` this does NOT create the singleton - it only
    consults the already-loaded vault (if any) so the deferred async ``load()``
    in ``get_vault()`` is never skipped by an import-time access.
    """
    v = _vault
    if v is not None and v._loaded:
        return v.resolve_key(key_name)
    return os.getenv(key_name, "")
