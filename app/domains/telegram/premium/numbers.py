"""Number formatting for preview legibility.

Rules, in order of priority:

1. A number never becomes a different number. Compaction ($1.24M) is a
   presentation of the same value; absence is ``—``, never ``0``.
2. Four significant characters beat long decimals. ``$3.10K`` reads at a
   glance in a Telegram preview; ``$3,094.72`` does not.
3. Percentages carry one decimal, SOL carries two, prices keep enough
   significant digits to stay non-zero.
"""

from __future__ import annotations

import math

DASH = "—"


def money(value: object, dash: str = DASH) -> str:
    """Compact USD: ``$1.24M``, ``$12.4K``, ``$3.10``, ``$0.000021``."""
    num = _as_float(value)
    if num is None:
        return dash
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(num) >= size:
            return f"${num / size:.2f}{suffix}"
    if abs(num) >= 1:
        return f"${num:,.2f}"
    if num == 0:
        return "$0"
    return f"${num:.6f}".rstrip("0").rstrip(".")


def price(value: object, dash: str = DASH) -> str:
    """Token price with enough decimals to stay non-zero: ``$0.000021``."""
    num = _as_float(value)
    if num is None:
        return dash
    if num == 0:
        return "$0"
    if num >= 0.01:
        return f"${num:,.4f}".rstrip("0").rstrip(".")
    zeros = max(0, -math.floor(math.log10(abs(num))) - 1)
    return f"${num:.{min(zeros + 2, 10)}f}"


def pct(value: object, dash: str = DASH) -> str:
    """``78.4%`` — one decimal, no space, no plus sign."""
    num = _as_float(value)
    return dash if num is None else f"{num:.1f}%"


def sol(value: object, dash: str = DASH) -> str:
    """``12.40`` — two decimals, unit left to the label."""
    num = _as_float(value)
    return dash if num is None else f"{num:.2f}"


def count(value: object, dash: str = DASH) -> str:
    """Thousands separators for holder counts."""
    num = _as_float(value)
    return dash if num is None else f"{int(num):,}"


def short(address: object, dash: str = DASH) -> str:
    """``7xKX...9fQ2`` — the editorial standard for wallet addresses."""
    text = str(address or "")
    return f"{text[:4]}...{text[-4:]}" if len(text) > 12 else (text or dash)


def move(value: object, dash: str = DASH) -> str:
    """Signed percentage delta: ``+38.2%`` / ``-12.0%``."""
    num = _as_float(value)
    return dash if num is None else f"{num:+.1f}%"


def _as_float(value: object) -> float | None:
    """``None``/empty/garbage -> ``None``; keep a real ``0.0`` intact."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
