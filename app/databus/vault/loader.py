"""Vault key loader - loads keys from GPG pass store."""

import os
import subprocess
from typing import Any

from app.core.logging import get_logger

logger = get_logger("databus.vault.loader")

VAULT_SCRIPT = "/root/.secrets/vault.py"


class VaultKeyLoader:
    """Loads keys from GPG pass store or environment variables."""

    def __init__(self) -> None:
        self._vault_available = False

    def check_vault_available(self) -> bool:
        """Check if vault script is available."""
        self._vault_available = os.path.exists(VAULT_SCRIPT)
        return self._vault_available

    async def get_key(self, env_name: str, vault_path: str) -> str | None:
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