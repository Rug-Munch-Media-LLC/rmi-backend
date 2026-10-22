"""HTTP access to the Telegram public preview (no auth, no account session)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

PREVIEW_URL = "https://t.me/s/{channel}"
USER_AGENT = "Mozilla/5.0 (compatible; rmi-news-ingest/1.0)"
TIMEOUT = 15.0


@dataclass(slots=True)
class PreviewResponse:
    status: int
    html: str | None
    etag: str | None
    not_modified: bool = False


async def fetch_preview(
    client: httpx.AsyncClient,
    channel: str,
    *,
    before: int | None = None,
    etag: str | None = None,
    timeout: float = TIMEOUT,
) -> PreviewResponse:
    """GET ``t.me/s/<channel>[?before=<id>]``; honour ``If-None-Match``.

    ``before`` walks backwards in history (pagination); omitting it returns the
    newest page. A 304 yields ``not_modified=True`` and no body.
    """
    url = PREVIEW_URL.format(channel=channel)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    if etag:
        headers["If-None-Match"] = etag
    params: dict[str, Any] = {"before": before} if before else {}
    response = await client.get(url, params=params, headers=headers, timeout=timeout)
    if response.status_code == 304:
        return PreviewResponse(304, None, etag, not_modified=True)
    response.raise_for_status()
    return PreviewResponse(response.status_code, response.text, response.headers.get("etag"))
