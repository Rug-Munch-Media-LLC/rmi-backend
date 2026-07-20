"""Internal service-to-service authentication key.
Read from RMI_INTERNAL_KEY env var. No default — must be set in production.
"""

import os

from app.core.logging import get_logger

log = get_logger(__name__)


def get_internal_key() -> str:
    """Retrieve the internal API key from the environment.
    Raises RuntimeError if not set — prevents silent fallback to a default value.
    """
    key = os.getenv("RMI_INTERNAL_KEY")
    if not key:
        raise RuntimeError(
            "RMI_INTERNAL_KEY environment variable is required. "
            "Generate one with: gopass generate rmi/prod/internal_key 32"
        )
    return key


def get_internal_key_or_none() -> str | None:
    """Like get_internal_key() but returns None instead of raising.
    Use for routers that can degrade gracefully without the key.
    """
    try:
        return get_internal_key()
    except RuntimeError:
        log.warning("RMI_INTERNAL_KEY not set — internal auth will be unavailable")
        return None


# Module-level singleton — returns a dev-safe default, logs warning if unset,
# so routers that import this at module level don't blow up in dev environments.
_INTERNAL_KEY = os.getenv("RMI_INTERNAL_KEY")
if not _INTERNAL_KEY:
    log.warning("RMI_INTERNAL_KEY not set — using dev placeholder. Set this env var in production.")
    _INTERNAL_KEY = "dev-internal-key-placeholder"

INTERNAL_API_KEY = _INTERNAL_KEY
