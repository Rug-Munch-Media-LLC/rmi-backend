"""Content-type validation for candidate icon URLs.

A URL that returns HTML, SVG or an error page is useless to a browser, so the
content type is proven before the URL is cached. ``HEAD`` is tried first, but
several CDNs (DexScreener) answer ``HEAD`` with 4xx while ``GET`` serves a PNG —
so any HEAD failure falls through to a ranged GET.
"""

from __future__ import annotations

from typing import Any

_TIMEOUT = 6.0
_MAX_BYTES = 10 * 1024 * 1024
_USER_AGENT = "rmi-token-icons/1.0 (+https://rugmunch.io)"


def _content_type(response: Any) -> str:
    return str(response.headers.get("content-type", "")).split(";")[0].strip().lower()


def _acceptable(content_type: str) -> bool:
    return content_type.startswith("image/") and "svg" not in content_type


async def _head(client: Any, url: str, headers: dict[str, str]) -> Any | None:
    """HEAD probe that returns ``None`` when the CDN refuses the method."""
    try:
        return await client.head(url, timeout=_TIMEOUT, follow_redirects=True, headers=headers)
    except Exception:
        return None


async def image_content_ok(client: Any, url: str) -> str | None:
    """Return the image content-type when ``url`` really serves an image."""
    headers = {"User-Agent": _USER_AGENT}
    head = await _head(client, url, headers)
    if head is not None and head.status_code < 400:
        ctype = _content_type(head)
        if ctype:
            return ctype if _acceptable(ctype) else None
    try:
        response = await client.get(
            url,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={**headers, "Range": "bytes=0-1023"},
        )
    except Exception:
        return None
    if response.status_code >= 400:
        return None
    length = str(response.headers.get("content-length", ""))
    if length.isdigit() and int(length) > _MAX_BYTES:
        return None
    content_type = _content_type(response)
    if not content_type or not _acceptable(content_type):
        return None
    return content_type
