"""Per-channel HTML content formatters for the bot-owned Telegram channels.

One formatter per channel:
* ``format_news_post``   — news channel (curated headlines, source-cited).
* ``format_breaking_post`` — news channel (solo 🚨 BREAKING story + official link).
* ``format_alerts_post`` — alerts channel (scanner risk digest, /scan links).
* ``format_alpha_post``  — alpha channel (whale moves + smart money + launches).

All backend/user-supplied text is HTML-escaped (``quote=False`` so Telegram
``<a>``/``<code>`` tags we build ourselves stay intact). Messages stay under
Telegram's 4096-char soft limit and always carry the bot footer.
"""

from __future__ import annotations

import html
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from app.domains.telegram.rugmunchbot.formatters import footer_links, sep, short_addr

_MAX_BODY_CHARS = 4090
#: Length of the summary shown under a solo breaking post.
_BREAKING_SUMMARY_CHARS = 600


class NewsPost(str):
    """A formatted post: a plain ``str`` plus an optional attached image URL.

    ``image_url`` is ``""`` when the post has no usable image. Because the
    value IS a ``str``, existing callers (``_safe_send``,
    ``post_news_update``, tests) keep working unchanged; photo-aware callers
    read ``.image_url`` and route through ``channels._media.send_news_photo``.
    """

    image_url: str = ""


def _pick_image_url(value: object) -> str:
    """Return *value* when it is a usable http(s) image URL, else ``""``."""
    if isinstance(value, str) and value.strip():
        url = value.strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _esc(value: object) -> str:
    """HTML-escape text for Telegram parse_mode=HTML (quotes left intact)."""
    return html.escape(str(value), quote=False)


