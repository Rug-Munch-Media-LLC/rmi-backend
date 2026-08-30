"""Coordinated shill-campaign detection — pure, deterministic grouping.

A shill campaign is a token (address or ``$TICKER``) that many distinct
accounts / KOL call channels push inside a short window. Legit coverage is a
couple of outlets; a coordinated campaign is N distinct pushers in W hours —
that is the signal.

Ported from the bot's ``channels/shill_watch.detect_shill_campaigns`` so the
backend owns the detection logic and both the bot + frontend consume it via
``/api/v1/shill/*``. ``detect_shill_campaigns`` is pure + deterministic for a
fixed input (no I/O, no network) — the service layer feeds it normalized
article dicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

#: Blue-chip tickers are mentioned by everyone constantly — "coordinated
#: shill" for them is noise by definition.
_BLUECHIP = {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB"}

#: Default min distinct pushing accounts for a token to be a campaign.
DEFAULT_MIN_ACCOUNTS = int(__import__("os").getenv("RMI_SHILL_MIN_ACCOUNTS", "3"))
#: Default min total mentions across those accounts.
DEFAULT_MIN_MENTIONS = int(__import__("os").getenv("RMI_SHILL_MIN_MENTIONS", "3"))
#: Detection window for grouping (hours).
DEFAULT_WINDOW_HOURS = int(__import__("os").getenv("RMI_SHILL_WINDOW_HOURS", "24"))


@dataclass(slots=True)
class ShillCampaign:
    """One detected coordinated shill campaign."""

    token: str
    chain: str
    accounts: list[str]  # distinct pushing channels, most-mentions first
    mentions: list[dict[str, str]] = field(default_factory=list)  # {channel, title, url}
    window_start: str = ""
    window_end: str = ""
    score: float = 0.0
    importance: float = 0.0  # rerank importance (2026-08-30 routing)
    screenshots: list[dict[str, str]] = field(default_factory=list)
    scanner_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable dedupe identity: token + chain."""
        return f"{self.chain}:{self.token}"

    def to_dict(self) -> dict[str, Any]:
        """Serializable shape for the API response."""
        return {
            "key": self.key,
            "token": self.token,
            "chain": self.chain,
            "accounts": self.accounts,
            "mentions": self.mentions,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "score": self.score,
            "importance": self.importance,
            "screenshots": self.screenshots,
            "scanner_evidence": self.scanner_evidence,
            "accounts_count": len(self.accounts),
            "mentions_count": len(self.mentions),
        }


def _posted_at(article: Any) -> datetime:
    """Article timestamp (UTC); falls back to now for undated rows."""
    ts = article.get("posted_at") if isinstance(article, dict) else getattr(article, "posted_at", None)
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _article_fields(article: Any) -> tuple[str, str, str, list[str]]:
    """(title, channel, url, entities) for a dict/NewsArticle (fail-open)."""
    if isinstance(article, dict):
        title = str(article.get("title") or article.get("text") or "")[:140]
        channel = str(article.get("source_channel") or article.get("channel") or "unknown")
        url = str(article.get("source_url") or article.get("url") or "")[:240]
        entities = [str(e) for e in (article.get("entities") or [])]
        return title, channel, url, entities
    title = str(getattr(article, "title", "") or "")[:140]
    channel = str(getattr(article, "source_channel", "") or "unknown")
    url = str(getattr(article, "source_url", "") or "")[:240]
    entities = [str(e) for e in (getattr(article, "entities", None) or [])]
    return title, channel, url, entities


#: Solana base58 mint / EVM 0x-address token identity regex.
_TOKEN_ADDRESS_RE = re.compile(r"\b(?:0x[a-fA-F0-9]{6,64}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")


def _token_ids(article: Any) -> set[tuple[str, str]]:
    """Token identities for one article: (chain, address) + ('ticker', $SYM).

    Dict-shaped articles carry ``entities`` (the primary mode the service
    layer receives from the bot). Non-dict rows (e.g. a ``NewsArticle``-like
    object) are scanned with a lightweight address/``$TICKER`` regex so
    detection stays self-contained in the backend. ``$TICKER`` is only kept
    when it is not a blue-chip (blue-chips are mentioned by everyone).
    """
    ids: set[tuple[str, str]] = set()
    if isinstance(article, dict):
        for e in article.get("entities") or []:
            e = str(e)
            if e.startswith("$") and len(e) >= 3:
                ids.add(("ticker", e.upper()))
            elif len(e) >= 32:
                ids.add(("address", e))
        return ids
    # Non-dict article — scan the raw text/body for $TICKERS and addresses.
    text = " ".join(
        str(v or "")
        for v in (
            getattr(article, "title", ""),
            getattr(article, "body", ""),
            getattr(article, "text", ""),
            getattr(article, "content", ""),
        )
        if v
    )
    for m in re.findall(r"\$([A-Za-z]{3,10})\b", text):
        sym = m.upper()
        if sym not in _BLUECHIP:
            ids.add(("ticker", "$" + sym))
    for m in _TOKEN_ADDRESS_RE.findall(text):
        ids.add(("address", m))
    return ids


def detect_shill_campaigns(
    articles: list[Any],
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_accounts: int = DEFAULT_MIN_ACCOUNTS,
    min_mentions: int = DEFAULT_MIN_MENTIONS,
) -> list[ShillCampaign]:
    """Flag tokens pushed by >= *min_accounts* distinct channels in a window.

    Pure + deterministic for a fixed input: groups by token identity, counts
    distinct pushing channels and total mentions inside ``window_hours``, and
    returns campaigns sorted by score (accounts*10 + mentions) desc.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=max(1, window_hours))

    buckets: dict[str, dict[str, Any]] = {}

    def _bucket(token: str, chain: str) -> dict[str, Any]:
        key = f"{chain}:{token}"
        if key not in buckets:
            buckets[key] = {
                "token": token,
                "chain": chain,
                "accounts": {},
                "mentions": [],
                "earliest": now,
            }
        return buckets[key]

    for article in articles or []:
        posted = _posted_at(article)
        if posted < cutoff:
            continue
        title, channel, url, _entities = _article_fields(article)
        for kind, value in _token_ids(article):
            if kind not in ("address", "ticker"):
                continue
            if not value or len(str(value)) < 3:
                continue
            if kind == "ticker" and str(value).upper().lstrip("$") in _BLUECHIP:
                continue
            b = _bucket(str(value), "ethereum" if kind == "address" else "ticker")
            b["accounts"].setdefault(channel, 0)
            b["accounts"][channel] += 1
            b["earliest"] = min(b["earliest"], posted)
            b["mentions"].append({"channel": channel, "title": title, "url": url})

    campaigns: list[ShillCampaign] = []
    for b in buckets.values():
        accounts = sorted(b["accounts"].items(), key=lambda kv: -kv[1])
        if len(accounts) < min_accounts:
            continue
        mentions = b["mentions"]
        if len(mentions) < min_mentions:
            continue
        campaigns.append(
            ShillCampaign(
                token=b["token"],
                chain=b["chain"],
                accounts=[a for a, _ in accounts],
                mentions=mentions,
                window_start=b["earliest"].isoformat(),
                window_end=now.isoformat(),
                score=len(accounts) * 10.0 + len(mentions),
            )
        )
    return sorted(campaigns, key=lambda c: c.score, reverse=True)


__all__ = [
    "DEFAULT_MIN_ACCOUNTS",
    "DEFAULT_MIN_MENTIONS",
    "DEFAULT_WINDOW_HOURS",
    "ShillCampaign",
    "detect_shill_campaigns",
]
