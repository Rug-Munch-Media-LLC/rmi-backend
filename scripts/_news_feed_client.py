"""Shared feed fetch client: browser TLS fingerprint + one gentle retry.

Split out of test_news_sources.py (2026-10-15). The fetch policy is one
concern and both the prober and the repair tool want it:

* curl_cffi Chrome impersonation — httpx's TLS signature is 403'd by
  Cloudflare-protected feeds that answer curl/urllib fine (the entire
  Substack block of 2026-08-13 was this; all 9 feeds served real RSS to a
  browser client).
* a single retry on 429/403/202 — a throttle/WAF answer is NOT death.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

try:  # environment-dependent: falls back to httpx when absent
    from curl_cffi import requests as _cffi_requests
except ImportError:  # pragma: no cover
    _cffi_requests = None


def fetch_feed(url: str, timeout_seconds: float) -> tuple[int, str]:
    """Fetch a feed with a browser TLS fingerprint when available."""
    if _cffi_requests is not None:
        try:
            r = _cffi_requests.get(
                url,
                impersonate="chrome",
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            return int(r.status_code), r.text
        except Exception as exc:  # any cffi failure -> httpx fallback
            log.debug("curl_cffi fetch failed for %s: %s", url, exc)
    r = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=timeout_seconds,
        follow_redirects=True,
    )
    return r.status_code, r.text


def fetch_with_retry(url: str, timeout_seconds: float) -> tuple[int, str]:
    """fetch_feed with one gentle retry for throttle/WAF answers."""
    status_code, text = 0, ""
    for attempt in range(2):
        status_code, text = fetch_feed(url, timeout_seconds)
        if status_code in (429, 403, 202) and attempt == 0:
            time.sleep(4.0)
            continue
        break
    return status_code, text
