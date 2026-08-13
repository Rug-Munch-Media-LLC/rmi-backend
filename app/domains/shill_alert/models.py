"""ShillPost — the normalized post shape feeding coordinated-shill detection.

Bridges raw Telegram channel messages (``news_ingest.ChannelMessage``) and
X/Farcaster post dicts into the post shape ``shill_detector`` consumes
(``author`` / ``text`` / ``ts``). The token is intentionally not carried on
the dataclass: ``shill_detector._post_token`` extracts ``$SYMBOL`` /
contract-address tokens from the post text, so the post dict shape only needs
author + text + ts (+ optional metadata).

Pure data layer — no network, no DB, no FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.news_ingest.models import ChannelMessage

_SOURCES = frozenset({"telegram", "x", "farcaster"})


def _parse_iso(text: str) -> float | None:
    """Parse an ISO-8601 timestamp to epoch seconds.

    Accepts an optional trailing ``Z`` (normalized to ``+00:00``), optional
    fractional seconds, and timezone-less values (assumed UTC — matching
    ``_dt_epoch``'s naive-datetime convention).
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _dt_epoch(parsed)


def parse_timestamp(value: Any) -> float | None:
    """Parse *value* into epoch seconds; return ``None`` when unparseable.

    Supports the formats the real X feed and post dicts actually carry:

    - ISO-8601 strings — ``2026-08-13T10:15:00Z``, with or without fractional
      seconds (``...00.123Z``) and with or without an explicit timezone
      (naive values are assumed UTC).
    - RFC 822/2822 date strings — the RSS ``pubDate`` shape
      (``Wed, 13 Aug 2026 10:15:00 GMT``) via ``email.utils.parsedate_to_datetime``.
    - Plain epoch int/float values (and numeric strings); millisecond epochs
      (>1e11) are converted to seconds.

    Never raises. Callers are expected to fail open on ``None`` (e.g.
    ``_epoch`` maps it to ``ts=0.0``).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, bytes):
        value = value.decode(errors="ignore")
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parsed = _parse_iso(text)
    if parsed is not None:
        return parsed
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is not None:
        return _dt_epoch(parsed)
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any, default: float = 0.0) -> float:
    """Coerce an epoch-seconds float or ISO/RFC822 date string to epoch seconds.

    Milliseconds (>1e11) are converted to seconds; unparseable values yield
    ``default`` (never raises). See ``parse_timestamp`` for the accepted
    formats.
    """
    parsed = parse_timestamp(value)
    return default if parsed is None else parsed


def _dt_epoch(value: datetime | None) -> float:
    """Epoch seconds for an aware/naive datetime (naive assumed UTC)."""
    if value is None:
        return 0.0
    dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return dt.timestamp()


def _first_text(*values: Any) -> str:
    """First non-empty stripped string, else ``""``."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass(slots=True)
class ShillPost:
    """A single post/message that may be part of a coordinated shill.

    Attributes:
        author: normalized author identity — Telegram channel username or
            X/Farcaster handle (without leading ``@``).
        text: post/message text.
        ts: epoch-seconds timestamp.
        source: origin platform (telegram | x | farcaster).
        channel: Telegram channel username (telegram only), optional.
        url: canonical post URL when known.
    """

    author: str
    text: str
    ts: float
    source: str = "telegram"
    channel: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if self.source not in _SOURCES:
            raise ValueError(f"invalid source {self.source!r}; expected one of {sorted(_SOURCES)}")

    def to_detector_post(self) -> dict[str, Any]:
        """Return the dict shape ``shill_detector`` expects.

        Includes ``author`` / ``text`` / ``ts`` plus optional ``channel`` /
        ``url`` metadata. ``source`` is deliberately omitted — it collides
        with ``shill_detector._post_author``'s fallback author key.
        """
        post: dict[str, Any] = {
            "author": self.author,
            "text": self.text,
            "ts": self.ts,
        }
        if self.channel:
            post["channel"] = self.channel
        if self.url:
            post["url"] = self.url
        return post

    @classmethod
    def from_channel_message(cls, msg: ChannelMessage) -> ShillPost:
        """Map a ``news_ingest.ChannelMessage`` to a Telegram ``ShillPost``.

        Author = the channel's public username (the identity that posted).
        """
        username = (msg.channel_username or msg.channel or "unknown").lstrip("@")
        url = f"https://t.me/{msg.channel_username}/{msg.message_id}" if msg.channel_username else None
        return cls(
            author=username,
            text=msg.text or "",
            ts=_dt_epoch(msg.posted_at),
            source="telegram",
            channel=username,
            url=url,
        )

    @classmethod
    def from_social_post(cls, raw: dict[str, Any], source: str) -> ShillPost:
        """Map an X/Farcaster post dict to a ``ShillPost`` (tolerant, never raises).

        Accepts flat post shapes (``handle``/``username``/``text``/
        ``created_at``) and nested author shapes (``user.username`` for X,
        ``author.username`` for Farcaster).
        """
        author = _first_text(
            _nested_text(raw, ("user", "author"), ("username", "screen_name", "handle")),
            raw.get("handle"),
            raw.get("username"),
            raw.get("screen_name"),
            raw.get("author"),
            raw.get("kol_id"),
        )
        text = _first_text(raw.get("text"), raw.get("content"), raw.get("body"), raw.get("description"))
        ts = _epoch(
            raw.get("ts")
            or raw.get("timestamp")
            or raw.get("created_at")
            or raw.get("published")
            or raw.get("pubDate")
            or raw.get("published_at")
            or raw.get("posted_at")
        )
        url = _first_text(raw.get("url"), raw.get("post_url"), raw.get("link"), raw.get("permalink"))
        return cls(
            author=author or "unknown",
            text=text or "",
            ts=ts,
            source=source if source in _SOURCES else "x",
            url=url or None,
        )


def _nested_text(raw: dict[str, Any], containers: tuple[str, ...], keys: tuple[str, ...]) -> str:
    """Pull the first non-empty string from nested author dicts."""
    for container in containers:
        obj = raw.get(container)
        if isinstance(obj, dict):
            value = _first_text(*(obj.get(key) for key in keys))
            if value:
                return value
    return ""