def _clip(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _risk_emoji(risk_level: str) -> str:
    return {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢", "safe": "✅"}.get(
        str(risk_level).lower(), "⚪"
    )


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%b %d, %H:%M UTC")


# ── News channel ──────────────────────────────────────────────────────────


def format_news_post(items: list[dict[str, Any]]) -> NewsPost:
    """News digest: numbered, source-cited headlines with links.

    Returns a ``NewsPost`` (a ``str``) whose ``.image_url`` is the first
    story's usable http(s) image — the digest's photo when the scheduler
    routes it through ``send_news_photo``. Items are the post-dicts shaped
    by ``channels.telegram_news._digest._article_to_post_dict`` (which
    already carries ``image_url``).
    """
    lines = ["📰 <b>RugMunch News</b>", sep(), ""]
    for i, item in enumerate(items[:6], 1):
        title = _esc(item.get("title") or "Untitled")
        source = _esc(item.get("source") or "unknown")
        url = (item.get("url") or "").strip()
        if url:
            title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
        lines.append(f"{i}. {title}")
        rating_bits = []
        if item.get("sentiment_label"):
            rating_bits.append(f"sentiment {_esc(item['sentiment_label'])}")
        if item.get("factual_rating"):
            rating_bits.append(f"factual {_esc(item['factual_rating'])}")
        suffix = f" — {source}" + (f" ({', '.join(rating_bits)})" if rating_bits else "")
        lines.append(f"   <i>{suffix}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    image_url = ""
    for item in items[:6]:
        candidate = _pick_image_url(item.get("image_url"))
        if candidate:
            image_url = candidate
            break
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = image_url
    return post


def format_breaking_post(item: dict[str, Any]) -> NewsPost:
    """Solo breaking-news post: one high-importance story + official link.

    Used when the top ranked story carries a breaking marker (🚨/BREAKING/
    URGENT/JUST IN/FLASH). Links to the official source URL when one exists
    (falls back to the channel post URL), clips the summary, and keeps the
    same footer as the digest. The story's image (og:image / inline image,
    the post-dict's ``image_url``) is carried on the returned ``NewsPost``
    so the scheduler can attach it as a photo.
    """
    title = _esc(item.get("title") or "Untitled")
    url = (item.get("url") or "").strip()
    summary = _esc((item.get("summary") or item.get("description") or "").strip())
    if summary:
        summary = _clip(summary, _BREAKING_SUMMARY_CHARS)
    source = _esc(item.get("source") or "unknown")
    headline = title if not url else f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    lines = ["🚨 <b>BREAKING</b>", sep(), ""]
    lines.append(headline)
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")
    lines.append(f"🔗 <i>Full story: {source}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url"))
    return post


# ── Alerts channel ────────────────────────────────────────────────────────


def format_alerts_post(alerts: list[dict[str, Any]]) -> str:
    """Scanner risk digest: high-risk tokens with honeypot/rug flags + /scan."""
    lines = ["🚨 <b>Risk Alerts</b>", sep(), ""]
    if not alerts:
        lines.append("No new high-risk tokens detected in the last window.")
        lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
        return _clip("\n".join(lines))
    for item in alerts[:6]:
        symbol = _esc(item.get("symbol") or "???")
        name = _esc(item.get("name") or symbol)
        addr = str(item.get("address") or "")
        chain = _esc(item.get("chain") or "")
        risk = _esc(item.get("risk_level") or "unknown")
        score = item.get("risk_score")
        score_txt = f"{_risk_emoji(risk)} <b>{name}</b> ({symbol})"
        if score is not None:
            with suppress(TypeError, ValueError):
                score_txt += f" — risk {float(score):.0f}/100"
        lines.append(score_txt)
        if chain:
            lines.append(f"   ⛓️ {chain}")
        if addr:
            lines.append(f"   🔍 /scan <code>{_esc(addr)}</code>")
        flags = [f for f in item.get("risk_flags") or [] if f]
        if flags:
            lines.append("   ⚠️ " + " · ".join(_esc(f) for f in flags[:5]))
        lines.append("")
    lines.append("<i>Auto-flagged by the RugMunch scanner — always /scan before buying.</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    return _clip("\n".join(lines))


# ── Alpha channel ─────────────────────────────────────────────────────────


def _fmt_usd(value: Any) -> str:
    with suppress(TypeError, ValueError):
        return f"${float(value):,.0f}"
    return ""


def _fmt_wallet_short(addr: str) -> str:
    return short_addr(addr) if addr else "?"


def format_alpha_post(
    whale_moves: list[dict[str, Any]],
    smart_money: list[dict[str, Any]],
    launches: list[dict[str, Any]],
) -> str:
    """Alpha digest: whale moves + smart money wallets + new launches.

    Every section only renders when its live source returned data — an empty
    section is omitted, never invented. Source labels are honest about where
    each item came from.
    """
    lines = ["🐋 <b>Alpha Feed</b>", sep(), ""]

    if whale_moves:
        lines.append("⚡ <b>Whale Moves</b>")
        for move in whale_moves[:4]:
            title = _esc(move.get("title") or "Whale move")
            chain = _esc(move.get("chain") or "solana")
            addr = str(move.get("token_address") or "")
            lines.append(f"• {title} ({chain})")
            if addr:
                lines.append(f"  🔍 /scan <code>{_esc(addr)}</code>")
        lines.append("")

    if smart_money:
        lines.append("🧠 <b>Smart Money Wallets</b>")
        for w in smart_money[:5]:
            label = _esc(w.get("label") or _fmt_wallet_short(str(w.get("address") or "")))
            addr = str(w.get("address") or "")
            pnl = _fmt_usd(w.get("pnl_usd") or w.get("realized_pnl"))
            bits = [f"<code>{_esc(addr)}</code>"]
            if pnl:
                bits.append(f"P&L {pnl}")
            lines.append(f"• {label} — " + " · ".join(bits))
        lines.append("")

    if launches:
        lines.append("🆕 <b>New Launches</b>")
        for launch in launches[:5]:
            symbol = _esc(launch.get("symbol") or "???")
            name = _esc(launch.get("name") or symbol)
            chain = _esc(launch.get("chain") or "")
            risk = _esc(launch.get("risk_level") or "unknown")
            addr = str(launch.get("address") or "")
            lines.append(f"• {_risk_emoji(risk)} <b>{name}</b> ({symbol}){f' — {chain}' if chain else ''}")
            if addr:
                lines.append(f"  🔍 /scan <code>{_esc(addr)}</code>")
        lines.append("")

    if not whale_moves and not smart_money and not launches:
        lines.append("No fresh alpha signals in this window — checking again soon.")
        lines.append("")

    lines.append(f"🕐 {_now_stamp()}")
    lines.append(footer_links())
    return _clip("\n".join(lines))


__all__ = [
    "NewsPost",
    "format_alerts_post",
    "format_alpha_post",
    "format_breaking_post",
    "format_news_post",
]
