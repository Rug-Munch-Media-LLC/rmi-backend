"""Coordinated-shill alert service: ShillPost → PlatformAlert → routing plan.

Wires the pure ``shill_detector`` rules into the canonical platform-alert
pipeline: group normalized posts by author, run coordinated-shill detection
(≥3 KOLs on the same token within a <2h window; ≥5 KOLs → critical) and —
when attribution data (``known_scammers`` / ``kol_rug_history``) is supplied —
per-KOL signal analysis, normalize the detector records into
``PlatformAlert`` (category=syndicate, source=shill_detector), and produce the
routing plan via ``alerts.publisher.route_alerts`` (rmialerts = high+,
main_channel = critical).

Pure decision layer: ``ingest_telegram_posts`` is the only I/O entry point
(fail-open) and ``route`` never performs a network send.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.databus.shill_detector import analyze_shill_signals, detect_coordinated_shills
from app.domains.alerts.models import PlatformAlert
from app.domains.alerts.publisher import route_alerts
from app.domains.news_ingest.ingest import channel_username_from_config, read_channel_messages
from app.domains.news_ingest.normalize import extract_entities
from app.domains.shill_alert.models import ShillPost

if TYPE_CHECKING:
    from collections.abc import Callable

_VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
_COORDINATED_CONFIDENCE = 0.8


def _normalize_author(author: Any) -> str:
    """Normalize an author handle for grouping (``@`` stripped, lowercased)."""
    return str(author or "unknown").strip().lstrip("@").lower()


# ── Grouping ────────────────────────────────────────────────────────────────


def group_by_author(posts: list[ShillPost]) -> dict[str, list[dict[str, Any]]]:
    """Group detector-post dicts by normalized author handle.

    Handles with different casing / ``@`` prefixes collapse to one key, so
    the detector sees the KOL's full post set regardless of how the source
    platform surfaced it.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for post in posts or []:
        grouped.setdefault(_normalize_author(post.author), []).append(post.to_detector_post())
    return grouped


# ── Detector-record → PlatformAlert ─────────────────────────────────────────


def _kols_of(alert_dict: dict[str, Any]) -> list[str]:
    """Distinct normalized KOL handles from a detector alert record."""
    kols: list[str] = []
    seen: set[str] = set()
    for item in str(alert_dict.get("kol_id") or "").split(","):
        handle = _normalize_author(item)
        if handle and handle not in seen:
            seen.add(handle)
            kols.append(handle)
    evidence = (alert_dict.get("evidence") or [{}])[0] if alert_dict.get("evidence") else {}
    for item in evidence.get("kols_involved", []) or []:
        handle = _normalize_author(item)
        if handle and handle not in seen:
            seen.add(handle)
            kols.append(handle)
    return kols


def _involved_posts(alert_dict: dict[str, Any], posts: list[ShillPost]) -> list[ShillPost]:
    kols = set(_kols_of(alert_dict))
    return [p for p in posts or [] if _normalize_author(p.author) in kols]


def _map_severity(alert_dict: dict[str, Any], kols: list[str]) -> str:
    """Honor the detector's own severity; fall back to the KOL-count ladder.

    The detector already emits ``critical`` for ≥5 KOLs and ``high``
    otherwise — this honors that value when present and applies the same
    ladder when a record lacks a severity.
    """
    own = str(alert_dict.get("severity") or "").strip().lower()
    if own in _VALID_SEVERITIES:
        return own
    return "critical" if len(kols) >= 5 else "high"


def _build_body(alert_dict: dict[str, Any], kols: list[str], sources: list[str]) -> str:
    """Human-readable body: pattern + handles + time window + sources."""
    pattern = str(alert_dict.get("pattern") or "coordinated")
    evidence = (alert_dict.get("evidence") or [{}])[0] if alert_dict.get("evidence") else {}
    parts = [f"Pattern: {pattern}", f"{len(kols)} KOLs: {', '.join(kols)}"]
    window = evidence.get("time_window_h")
    if window is not None:
        parts.append(f"within {window}h")
    if sources:
        parts.append(f"sources: {', '.join(sources)}")
    detail = str(evidence.get("detail") or "")
    if detail:
        parts.append(detail)
    return ". ".join(parts) + "."


