"""Deterministic branded fallback URL — always resolves to a real PNG."""

from __future__ import annotations

from urllib.parse import quote

#: Served by ``app.token_icons.router`` (never an external host).
FALLBACK_PATH = "/api/v1/tokens/icon-fallback"


def fallback_icon_url(chain: str, token: str) -> str:
    """Stable, self-hosted URL for a token with no usable upstream icon."""
    chain_part = quote(str(chain or "unknown").strip().lower(), safe="")
    token_part = quote(str(token or "unknown").strip(), safe="")
    return f"{FALLBACK_PATH}/{chain_part}/{token_part}.png"
