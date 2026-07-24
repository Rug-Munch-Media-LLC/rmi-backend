"""
Universal API key loader with cascading fallbacks.
Resolves Docker env-loading issues by checking multiple sources.
Pattern: env var → /tmp/{name}_key.txt → /root/.secrets/sentinel_enrichment_keys.env → None
"""

from __future__ import annotations
import os

def load_key(name: str) -> str:
    """Load an API key with multiple fallback sources.

    Checks in order:
    1. Environment variable (os.getenv)
    2. File-based fallback at /tmp/{name}_key.txt (Docker workaround)
    3. Secrets file at /root/.secrets/sentinel_enrichment_keys.env

    Returns empty string if not found.
    """
    # 1. Environment variable
    key = os.getenv(name, "")
    if key:
        return key

    # 2. File-based fallback (works around Docker env sanitization)
    file_path = f"/tmp/{name.lower().replace('_api_key', '')}_key.txt"
    try:
        if os.path.exists(file_path):
            with open(file_path) as f:
                key = f.read().strip()
                if key:
                    return key
    except (ConnectionError, OSError, TimeoutError):
        pass

    # 3. Central secrets file
    secrets_path = "/root/.secrets/sentinel_enrichment_keys.env"
    try:
        if os.path.exists(secrets_path):
            with open(secrets_path) as f:
                for line in f:
                    if line.startswith(f"{name}="):
                        key = line.split("=", 1)[1].split("#")[0].strip()
                        if key:
                            return key
    except (ConnectionError, OSError, TimeoutError):
        pass

    return ""

# Enrichment key service name → env var mapping
SERVICE_KEYS = {
    "nansen": "NANSEN_API_KEY",
    "chainaware": "CHAIN_AWARE_API_KEY",
    "dune": "DUNE_API_KEY",
    "santiment": "SANTIMENT_API_KEY",
    "thegraph": "THEGRAPH_API_KEY",
    "defi": "DEFI_API_KEY",
    "webacy": "WEBACY_API_KEY",
    "lunarcrush": "LUNARCRUSH_API_KEY",
    "arkham": "ARKHAM_API_KEY",
    "blowfish": "BLOWFISH_API_KEY",
}
