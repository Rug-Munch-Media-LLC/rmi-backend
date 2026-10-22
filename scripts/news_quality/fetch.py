"""Publisher fetch + extraction for the news-quality backfill.

Direct GET with a browser UA; FlareSolverr only when challenged (403/429/503).
Trafilatura extraction runs precision-first: a paywall teaser yields a short
body, which the scorer then classifies as ``thin``/``stub`` rather than "ok".
"""

from __future__ import annotations

import httpx

FLARESOLVERR = "http://127.0.0.1:8192/v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
#: Below this a fetched body is not the article — keep the RSS fragment instead.
MIN_PUBLISHER_BODY = 400
_CHALLENGE_CODES = frozenset({403, 429, 503})


def _solve(url: str) -> str:
    """Ask FlareSolverr for the page HTML; empty string on any failure."""
    try:
        solved = httpx.post(
            FLARESOLVERR,
            json={"cmd": "request.get", "url": url, "maxTimeout": 45_000},
            timeout=90.0,
        )
        return str(((solved.json() or {}).get("solution") or {}).get("response") or "")
    except (httpx.HTTPError, ValueError):
        return ""


def publisher_html(url: str) -> str:
    """Publisher page HTML, or ``""`` when it cannot be fetched."""
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    try:
        resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=25.0)
    except httpx.HTTPError:
        return ""
    if resp.status_code in _CHALLENGE_CODES:
        return _solve(url)
    return resp.text if resp.status_code < 400 else ""


def body_from_html(html: str, url: str) -> str:
    """Trafilatura body, precision-first; empty when the page is not journalism."""
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)
    return (text or "").strip()
