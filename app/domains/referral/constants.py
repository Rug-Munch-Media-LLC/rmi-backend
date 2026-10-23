"""Referral attribution constants — one source of truth for surfaces, tiers, partners."""

from __future__ import annotations

SURFACES: frozenset[str] = frozenset({"scan_quick", "scan_deep", "bot", "content", "frontend"})
TIERS: frozenset[str] = frozenset({"anonymous", "free", "pro", "enterprise"})
DEFAULT_TIER: str = "anonymous"

# Allowlist: partner key -> https base URL with an optional ``{code}`` placeholder.
# A partner NOT in this map can never be redirected to. That is the whole
# open-redirect defence: we never take a URL from the caller, only a key.
PARTNER_TARGETS: dict[str, str] = {
    "jupiter": "https://jup.ag/?ref={code}",
    "hyperliquid": "https://app.hyperliquid.xyz/?ref={code}",
    "banana": "https://bananagun.io/?ref={code}",
    "gmgn": "https://gmgn.ai/?ref={code}",
    "axiom": "https://axiom.trade/?ref={code}",
    "odin": "https://odinbot.io/?ref={code}",
    "padre": "https://trade.padre.gg/?ref={code}",
}

# Attribution codes are opaque referral slugs, never URLs or free text.
CODE_PATTERN: str = r"^[A-Za-z0-9_-]{1,64}$"
CLICK_RETENTION_DAYS: int = 400
