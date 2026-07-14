"""Internal service-to-service authentication key.
Read from RMI_INTERNAL_KEY env var. No default — must be set in production.
"""

import os


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


# Module-level singleton (evaluated at import time)
INTERNAL_API_KEY = get_internal_key()
