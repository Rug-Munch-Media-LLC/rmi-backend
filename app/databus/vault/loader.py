"""Vault key loader - loads keys from GPG pass store."""

import asyncio
import os

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
                proc = await asyncio.create_subprocess_exec(
                    "python3",
                    VAULT_SCRIPT,
                    "get",
                    vault_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    val = stdout.decode().strip()
                    if val and val not in ("", "None", "***"):
                        return val
            except Exception as e:
                logger.debug("vault unavailable, falling back to env: %s", e)

        # Fallback to env var
        val = os.getenv(env_name, "").strip()
        if val and val not in ("", "None", "***", "your_key_here"):
            return val

        return None
