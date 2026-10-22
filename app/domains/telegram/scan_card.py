"""Telegram-native rendering of a scanner result.

One function — :func:`scan_card` — turns a scanner result dict (payload of
``POST /api/v2/scanner/token/scan``) or a ``ScanResult`` into a fixed-shape
card: an HTML body for ``parse_mode="HTML"`` and a plain-text fallback for the
retry path when Telegram rejects the markup.

Two rules govern this module, both learned from production alert failures:

1. **Never invent a number.** A missing enrichment renders as ``unknown``, not
   ``0`` — a card reading "liquidity $0" on a live token is a false rug call.
2. **The verdict and the numbers are never truncated.** Telegram rejects a
   message over 4,096 characters with HTTP 400 and the whole alert is lost, so
   only the flag list is shortened, and it says how many flags it dropped.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from app.domains.telegram.scan_card_fields import (
    field,
    flags,
    link,
    metrics,
    severity,
    symbol,
    verdict,
)

MAX_CHARS = 4096


@dataclass(frozen=True)
class ScanCard:
    """Rendered card: HTML body, plain-text fallback, and their sizes."""

    html: str
    text: str

    def __len__(self) -> int:
        return len(self.html)


def esc(value: object) -> str:
    """Escape for ``parse_mode="HTML"``. Quotes are left alone — no attributes."""
    return html.escape(str(value), quote=False)


def _render(
    result: dict, address: str, rows: list[tuple[str, str]], dropped: int, html_mode: bool
) -> str:
    """One renderer, two emitters — the plain-text path must mirror the HTML path."""
    escape = esc if html_mode else str
    bold = (lambda s: f"<b>{s}</b>") if html_mode else (lambda s: s)
    chain = str(result.get("chain", "unknown")).upper()
    out = [bold(f"${escape(symbol(result, address))} · {escape(chain)} · {severity(result)}")]
    out.append(f"<code>{escape(address)}</code>" if html_mode else address)
    out.append("")
    out.extend(f"{bold(label + ':')} {escape(value)}" for label, value in verdict(result))
    block = "\n".join(f"{name:<14}{escape(value)[:44]}" for name, value in metrics(result))
    out.extend([f"<pre>{block}</pre>" if html_mode else block, bold("Flags")])
    out.extend(
        (f"• [{escape(status)}] {escape(reason)[:120]}" if html_mode else f"- [{status}] {reason}")
        for reason, status in rows
    )
    if not rows:
        out.append("None recorded.")
    if dropped:
        out.append(f"… {dropped} more flag(s), see full report.")
    label, url = link(result, address)
    out.extend(["", f'<a href="{escape(url)}">{label}</a>' if html_mode else f"{label}: {url}"])
    return "\n".join(out)


def scan_card(token: object, max_chars: int = MAX_CHARS) -> ScanCard:
    """Render a scanner result as a Telegram HTML card plus a plain-text fallback.

    ``token`` is the scan payload dict (or anything with the same keys, e.g.
    ``ScanResult.__dict__``). Only the flag list is ever shortened; the verdict
    block and the metric block are always emitted in full.
    """
    result = token if isinstance(token, dict) else getattr(token, "__dict__", {}) or {}
    address = str(
        field(
            result,
            "token_address",
            default=field(result, "token", default=field(result, "address", default="unknown")),
        )
    )
    full = flags(result)
    kept, dropped = full, 0
    body = _render(result, address, kept, dropped, True)
    while len(body) > max_chars and len(kept) > 1:
        kept = kept[: max(1, len(kept) // 2)]
        dropped = len(full) - len(kept)
        body = _render(result, address, kept, dropped, True)
    if len(body) > max_chars:
        kept, dropped = [], len(full)
        body = _render(result, address, kept, dropped, True)
    return ScanCard(html=body, text=_render(result, address, kept, dropped, False))
