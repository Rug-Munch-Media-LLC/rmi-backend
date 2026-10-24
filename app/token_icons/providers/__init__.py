"""Icon sources, tried in cost order (cheapest and most reliable first).

GMGN is intentionally absent: its Agent API ``/v1/token/info`` payload carries
no logo/image field (verified against ``app/databus/gmgn_endpoints_market.py``),
and its public web API is Cloudflare-blocked — it cannot resolve icons.
"""

from __future__ import annotations

from app.token_icons.providers import coingecko, dexscreener, trustwallet

#: Ordered providers — first acceptable hit wins.
PROVIDERS = (
    ("dexscreener", dexscreener.fetch_icon),
    ("coingecko", coingecko.fetch_icon),
    ("trustwallet", trustwallet.fetch_icon),
)

__all__ = ["PROVIDERS"]
