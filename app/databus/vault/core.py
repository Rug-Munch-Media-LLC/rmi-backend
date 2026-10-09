"""
DataBus Vault Integration - Zero-Plaintext Key Management
==========================================================

Never reads API keys from .env. Never logs them. Never exposes them in responses.
Pulls from the GPG pass store at runtime, keeps them encrypted in memory,
auto-rotates on 429, and can refresh from vault without restart.

Architecture:
  1. On startup: vault.py decrypts all keys from pass store into locked memory
  2. Keys stored as obfuscated bytes - never Python strings that could be repr()'d
  3. When a provider needs a key: acquire() returns it for the minimum time needed
  4. Key rotation: on 429/401, mark key disabled, rotate to next in pool
  5. Auto-refresh: vault.py can be called to add new keys without restart
  6. Admin endpoints NEVER expose key values - only status (active/disabled/rate-limited)
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.databus.vault.config import PROVIDER_CONFIG
from app.databus.vault.loader import VaultKeyLoader
from app.databus.vault.models import KeyState, ManagedKey
from app.databus.vault.pool import VaultKeyPool
from app.databus.vault.registry import KEY_REGISTRY
from app.databus.vault.singleton import resolve_key

logger = get_logger("databus.vault")


class DataBusVault:
    """
    Central vault for all API keys. Loads from GPG pass store,
    keeps keys obfuscated in memory, never exposes plaintext.

    FALLBACK: If vault.py is unavailable, falls back to env vars
    (for Docker container runtime). But vault is always preferred.
    """

    def __init__(self) -> None:
        self.pools: dict[str, VaultKeyPool] = {}
        self._loaded = False
        self._loader = VaultKeyLoader()
        self.arkham_ws_key: str = ""

    async def load(self) -> None:
        """Load all keys from vault (preferred) or env (fallback)."""
        if self._loaded:
            return

        # Check vault availability
        self._loader.check_vault_available()

        for provider, key_defs in KEY_REGISTRY.items():
            cfg = PROVIDER_CONFIG.get(provider, {"rps": 10.0, "burst": 15, "quota": 0})
            pool = VaultKeyPool(
                provider=provider,
                rate_rps=cfg["rps"],
                burst=cfg["burst"],
                monthly_quota=cfg["quota"],
            )

            for env_name, vault_path in key_defs:
                value = await self._loader.get_key(env_name, vault_path)
                if value and value not in ("", "your_key_here", "***"):
                    pool.add_key(env_name, value, source="vault" if self._loader._vault_available else "env")

            if pool.keys:
                self.pools[provider] = pool
                logger.info(f"Vault: {provider} -> {len(pool.keys)} key(s) loaded")

        # Load Arkham extra keys (WS key)
        await self._load_arkham_extra_keys()

        self._loaded = True
        logger.info(
            f"DataBus Vault loaded: {sum(len(p.keys) for p in self.pools.values())} keys across {len(self.pools)} providers"
        )

    async def _load_arkham_extra_keys(self) -> None:
        """Load Arkham API key + WebSocket key."""
        if "arkham" not in self.pools:
            pool = VaultKeyPool("arkham", rate_rps=5.0, burst=5, monthly_quota=100000)
            self.pools["arkham"] = pool

        pool = self.pools["arkham"]
        if not pool.keys:
            val = await self._loader.get_key("ARKHAM_API_KEY", "infra/arkham_api_key")
            if val and val not in ("", "your_key_here", "***"):
                pool.add_key("ARKHAM_API_KEY", val)

        # Store WS key separately (not a pool key)
        ws_key = await self._loader.get_key("ARKHAM_WS_KEY", "infra/arkham_ws_key")
        if ws_key and ws_key not in ("", "your_key_here", "***"):
            self.arkham_ws_key = ws_key
        else:
            ws_key = os.getenv("ARKHAM_WS_KEY", "")
            if ws_key:
                self.arkham_ws_key = ws_key

    async def acquire_key(self, provider: str) -> ManagedKey | None:
        """Acquire a key from the provider's pool."""
        pool = self.pools.get(provider)
        if not pool:
            return None
        return await pool.acquire()

    def resolve_key(self, key_name: str) -> str:
        """Resolve a key value from the loaded vault pool (preferred) or env (fallback).

        Searches every pool for a ManagedKey whose key_name matches. If found,
        returns the decrypted value. Otherwise falls back to the documented env
        var. Never logs the value. Safe to call when vault is not yet loaded —
        it simply returns the env fallback.
        """
        for pool in self.pools.values():
            for key in pool.keys:
                if key.key_name == key_name:
                    return key.value
        return os.getenv(key_name, "")

    def get_pool(self, provider: str) -> VaultKeyPool | None:
        return self.pools.get(provider)

    def status(self) -> dict[str, Any]:
        """Full status report. NEVER includes key values."""
        total_keys = sum(len(p.keys) for p in self.pools.values())
        active_keys = sum(sum(1 for k in p.keys if k.is_available()) for p in self.pools.values())
        return {
            "vault_available": self._loader._vault_available,
            "total_providers": len(self.pools),
            "total_keys": total_keys,
            "active_keys": active_keys,
            "providers": {name: pool.status() for name, pool in self.pools.items()},
        }

    async def reload(self) -> None:
        """Force reload all keys from vault. Useful after adding new keys."""
        self._loaded = False
        self.pools.clear()
        await self.load()

    async def add_key(self, provider: str, key_name: str, value: str) -> None:
        """Hot-add a key to a provider pool without restart."""
        if provider not in self.pools:
            cfg = PROVIDER_CONFIG.get(provider, {"rps": 10.0, "burst": 15, "quota": 0})
            self.pools[provider] = VaultKeyPool(
                provider=provider,
                rate_rps=cfg["rps"],
                burst=cfg["burst"],
                monthly_quota=cfg["quota"],
            )
        self.pools[provider].add_key(key_name, value, source="manual")
        logger.info(f"Vault: Hot-added {key_name} to {provider} pool")

    def reset_monthly_counters(self) -> None:
        """Reset monthly call counters. Call from cron on the 1st of each month."""
        for pool in self.pools.values():
            for key in pool.keys:
                key.calls_this_month = 0
                if key.state == KeyState.EXHAUSTED:
                    key.state = KeyState.ACTIVE

    def capacity_report(self) -> dict[str, Any]:
        """Full capacity and credit report (delegates to capacity.py)."""
        from app.databus.vault.capacity import capacity_report as _capacity_report

        return _capacity_report(self.pools)


# Re-export public types for backward compatibility
__all__ = [
    "KEY_REGISTRY",
    "PROVIDER_CONFIG",
    "DataBusVault",
    "KeyState",
    "ManagedKey",
    "VaultKeyPool",
    "resolve_key",
]
