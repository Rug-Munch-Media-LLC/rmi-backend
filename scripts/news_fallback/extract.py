"""Article extraction: trafilatura body + metadata, with a quality gate."""

from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import trafilatura

MIN_BODY = 200
MIN_PARAGRAPHS = 2
_OG = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)', re.I
)
_OG_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:title["\']', re.I
)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_SOFT_404 = re.compile(
    r"\b(404|not found|page not found|doesn.t exist|access denied|forbidden)\b", re.I
)
MAX_BODY = 20000


@dataclass
class Article:
    """A extracted, renderable article."""

    url: str
    title: str
    body: str
    published: datetime | None


def _page_title(html: str) -> str:
    """og:title <-> h1, in that order; <title> is often site-wide boilerplate."""
    for pattern in (_OG, _OG_ALT):
        match = pattern.search(html)
        if match:
            return htmllib.unescape(match.group(1)).strip()
    match = _H1.search(html)
    if match:
        return htmllib.unescape(_TAGS.sub(" ", match.group(1))).strip()
    return ""


def _parse_date(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError:
        return None


def extract(url: str, html: str) -> Article | None:
    """Return an Article, or None when the page is not usable journalism."""
    body = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    body = (body or "").strip()
    if len(body) < MIN_BODY:
        return None
    long_paras = [p for p in body.split("\n") if len(p.strip()) >= 120]
    if len(long_paras) < MIN_PARAGRAPHS:
        return None  # navigation/marketing shell, not an article
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = _page_title(html) or (getattr(meta, "title", "") or "").strip()
    if len(title) < 8 or _SOFT_404.search(title):
        return None
    return Article(
        url=url,
        title=title[:500],
        body=body[:MAX_BODY],
        published=_parse_date(getattr(meta, "date", None)),
    )