def _to_platform_alert(alert_dict: dict[str, Any], posts: list[ShillPost], *, now: float) -> PlatformAlert:
    """Normalize one detector record into a canonical syndicate alert."""
    kols = _kols_of(alert_dict)
    involved = _involved_posts(alert_dict, posts)
    sources = sorted({p.source for p in involved})
    token = str(alert_dict.get("token_symbol") or alert_dict.get("token_address") or "UNKNOWN")
    pattern = str(alert_dict.get("pattern") or "coordinated")
    if pattern == "known_scammer":
        title = f"Known scammer shilling: {token}"
    else:
        title = f"Coordinated shill: {token} pumped by {len(kols)} channels"
    return PlatformAlert(
        severity=_map_severity(alert_dict, kols),
        category="syndicate",
        title=title,
        body=_build_body(alert_dict, kols, sources),
        token_address=str(alert_dict.get("token_address") or "") or None,
        chain=str(alert_dict.get("token_chain") or "") or None,
        source="shill_detector",
        # event_key is auto-derived by the model validator via the canonical
        # alerts-ingestion helper (PlatformAlert.derive_event_key):
        # sha256(category|entity|source|5-min-ts-bucket).
        confidence=_COORDINATED_CONFIDENCE,
        ts=str(alert_dict.get("created_at") or "") or datetime.fromtimestamp(now, tz=UTC).isoformat(),
        urls=[p.url for p in involved if p.url],
    )


# ── Detection ───────────────────────────────────────────────────────────────


def run_shill_detection(
    posts: list[ShillPost],
    *,
    known_scammers: set[str] | list[str] | None = None,
    kol_rug_history: dict[str, list[dict[str, Any]]] | None = None,
) -> list[PlatformAlert]:
    """Detect coordinated shills across *posts* and emit canonical alerts.

    Groups posts by normalized author, then runs the coordinated-shill
    detector (≥3 KOLs bullish on the same token within <2h; ≥5 KOLs →
    critical). When attribution data is supplied (``known_scammers`` /
    ``kol_rug_history``), ``analyze_shill_signals`` additionally flags known
    bad actors per KOL (known_scammer → critical). ``now`` is derived from
    the posts' own timestamps (deterministic for tests).
    """
    grouped = group_by_author(posts)
    now = max((p.ts for p in posts or []), default=datetime.now(UTC).timestamp())

    if known_scammers is not None or kol_rug_history is not None:
        summary = analyze_shill_signals(
            grouped,
            kol_wallets_by_kol={},
            wallet_txs_by_wallet={},
            known_scammers=known_scammers,
            kol_rug_history=kol_rug_history,
            now=now,
        )
        alert_dicts = summary["alerts"]
    else:
        alert_dicts = detect_coordinated_shills(grouped, now=now)

    return [_to_platform_alert(ad, posts, now=now) for ad in alert_dicts]


# ── Telegram ingestion ──────────────────────────────────────────────────────


async def ingest_telegram_posts(client: Any, channels: list[dict], limit: int = 50) -> list[ShillPost]:
    """Read Telegram channel messages and keep the token-mentioning ones.

    Uses ``news_ingest.read_channel_messages`` (injectable client — a
    wrapper with ``iter_messages`` or an async fake with ``read_messages``).
    Messages are filtered to those mentioning a ``$SYMBOL`` / contract
    address (``extract_entities``) and mapped to ``ShillPost`` with the
    channel username as author. Fail-open: any error → ``[]``.
    """
    posts: list[ShillPost] = []
    try:
        for channel in channels or []:
            username = channel_username_from_config(channel)
            if not username:
                continue
            messages = await read_channel_messages(client, username, max(1, int(limit)))
            for msg in messages:
                if msg.text and extract_entities(msg.text):
                    posts.append(ShillPost.from_channel_message(msg))
    except Exception:
        return []
    return posts


# ── X / Farcaster ingestion ─────────────────────────────────────────────────


