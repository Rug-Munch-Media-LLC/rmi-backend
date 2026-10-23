"""Ghost Content API client — server-side only.

Ghost (headless CMS) backs the site's blog / feed / newsletter. The frontend
reads posts through ``/api/v1/intelligence/ghost/*``; the Content API key is
read from the environment and never leaves the server.

Two operational notes:

* Ghost answers **301** to the public site when its configured ``url`` is
  https and the request arrives over plain http with an internal Host
  (``rmi-ghost:2368``). Sending ``X-Forwarded-Proto``/``X-Forwarded-Host``
  makes Ghost treat the call as the public site and serve JSON directly,
  removing the hop through the public edge.
* Failures are *classified*, never swallowed: the caller gets a
  :class:`GhostUnavailable` naming the host instead of an empty 200.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_GHOST_URL = "http://rmi-ghost:2368"
DEFAULT_SITE_URL = "https://rugmunch.io"

MAX_LIMIT = 50
# DI seam: tests swap in an httpx client backed by MockTransport.
_CLIENT_FACTORY: Any = httpx.AsyncClient
_TAG_RE = re.compile(r"[^A-Za-z0-9_-]+")


class GhostUnavailable(Exception):
    """Ghost could not serve the request. Carries a client-safe description."""

    def __init__(self, code: str, host: str, detail: str = "") -> None:
        self.code = code
        self.host = host
        self.detail = detail
        super().__init__(f"{code} host={host} detail={detail}".strip())

    def payload(self) -> dict[str, Any]:
        """Degraded body: same top-level shape, explicit error, host named."""
        error: dict[str, Any] = {"code": self.code, "host": self.host, "degraded": True}
        if self.detail:
            error["detail"] = self.detail
        return {
            "posts": [],
            "error": error,
            "meta": {"source": "ghost", "degraded": True},
        }


def ghost_config() -> tuple[str, str, str]:
    """(base_url, content_key, site_url) read at call time so env changes test cleanly."""
    base = (os.getenv("GHOST_URL") or DEFAULT_GHOST_URL).strip().rstrip("/")
    key = (os.getenv("GHOST_CONTENT_API_KEY") or "").strip()
    site = (os.getenv("GHOST_SITE_URL") or DEFAULT_SITE_URL).strip().rstrip("/")
    return base, key, site


def build_filter(tag: str) -> str:
    """Ghost filter string: published only, optionally scoped to one tag.

    ``tag`` is a single slug. Anything after the first non-slug character is
    dropped, so a crafted value cannot append its own filter clause.
    """
    parts = ["status:published"]
    token = _TAG_RE.split(tag.strip())[0] if tag and tag.strip() else ""
    if token:
        parts.append(f"tag:{token}")
    return "+".join(parts)


def normalize_post(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Ghost Content API post to the public feed shape."""
    return {
        "id": raw.get("id"),
        "slug": raw.get("slug"),
        "url": raw.get("url"),
        "title": raw.get("title"),
        "excerpt": raw.get("excerpt") or raw.get("custom_excerpt") or "",
        "html": raw.get("html") or "",
        "feature_image": raw.get("feature_image") or "",
        "published_at": raw.get("published_at"),
        "updated_at": raw.get("updated_at"),
        "reading_time": raw.get("reading_time"),
        "tags": [t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict) and t.get("name")],
        "authors": [a.get("name") for a in (raw.get("authors") or []) if isinstance(a, dict) and a.get("name")],
    }


def _classify(exc: Exception, host: str) -> GhostUnavailable:
    detail = str(exc)[:160]
    if isinstance(exc, httpx.TimeoutException):
        return GhostUnavailable("ghost_timeout", host, detail)
    if isinstance(exc, httpx.ConnectError):
        low = detail.lower()
        dns = (
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
        )
        code = "ghost_dns_error" if any(m in low for m in dns) else "ghost_unreachable"
        return GhostUnavailable(code, host, detail)
    return GhostUnavailable("ghost_unreachable", host, detail)


async def _call(
    path: str,
    params: dict[str, Any],
    *,
    client_factory: Any = None,
) -> dict[str, Any]:
    """GET one Content API path, returning parsed JSON or raising GhostUnavailable."""
    base, key, site = ghost_config()
    host = base.split("://", 1)[-1]
    if not key:
        raise GhostUnavailable("ghost_key_not_configured", host)
    query = {**params, "key": key}
    headers = {
        "Accept-Version": "v5.0",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": site.split("://", 1)[-1],
    }
    factory = client_factory or _CLIENT_FACTORY
    try:
        async with factory(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(f"{base}{path}", params=query, headers=headers)
    except Exception as exc:
        failure = _classify(exc, host)
        logger.warning("ghost_content_call_failed code=%s host=%s err=%s", failure.code, host, failure.detail)
        raise failure from exc
    if resp.status_code != 200:
        raise GhostUnavailable("ghost_http_error", host, f"status={resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise GhostUnavailable("ghost_bad_response", host, "non-json body") from exc


async def fetch_published_posts(
    *,
    limit: int = 20,
    page: int = 1,
    tag: str = "",
    client_factory: Any = None,
) -> dict[str, Any]:
    """Published posts, newest first, optionally scoped to one Ghost tag."""
    data = await _call(
        "/ghost/api/content/posts/",
        {
            "limit": min(max(limit, 1), MAX_LIMIT),
            "page": max(page, 1),
            "include": "tags,authors",
            "formats": "html",
            "filter": build_filter(tag),
        },
        client_factory=client_factory,
    )
    posts = [normalize_post(p) for p in (data.get("posts") or []) if isinstance(p, dict)]
    pagination = (data.get("meta") or {}).get("pagination", {})
    return {"posts": posts, "meta": {"source": "ghost", "pagination": pagination}}


async def fetch_post_by_slug(slug: str, *, client_factory: Any = None) -> dict[str, Any] | None:
    """One published post by slug, or None when Ghost has no such post."""
    data = await _call(
        f"/ghost/api/content/posts/slug/{slug}/",
        {"include": "tags,authors", "formats": "html"},
        client_factory=client_factory,
    )
    posts = data.get("posts") or []
    return normalize_post(posts[0]) if posts else None
