"""Centralized secrets access via gopass.

All API keys and secrets MUST go through this module.
Never use os.getenv() directly for secrets.

Priority: gopass > env var > crash

Usage:
    from app.core.secrets import get_secret

    key = get_secret("SUPABASE_SERVICE_KEY", "rmi/prod/supabase_service_key")
"""

from __future__ import annotations
import os
import subprocess

from app.core.logging import get_logger

logger = get_logger(__name__)

def _from_gopass(path: str) -> str | None:
    """Try to read a secret from gopass. Returns None if gopass not available."""
    try:
        # nosec: path is hardcoded by callers (e.g., "rmi/prod/supabase_service_key"),
        # not from user input. gopass is always PATH-resolvable in production.
        result = subprocess.run(  # noqa: S603
            ["gopass", "show", "-o", path],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except FileNotFoundError:
        # gopass not installed
        pass
    except subprocess.TimeoutExpired:
        logger.warning("gopass timed out reading %s", path)
    except Exception as exc:
        logger.warning("gopass error reading %s: %s", path, exc)
    return None

def get_secret(env_var: str, gopass_path: str | None = None) -> str:
    """Get a secret from gopass first, fall back to env var.

    Priority: gopass > env var > crash

    Args:
        env_var: Environment variable name (fallback).
        gopass_path: gopass path (e.g., 'rmi/prod/supabase_service_key').

    Returns:
        The secret value as a string.

    Raises:
        RuntimeError: If the secret cannot be found from any source.
    """
    # Try gopass first
    if gopass_path:
        secret = _from_gopass(gopass_path)
        if secret:
            return secret

    # Fall back to env var
    secret = os.getenv(env_var)
    if secret is None:
        sources = f"gopass:{gopass_path}" if gopass_path else env_var
        raise RuntimeError(f"Secret not found. Tried: {sources}")

    # Warn if only env var was available (not gopass)
    if gopass_path:
        logger.warning(
            "Secret %s loaded from env var, not gopass. Migrate to gopass:%s",
            env_var,
            gopass_path,
        )

    return secret

def get_secret_or_none(env_var: str, gopass_path: str | None = None) -> str | None:
    """Get a secret with graceful fallback — returns None instead of raising.

    Args:
        env_var: Environment variable name (fallback).
        gopass_path: Optional gopass path.

    Returns:
        The secret value, or None if not found.
    """
    try:
        return get_secret(env_var, gopass_path)
    except RuntimeError:
        return None
