"""Article URL discovery: RSS entries first, listing-page links second."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import feedparser

_SKIP = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|search|login|signup|"
    r"privacy|terms|about|contact|cookie|subscribe|newsletter)(/|$)",
    re.I,
)
_BAD_EXT = re.compile(r"\.(css|js|png|jpe?g|gif|svg|webp|ico|pdf|zip|xml|rss)$", re.I)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_ISO = re.compile(r"/(20\d{2})/(\d{2})/")
_SECTION = re.compile(r"/(blog|news|research|insights|posts|articles|analysis|learn|press)/", re.I)


def feed_entries(body: str) -> list[tuple[str, str]]:
    """Return (link, title) pairs from a feed body, empty when not a feed."""
    parsed = feedparser.parse(body)
    if not getattr(parsed, "entries", None):
        return []
    out: list[tuple[str, str]] = []
    for entry in parsed.entries[:30]:
        link = (entry.get("link") or "").strip()
        if link:
            out.append((link, (entry.get("title") or "").strip()))
    return out


def listing_links(html: str, base: str, limit: int = 40) -> list[str]:
    """Same-host article-ish links from an HTML listing page, in page order."""
    host = urlparse(base).netloc.removeprefix("www.")
    seen: set[str] = set()
    out: list[str] = []
    for raw in _HREF.findall(html):
        url = urljoin(base, raw).split("#", 1)[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.removeprefix("www.") != host:
            continue
        path = parsed.path
        if len(path) < 6 or _SKIP.search(path) or _BAD_EXT.search(path):
            continue
        if not (_ISO.search(path) or _SECTION.search(path)):
            continue
        slug = path.rstrip("/").rsplit("/", 1)[-1]
        if len(slug) < 8:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out
