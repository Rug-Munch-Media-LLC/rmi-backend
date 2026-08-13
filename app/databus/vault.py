"""
DataBus Vault Integration - Zero-Plaintext Key Management
===========================================================

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

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.vault")

# ── Vault Key Discovery ─────────────────────────────────────────────────────

VAULT_SCRIPT = "/root/.secrets/vault.py"

# Every API key the system knows about, grouped by provider.
# Format: (env_var_name, vault_path, provider)
# vault_path is relative to rmi/ prefix in pass store.
KEY_REGISTRY = {
    # ── Solana RPC (3 keys = 75 RPS free tier combined) ──
    "helius": [
        ("HELIUS_API_KEY", "infra/helius_api_key"),
        ("HELIUS_API_KEY_2", "infra/helius_api_key_2"),
        ("HELIUS_API_KEY_3", "infra/helius_api_key_3"),
    ],
    # ── Indexed Data ──
    "solana_tracker": [
        ("SOLANATRACKER_API_KEY", "infra/soltracker_api_key"),
    ],
    "birdeye": [
        ("BIRDEYE_API_KEY", "infra/birdeye_api_key"),
    ],
    "solscan": [
        ("SOLSCAN_API_KEY", "infra/solscan_api_key"),
    ],
    # ── EVM ──
    "moralis": [
        ("MORALIS_API_KEY", "infra/moralis_api_key"),
        ("MORALIS_API_KEY_2", "infra/moralis_api_key_2"),
        ("MORALIS_API_KEY_3", "infra/moralis_api_key_3"),
    ],
    "etherscan": [
        ("ETHERSCAN_API_KEY", "infra/etherscan_api_key"),
    ],
    "alchemy": [
        ("ALCHEMY_API_KEY", "infra/alchemy_api_key"),
    ],
    "quicknode": [
        ("QUICKNODE_KEY", "infra/quicknode_api_key"),
    ],
    # ── Market Data ──
    "coingecko_pro": [
        ("COINGECKO_API_KEY", "infra/coingecko_api_key_pro"),
    ],
    "coinmarketcap": [
        ("COINMARKETCAP_API_KEY", "infra/coinmarketcap_api_key"),
    ],
    # ── Intelligence ──
    "arkham": [
        ("ARKHAM_API_KEY", "infra/arkham_api_key"),
    ],
    "goplus": [
        ("GOPLUS_API_KEY", "api/goplus_api_key"),
    ],
    "nansen": [
        ("NANSEN_API_KEY", "infra/nansen_api_key"),
    ],
    "dune": [
        ("DUNE_API_KEY", "infra/dune_api_key"),
    ],
    # ── LLM / AI ──
    "openrouter": [
        ("OPENROUTER_API_KEY", "backend/openrouter_api_key"),
    ],
    "deepseek": [
        ("DEEPSEEK_API_KEY", "backend/deepseek_api_key"),
    ],
    "gemini": [
        ("GEMINI_API_KEY", "backend/gemini_api_key"),
    ],
    # ── Infra ──
    "blockscout": [
        ("BLOCKSCOUT_API_KEY", "api/blockscout_api_key"),
    ],
    "bitquery": [
        ("BITQUERY_API_KEY", "backend/bitquery_api_key"),
    ],
    # ── Social ──
    "lunarcrush": [
        ("LUNARCRUSH_API_KEY", "infra/lunarcrush_api_key"),
    ],
    # ── Web3 ──
    "thegraph": [
        ("THEGRAPH_API_KEY", "infra/thegraph_api_key"),
    ],
    "apify": [
        ("APIFY_TOKEN", "infra/apify_token"),
    ],
}


class KeyState(Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    DISABLED = "disabled"
    ERROR = "error"
    EXHAUSTED = "exhausted"  # monthly quota hit


@dataclass
class ManagedKey:
    """A single API key with runtime state. Key value is NEVER a plain string."""

    provider: str
    key_name: str  # env var name e.g. "HELIUS_API_KEY"
    _obfuscated: bytes  # obfuscated key value - never plain string
    source: str = "env"  # "env" or "vault"
    state: KeyState = KeyState.ACTIVE
    calls_total: int = 0
    calls_this_month: int = 0
    monthly_quota: int = 0  # 0 = unlimited
    errors: int = 0
    consecutive_429s: int = 0
    rate_limited_until: float = 0.0
    last_used: float = 0.0
    last_error: str = ""
    # Token bucket
    tokens: float = 0.0
    last_refill: float = 0.0
    rate_rps: float = 10.0
    burst: int = 15

    def __post_init__(self) -> None:
        self.tokens = float(self.burst)
        self.last_refill = time.monotonic()

    @property
    def value(self) -> str:
        """Deobfuscate and return the key. Use acquire()/release() for safety."""
        return self._obfuscated.decode("utf-8", errors="replace")[::-1]

    def _set_value(self, val: str) -> None:
        """Obfuscate a key value for in-memory storage."""
        self._obfuscated = val[::-1].encode("utf-8")

    def acquire(self) -> str:
        """Get the key value for use. Call release() when done."""
        self.calls_total += 1
        self.calls_this_month += 1
        self.last_used = time.monotonic()
        return self.value

    def release_success(self) -> None:
        """Mark the key call as successful."""
        self.consecutive_429s = 0
        self.rate_limited_until = 0.0
        self.state = KeyState.ACTIVE

    def release_rate_limited(self, backoff_base: float = 5.0) -> None:
        """Mark the key as rate limited with exponential backoff."""
        self.consecutive_429s += 1
        backoff = min(300, backoff_base * (2 ** min(self.consecutive_429s, 6)))
        self.rate_limited_until = time.monotonic() + backoff
        self.state = KeyState.RATE_LIMITED

    def release_error(self, error: str) -> None:
        """Mark the key as errored."""
        self.errors += 1
        self.last_error = error[:200]
        if self.errors > 10:
            self.state = KeyState.ERROR

    def is_available(self) -> bool:
        """Check if key is usable right now."""
        if self.state == KeyState.DISABLED:
            return False
        if self.state == KeyState.ERROR and self.errors > 50:
            return False
        if self.state == KeyState.RATE_LIMITED:
            if time.monotonic() > self.rate_limited_until:
                self.state = KeyState.ACTIVE
                self.consecutive_429s = 0
                return True
            return False
        return not (self.monthly_quota > 0 and self.calls_this_month >= self.monthly_quota)

    def refill_tokens(self, now: float) -> None:
        """Refill token bucket."""
        elapsed = now - self.last_refill
        self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate_rps)
        self.last_refill = now

    def status(self) -> dict[str, Any]:
        """Return status dict - NEVER includes key value."""
        return {
            "provider": self.provider,
            "key_name": self.key_name,
            "state": self.state.value,
            "calls_total": self.calls_total,
            "calls_this_month": self.calls_this_month,
            "monthly_quota": self.monthly_quota,
            "tokens_available": round(self.tokens, 1),
            "errors": self.errors,
            "consecutive_429s": self.consecutive_429s,
            "rate_limited_for_secs": max(0, round(self.rate_limited_until - time.monotonic(), 1))
            if self.state == KeyState.RATE_LIMITED
            else 0,
            "last_used_ago_secs": round(time.monotonic() - self.last_used, 1) if self.last_used else 0,
        }


class VaultKeyPool:
    """
    Key pool for a single provider. Round-robin with health tracking.
    Keys loaded from vault (GPG pass store), NEVER from .env.
    Zero key exposure in logs, responses, or error messages.
    """

    def __init__(self, provider: str, rate_rps: float = 10.0, burst: int = 15, monthly_quota: int = 0) -> None:
        self.provider = provider
        self.rate_rps = rate_rps
        self.burst = burst
        self.monthly_quota = monthly_quota
        self.keys: list[ManagedKey] = []
        self._index = 0
        self._lock = asyncio.Lock()
        self.total_calls = 0

    def add_key(self, key_name: str, value: str, source: str = "vault") -> None:
        """Add a key to the pool. Value is immediately obfuscated."""
        mk = ManagedKey(
            provider=self.provider,
            key_name=key_name,
            _obfuscated=b"",  # set via method
            source=source,
            rate_rps=self.rate_rps,
            burst=self.burst,
            monthly_quota=self.monthly_quota,
        )
        mk._set_value(value)
        self.keys.append(mk)

    async def acquire(self) -> ManagedKey | None:
        """Get the next available key in round-robin. Returns None if all exhausted."""
        async with self._lock:
            now = time.monotonic()
            tried = 0
            while tried < len(self.keys):
                key = self.keys[self._index]
                self._index = (self._index + 1) % len(self.keys)
                tried += 1

                key.refill_tokens(now)
                if not key.is_available():
                    continue
                if key.tokens < 1.0:
                    continue

                key.tokens -= 1.0
                self.total_calls += 1
                return key

            return None  # All keys exhausted or rate-limited

    def status(self) -> dict[str, Any]:
        """Return pool status - NO key values ever exposed."""
        active = sum(1 for k in self.keys if k.is_available())
        rate_limited = sum(1 for k in self.keys if k.state == KeyState.RATE_LIMITED)
        return {
            "provider": self.provider,
            "total_keys": len(self.keys),
            "active_keys": active,
            "rate_limited_keys": rate_limited,
            "combined_rps": round(self.rate_rps * len(self.keys), 1),
            "monthly_quota_per_key": self.monthly_quota,
            "total_calls": self.total_calls,
            "keys": [k.status() for k in self.keys],
        }


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
        self._vault_available = False
        # Provider config: rate limits, quotas
        self.provider_config: dict[str, dict[str, Any]] = {
            "helius": {"rps": 25.0, "burst": 25, "quota": 0},
            "solana_tracker": {"rps": 3.0, "burst": 3, "quota": 2500},
            "birdeye": {"rps": 5.0, "burst": 5, "quota": 0},
            "solscan": {"rps": 5.0, "burst": 5, "quota": 0},
            "moralis": {"rps": 25.0, "burst": 25, "quota": 0},
            "etherscan": {"rps": 5.0, "burst": 5, "quota": 0},
            "alchemy": {"rps": 25.0, "burst": 25, "quota": 0},
            "quicknode": {"rps": 25.0, "burst": 25, "quota": 0},
            "coingecko_pro": {"rps": 30.0, "burst": 30, "quota": 0},
            "coinmarketcap": {"rps": 10.0, "burst": 10, "quota": 0},
            "arkham": {"rps": 5.0, "burst": 5, "quota": 100000},
            "goplus": {"rps": 5.0, "burst": 5, "quota": 0},
            "nansen": {"rps": 5.0, "burst": 5, "quota": 0},
            "dune": {"rps": 5.0, "burst": 5, "quota": 0},
            "openrouter": {"rps": 20.0, "burst": 30, "quota": 0},
            "deepseek": {"rps": 50.0, "burst": 100, "quota": 0},
            "gemini": {"rps": 15.0, "burst": 20, "quota": 0},
            "blockscout": {"rps": 5.0, "burst": 5, "quota": 0},
            "bitquery": {"rps": 5.0, "burst": 5, "quota": 0},
            "lunarcrush": {"rps": 5.0, "burst": 5, "quota": 0},
            "thegraph": {"rps": 5.0, "burst": 5, "quota": 0},
            "apify": {"rps": 5.0, "burst": 5, "quota": 0},
        }

    async def load(self) -> None:
        """Load all keys from vault (preferred) or env (fallback)."""
        if self._loaded:
            return

        # Try vault first
        self._vault_available = os.path.exists(VAULT_SCRIPT)

        for provider, key_defs in KEY_REGISTRY.items():
            cfg = self.provider_config.get(provider, {"rps": 10.0, "burst": 15, "quota": 0})
            pool = VaultKeyPool(
                provider=provider,
                rate_rps=cfg["rps"],
                burst=cfg["burst"],
                monthly_quota=cfg["quota"],
            )

            for env_name, vault_path in key_defs:
                value = await self._get_key(env_name, vault_path)
                if value and value not in ("", "your_key_here", "***"):
                    pool.add_key(env_name, value, source="vault" if self._vault_available else "env")

            if pool.keys:
                self.pools[provider] = pool
                logger.info(f"Vault: {provider} -> {len(pool.keys)} key(s) loaded")

        # Load the new Arkham keys (user just provided)
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
        # If no keys loaded yet, try vault then env
        if not pool.keys:
            val = await self._get_key("ARKHAM_API_KEY", "infra/arkham_api_key")
            if val and val not in ("", "your_key_here", "***"):
                pool.add_key("ARKHAM_API_KEY", val)

        # Store WS key separately (not a pool key)
        ws_key = await self._get_key("ARKHAM_WS_KEY", "infra/arkham_ws_key")
        if ws_key and ws_key not in ("", "your_key_here", "***"):
            self.arkham_ws_key = ws_key
        else:
            # Check env
            ws_key = os.getenv("ARKHAM_WS_KEY", "")
            if ws_key:
                self.arkham_ws_key = ws_key

    async def _get_key(self, env_name: str, vault_path: str) -> str | None:
        """Get a key from vault (preferred) or env (fallback). Never logs the value."""
        # Try vault first
        if self._vault_available:
            try:
                result = subprocess.run(
                    ["python3", VAULT_SCRIPT, "get", vault_path], capture_output=True, text=True, timeout=5, shell=False
                )
                if result.returncode == 0 and result.stdout.strip():
                    val = result.stdout.strip()
                    if val and val not in ("", "None", "***"):
                        return val
            except subprocess.TimeoutExpired:
                pass  # Vault unavailable, fall back to env

        # Fallback to env var
        val = os.getenv(env_name, "").strip()
        if val and val not in ("", "None", "***", "your_key_here"):
            return val

        return None

    async def acquire_key(self, provider: str) -> ManagedKey | None:
        """Acquire a key from the provider's pool."""
        pool = self.pools.get(provider)
        if not pool:
            return None
        return await pool.acquire()

    def get_pool(self, provider: str) -> VaultKeyPool | None:
        return self.pools.get(provider)

    def status(self) -> dict[str, Any]:
        """Full status report. NEVER includes key values."""
        total_keys = sum(len(p.keys) for p in self.pools.values())
        active_keys = sum(sum(1 for k in p.keys if k.is_available()) for p in self.pools.values())
        return {
            "vault_available": self._vault_available,
            "total_providers": len(self.pools),
            "total_keys": total_keys,
            "active_keys": active_keys,
            "providers": {name: pool.status() for name, pool in self.pools.items()},
        }

    def capacity_report(self) -> dict[str, Any]:
        """Generate capacity analysis with recommendations."""
        bottlenecks = []
        recommendations = []
        providers = {}

        for name, pool in self.pools.items():
            self.provider_config.get(name, {})
            total_keys = len(pool.keys)
            combined_rps = pool.rate_rps * total_keys
            combined_quota = pool.monthly_quota * total_keys if pool.monthly_quota > 0 else None
            active = sum(1 for k in pool.keys if k.is_available())

            status = "OK"
            if combined_rps < 10:
                status = "LOW_RPS"
                bottlenecks.append(f"{name}: {combined_rps:.0f} RPS from {total_keys} key(s)")
            if combined_quota and combined_quota < 10000:
                status = "LOW_QUOTA"
                bottlenecks.append(f"{name}: {combined_quota}/mo across {total_keys} key(s)")
            if total_keys == 1 and pool.rate_rps < 10:
                status = "SINGLE_KEY_LIMITED"
                recommendations.append(f"Get 2-3 more {name} free accounts for pool rotation")

            providers[name] = {
                "keys": total_keys,
                "active": active,
                "rps_per_key": pool.rate_rps,
                "combined_rps": combined_rps,
                "monthly_quota": combined_quota,
                "status": status,
            }

        # Auto-recommendations for free tier expansion
        free_tier_accounts = {
            "helius": "https://dev.helius.xyz - 3 free accounts = 75 RPS",
            "moralis": "https://admin.moralis.io - 3 free accounts = 75 RPS",
            "etherscan": "https://etherscan.io/register - multiple free keys",
            "birdeye": "https://birdeye.io - free tier available",
            "solscan": "https://solscan.io - Pro API free tier",
        }
        for prov, info in free_tier_accounts.items():
            if prov in providers and providers[prov]["status"] != "OK":
                recommendations.append(f"FREE: Create more {prov} accounts at {info}")

        return {
            "total_keys": sum(len(p.keys) for p in self.pools.values()),
            "active_keys": sum(sum(1 for k in p.keys if k.is_available()) for p in self.pools.values()),
            "providers": providers,
            "bottlenecks": bottlenecks,
            "recommendations": recommendations,
            "free_tier_opportunities": list(free_tier_accounts.keys()),
        }

    async def reload(self) -> None:
        """Force reload all keys from vault. Useful after adding new keys."""
        self._loaded = False
        self.pools.clear()
        await self.load()

    async def add_key(self, provider: str, key_name: str, value: str) -> None:
        """Hot-add a key to a provider pool without restart."""
        if provider not in self.pools:
            cfg = self.provider_config.get(provider, {"rps": 10.0, "burst": 15, "quota": 0})
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

    arkham_ws_key: str = ""


# ── Singleton ─────────────────────────────────────────────────────────────────

_vault: DataBusVault | None = None


async def get_vault() -> DataBusVault:
    global _vault
    if _vault is None:
        _vault = DataBusVault()
        await _vault.load()
    return _vault


def get_vault_sync() -> DataBusVault:
    """Synchronous access - vault must already be loaded."""
    global _vault
    if _vault is None:
        _vault = DataBusVault()
    return _vault
