"""Brand palette for generated fallback icons (deterministic per token)."""

from __future__ import annotations

import hashlib

#: Brand-leaning palette; index chosen by a stable hash of chain+token.
BRAND_PALETTE: tuple[tuple[int, int, int], ...] = (
    (124, 58, 237),  # violet
    (14, 165, 233),  # sky
    (16, 185, 129),  # emerald
    (245, 158, 11),  # amber
    (236, 72, 153),  # pink
)


def brand_rgb(chain: str, token: str) -> tuple[int, int, int]:
    """Deterministically pick a brand colour for a token."""
    digest = hashlib.sha256(f"{str(chain).lower()}:{token}".encode()).digest()
    return BRAND_PALETTE[digest[0] % len(BRAND_PALETTE)]
