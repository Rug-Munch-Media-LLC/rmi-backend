"""Normalization: raw Telegram channel messages → ``NewsArticle``.

Pure helpers only — no network, no DB, no Telegram dependency. Regexes for
EVM / Solana addresses mirror the canonical patterns in ``app.rag.dedup`` so
entity extraction stays consistent with the RAG entity-overlap dedup level.
"""

from __future__ import annotations

import re
from typing import Final

from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.rag.dedup import content_hash

# ── Tunable thresholds ─────────────────────────────────────────────────────

_MIN_NEWS_CHARS: Final = 60  # shorter (and entity-free) → promo/telegraph noise
_FORWARD_ONLY_MAX_CHARS: Final = 120  # short forwarded posts are re-share noise
_TITLE_MAX_CHARS: Final = 120

# ── Bot junk / promo line patterns (stripped before classification) ────────

_JUNK_LINE_RES: Final = (
    re.compile(
        r"(?i)^\s*(join|subscribe|follow|add|share)\s+(our|the|this|my)?\s*(channel|group|telegram|chat|page)\b"
    ),
    re.compile(r"(?i)^\s*(join|follow|subscribe|add)\s+(us|now|me)\b"),
    re.compile(r"(?i)^\s*(buy|sell|pump|dump|moon|gem|signal|alpha|call)\b.{0,40}\b(now|soon|fast|🚀|🔥|💎|🤑)\b"),
    re.compile(r"(?i)^\s*(dm|message)\s+(me|us|for)\b"),
    re.compile(r"(?i)^\s*(watch|read|see|listen)\s+(the\s+)?(full|more|video|thread)\b"),
    re.compile(r"(?i)^\s*(click|tap)\s+(here|the link)\b"),
    re.compile(r"(?i)^\s*powered by rugmunch\b"),
    re.compile(r"(?i)^\s*(🚀|🔥|💎|💰|🤑|🎁|✅)\s*$"),
    re.compile(r"^\s*t\.me/\S+\s*$"),  # bare join-link footer lines
    re.compile(r"^\s*https?://\S+\s*$"),  # bare URL lines (mirrors rag.dedup)
)

# ── Entity regexes (same shapes as app.rag.dedup) ──────────────────────────

_EVM_ADDRESS_RE: Final = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOLANA_ADDRESS_RE: Final = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_RE: Final = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,11})\b")


def extract_entities(text: str) -> list[str]:
    """Extract ordered, de-duplicated crypto entities from *text*.

    Recognizes ``$SYMBOL`` mentions (normalized to uppercase), full EVM
    ``0x`` 40-hex addresses, and Solana base58 addresses. Pure and
    dependency-free — no network, no KB lookups.
    """
    if not text:
        return []
    seen: set[str] = set()
    entities: list[str] = []
    for match in _SYMBOL_RE.finditer(text):
        entity = f"${match.group(1).upper()}"
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    for match in _EVM_ADDRESS_RE.finditer(text):
        entity = match.group(0)
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    for match in _SOLANA_ADDRESS_RE.finditer(text):
        entity = match.group(0)
        if entity not in seen:
            seen.add(entity)
            entities.append(entity)
    return entities


def strip_junk_lines(text: str) -> str:
    """Remove bot-junk / promo / bare-link lines, preserving real content."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in _JUNK_LINE_RES):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _is_noise(text: str, *, forwarded: bool) -> bool:
    """True when *text* is promo/telegraph noise rather than a news item."""
    if not text or not text.strip():
        return True
    if re.fullmatch(r"\s*(?:t\.me/|https?://)\S+\s*", text):
        return True
    if len(text) < _MIN_NEWS_CHARS and not extract_entities(text):
        return True
    return forwarded and len(text) < _FORWARD_ONLY_MAX_CHARS


def channel_message_to_article(msg: ChannelMessage, category: str) -> NewsArticle | None:
    """Convert a raw channel message into a ``NewsArticle``.

    Strips bot junk, skips forward-only noise and non-news (empty, link-only,
    short entity-free promo) messages. Returns ``None`` for anything that is
    not a usable news item.
    """
    if msg is None or not msg.text or not msg.text.strip():
        return None

    text = strip_junk_lines(msg.text).strip()
    if _is_noise(text, forwarded=msg.forwarded_from is not None):
        return None

    lines = text.splitlines()
    title = re.sub(r"\s+", " ", lines[0]).strip()[:_TITLE_MAX_CHARS]
    body = text
    entities = extract_entities(text)

    return NewsArticle(
        title=title,
        body=body,
        source_channel=msg.channel or msg.channel_username,
        source_url=f"https://t.me/{msg.channel_username}/{msg.message_id}",
        category=category,
        posted_at=msg.posted_at,
        content_hash=content_hash(body),
        entities=entities,
    )