def _unwrap_post_list(raw: Any) -> list[Any]:
    """Coerce a reader result into a list of post dicts.

    Accepts a bare list, or a wrapper dict holding a list under one of the
    conventional keys (``posts`` / ``casts`` / ``tweets`` / ``items`` /
    ``data`` / ``results``) — e.g. the in-repo X reader
    ``app.caching_shield.social_feed.get_twitter_feed`` returns
    ``{"tweets": [...]}``. Anything else → ``[]``.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("posts", "casts", "tweets", "items", "data", "results"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


async def _reader_posts(reader: Callable[[int], Any], limit: int) -> list[dict[str, Any]]:
    """Call *reader* and normalize its output to a list of post dicts.

    Tolerates sync readers and async (coroutine-returning) readers alike.
    Non-dict items are dropped; a reader returning garbage yields ``[]``.
    """
    raw = reader(max(1, int(limit)))
    if asyncio.iscoroutine(raw):
        raw = await raw
    return [item for item in _unwrap_post_list(raw) if isinstance(item, dict)]


def _normalize_timestamp_keys(item: dict[str, Any]) -> dict[str, Any]:
    """Map provider timestamp keys onto the keys ``from_social_post`` reads.

    The in-repo X reader (``get_twitter_feed``) emits ``published`` (an RSS
    ``pubDate`` — RFC 822/2822, e.g. ``Wed, 13 Aug 2026 10:15:00 GMT``),
    which ``from_social_post`` also reads directly; surfacing it as
    ``created_at`` additionally keeps older casters working. Accepts either
    ``published`` or ``pubDate``. Never mutates the caller's dict.
    """
    if "created_at" not in item:
        source = item.get("published") or item.get("pubDate")
        if source:
            normalized = dict(item)
            normalized["created_at"] = source
            return normalized
    return item


async def ingest_farcaster_posts(reader: Callable[[int], Any], limit: int = 50) -> list[ShillPost]:
    """Read Farcaster casts and keep the token-mentioning ones.

    ``reader`` is an injectable duck-typed callable
    ``reader(limit) -> list[post dict]`` (or a dict wrapping the list under
    ``casts`` / ``posts`` / ``data``). Expected cast dict shape (any subset):

        {"author": {"username": "handle"}, "text": "...",
         "timestamp": "ISO-8601", "url": "https://warpcast.com/..."}

    Flat shapes are also accepted (``handle`` / ``username``, ``text``,
    ``created_at`` / ``published_at``, ``post_url`` / ``permalink``) — see
    ``ShillPost.from_social_post``. There is no in-repo Farcaster *post*
    reader today (``socialfi_resolver`` only exposes profile lookups), so
    pass an adapter over your Farcaster Hub / Neynar reader. Posts are
    filtered to those mentioning a ``$SYMBOL`` / contract address
    (``extract_entities``) and mapped via ``ShillPost.from_social_post``
    with ``source="farcaster"``. Fail-open: any error → ``[]``.
    """
    posts: list[ShillPost] = []
    try:
        for item in await _reader_posts(reader, limit):
            text = str(item.get("text") or item.get("content") or "")
            if text and extract_entities(text):
                posts.append(ShillPost.from_social_post(item, source="farcaster"))
    except Exception:
        return []
    return posts


async def ingest_x_posts(reader: Callable[[int], Any], limit: int = 50) -> list[ShillPost]:
    """Read X posts and keep the token-mentioning ones.

    ``reader`` is an injectable duck-typed callable
    ``reader(limit) -> list[post dict]`` (or a dict wrapping the list under
    ``tweets`` — the exact shape of the in-repo reader
    ``app.caching_shield.social_feed.get_twitter_feed``, which can be passed
    directly). Expected X post dict shape (any subset):

        {"handle": "handle", "text": "...",
         "published": "ISO-8601", "url": "https://x.com/..."}

    Nested ``user.username`` is also accepted.
    ``app.databus.social_feeds`` additionally exposes a sync Nitter reader
    (``fetch_nitter``) whose article dicts carry the username under
    ``source`` — wrap it if you need it. Posts are filtered to those
    mentioning a ``$SYMBOL`` / contract address (``extract_entities``) and
    mapped via ``ShillPost.from_social_post`` with ``source="x"``.
    Fail-open: any error → ``[]``.
    """
    posts: list[ShillPost] = []
    try:
        for item in await _reader_posts(reader, limit):
            item = _normalize_timestamp_keys(item)
            text = str(item.get("text") or item.get("content") or "")
            if text and extract_entities(text):
                posts.append(ShillPost.from_social_post(item, source="x"))
    except Exception:
        return []
    return posts


async def ingest_x_from_feed(limit: int = 50) -> list[ShillPost]:
    """Read the in-repo X feed and keep the token-mentioning posts.

    Convenience wrapper over ``ingest_x_posts`` wired to the real reader
    ``app.caching_shield.social_feed.get_twitter_feed`` (async, returns
    ``{"tweets": [...]}``). Its tweet dicts carry an RFC 822 ``published``
    value (RSS ``pubDate``) and a top-level ``handle``/``author`` — both
    handled by ``parse_timestamp`` / ``ShillPost.from_social_post``, so the
    parsed ``ts`` is real feed time instead of the old fail-open ``0.0``.
    The reader import is lazy: a missing or unhealthy social-feed module
    never blocks ingestion. Fail-open: any error → ``[]``.
    """
    try:
        from app.caching_shield.social_feed import get_twitter_feed
    except Exception:
        return []
    return await ingest_x_posts(get_twitter_feed, limit=limit)


# ── Routing ─────────────────────────────────────────────────────────────────


def route(alerts: list[PlatformAlert]) -> dict[str, Any]:
    """Produce the channel routing plan for *alerts* (default channels).

    Returns a summary of the publisher's pure routing plan: the full plan,
    per-channel alert lists, and counts. Defaults: rmialerts = high+,
    main_channel = critical-only. The plan is never sent — the caller
    executes the transport.
    """
    plan = route_alerts(alerts)
    return {
        "plan": plan,
        "channels": {entry["channel"]: entry["alerts"] for entry in plan},
        "counts": {entry["channel"]: entry["count"] for entry in plan},
    }
