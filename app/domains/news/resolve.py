"""Resolve news.google.com interstitials to the real publisher URL.

Google removed the server-side redirect: ``GET /rss/articles/<id>`` answers 200
with a ~600 KB JavaScript shell and no publisher link anywhere in the markup.
The ``batchexecute`` decoder is also closed (returns ``null`` with code 3), so
the only path left is a real browser navigating the interstitial. Chromium on
Talos resolves 3/3 in ~4 s.

Never invent a URL: when resolution fails this returns ``None`` and the caller
must keep the body untouched and mark the row ``google_shell``.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from app.domains.news.quality import is_google_news_url

#: Redirect budget per article (matches the ingest contract).
MAX_HOPS = 3
_NAV_TIMEOUT_MS = 45_000
_SETTLE_MS = 3_500
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

Navigate = Callable[[str, int], "str | None"]


def _navigate_browser(url: str, max_hops: int) -> str | None:
    """Drive headless Chromium and return the publisher URL it settles on."""
    from playwright.sync_api import sync_playwright

    hops: list[int] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page()

            def _record(response: object) -> None:
                try:
                    if response.request.is_navigation_request() and response.status in _REDIRECT_CODES:
                        hops.append(response.status)
                except (AttributeError, RuntimeError):
                    return

            page.on("response", _record)
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            page.wait_for_timeout(_SETTLE_MS)
            final = page.url or ""
        except Exception:
            return None
        finally:
            browser.close()
    if len(hops) > max_hops:
        return None
    return _clean(final)


def _clean(candidate: str) -> str | None:
    """Accept only absolute http(s) URLs that left Google News."""
    if not candidate or is_google_news_url(candidate):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or "." not in parsed.hostname:
        return None
    return candidate


class Resolver:
    """One resolution entry point; ``navigate`` is the test seam."""

    def __init__(self, navigate: Navigate = _navigate_browser, max_hops: int = MAX_HOPS) -> None:
        self._navigate = navigate
        self._max_hops = max_hops

    def canonical(self, url: str) -> str | None:
        """Return the publisher URL, or None when it cannot be resolved."""
        if not is_google_news_url(url):
            return _clean(url)
        resolved = self._navigate(url, self._max_hops)
        return _clean(resolved) if resolved else None
