"""Public media router — serves image assets referenced by feed payloads.

Feed posts (shill evidence screenshots, feed thumbnails) reference images by
a server-side ``image_path`` that is NOT publicly reachable. This router
exposes them as safe public URLs:

  GET /api/v1/media/image/{name}  → the image bytes (tokenless, rate-limited)

Security contract:
  * ``name`` is a **basename** — path separators are rejected outright, so
    no traversal is possible regardless of encoding tricks.
  * Files are resolved ONLY inside the whitelisted roots below (env
    ``RMI_MEDIA_IMAGE_DIRS``, comma-separated; defaults cover the shill
    screenshot dirs the bot + backend write to).
  * Only image extensions are served; content type is derived from the
    extension, never from client input.
  * Tokenless but rate-limited (same ``get_rate_limit_dependency`` pattern
    as the other public shill GETs) + long-lived cache headers.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency
from app.utils.media_urls import public_image_url  # noqa: F401 — re-exported for callers

logger = get_logger("media_router")

router = APIRouter(prefix="/api/v1/media", tags=["media"])

#: Whitelisted image roots (first match wins). Mirrors the bot's
#: ``SHILL_SCREENSHOT_DIR`` default and the fleet data dir.
_DEFAULT_IMAGE_DIRS = "/path/to/shill_shots,/path/to/feed_images"
_IMAGE_DIRS: tuple[Path, ...] = tuple(
    Path(p.strip()) for p in os.getenv("RMI_MEDIA_IMAGE_DIRS", _DEFAULT_IMAGE_DIRS).split(",") if p.strip()
)

#: Basenames only — anything containing a separator or dotfile escape fails.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

#: Extension → content type (only these are served).
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}

#: Public URL prefix for derived image_url fields (kept in one place so the
#: evidence normalizer and this router never drift).
PUBLIC_IMAGE_URL_PREFIX = "/api/v1/media/image"


def _safe_name(name: str) -> bool:
    return bool(_SAFE_NAME_RE.match(name))


def _resolve(name: str) -> Path | None:
    """Resolve *name* to an existing image file inside a whitelisted root."""
    if not _safe_name(name):
        return None
    suffix = Path(name).suffix.lower()
    if suffix not in _CONTENT_TYPES:
        return None
    for root in _IMAGE_DIRS:
        candidate = root / name
        try:
            if candidate.is_file():
                # Defense in depth: the resolved path must stay under root.
                candidate.resolve().relative_to(root.resolve())
                return candidate
        except (OSError, ValueError):
            continue
    return None


@router.get(
    "/image/{name}",
    summary="Serve a feed image asset by filename",
    dependencies=[Depends(get_rate_limit_dependency("media_img", rate=10.0, burst=30))],
)
async def serve_image(name: str, request: Request) -> FileResponse:
    """Serve an image asset referenced by feed posts' ``image_path``.

    Tokenless but rate-limited; basename-only resolution inside whitelisted
    dirs (see module docstring). 404 (never 500) when the asset is missing.
    """
    path = _resolve(name)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return FileResponse(
        path,
        media_type=_CONTENT_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


# ── Remote thumbnail proxy ────────────────────────────────────────────
# Publishers block hotlinking (403 on referer) or the whole fetch (WAF).
# The site must still render a thumbnail EVERY time (image doctrine), so
# this route fetches the remote image server-side — direct first, then the
# fleet proxy ladder — caches it in Redis, and serves the bytes.
#
#   GET /api/v1/media/thumb?url=<http(s) image url>
#
# Security contract:
#   * ``url`` must be http(s) — data:/file:/ftp: rejected.
#   * SSRF guard: private/loopback/link-local hosts rejected (resolved).
#   * Response content-type must be an image; body capped at 8 MB.
#   * Redis cache 24h (key = sha256(url)) — each unique image fetched once.
#   * Tokenless but rate-limited (same pattern as /image/{name}).

_THUMB_CACHE_TTL = 86400  # 24h
_THUMB_MAX_BYTES = 8 * 1024 * 1024  # 8 MB cap
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


async def _fetch_remote_image(url: str) -> tuple[bytes | None, str | None]:
    """Fetch an image through the egress ladder: direct → residential pool.

    Returns ``(bytes, content_type)`` or ``(None, None)``. Never raises.
    """
    try:
        import httpx

        # 1) Direct with a browser UA (defeats naive referer/UA blocks).
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA, "Accept": "image/*,*/*;q=0.8"},
        ) as client:
            resp = await client.get(url)
            if (
                resp.status_code == 200
                and (resp.headers.get("content-type") or "").startswith("image/")
                and len(resp.content) <= _THUMB_MAX_BYTES
            ):
                return resp.content, resp.headers.get("content-type")
    except Exception as exc:
        logger.debug("thumb direct fetch failed url=%s: %s", url, exc)

    # 2) Rotating residential pool (WEBSHARE_PROXY_LIST, same as the bot).
    try:
        import httpx

        from app.databus.proxy_rotator import get_proxy_rotator

        rotator = get_proxy_rotator()
        for _ in range(3):  # try up to 3 residential IPs
            proxy = rotator.get_rotating_proxy()
            if not proxy:
                break
            try:
                async with httpx.AsyncClient(
                    proxy=proxy,
                    timeout=20,
                    follow_redirects=True,
                    headers={"User-Agent": _BROWSER_UA, "Accept": "image/*,*/*;q=0.8"},
                ) as client:
                    resp = await client.get(url)
                    if (
                        resp.status_code == 200
                        and (resp.headers.get("content-type") or "").startswith("image/")
                        and len(resp.content) <= _THUMB_MAX_BYTES
                    ):
                        return resp.content, resp.headers.get("content-type")
            except Exception as exc:
                logger.debug("thumb proxy fetch failed proxy=%s: %s", proxy, exc)
    except Exception as exc:
        logger.debug("thumb proxy ladder unavailable: %s", exc)

    return None, None


def _ssrf_safe(url: str) -> bool:
    """Reject non-http(s) and private/loopback/link-local targets."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        host = parts.hostname
        # Literal IPs: reject private ranges outright.
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return False
        except ValueError:
            # Hostname: reject obvious internal names (DNS is resolved by
            # httpx; a DNS-rebinding guard is out of scope for thumbnails).
            lowered = host.lower()
            if lowered == "localhost" or lowered.endswith(".local"):
                return False
        return True
    except Exception:
        return False


@router.get(
    "/thumb",
    summary="Proxy a remote article thumbnail (always-image doctrine)",
    dependencies=[Depends(get_rate_limit_dependency("media_thumb", rate=10.0, burst=30))],
)
async def proxy_thumbnail(url: str, request: Request):
    """Fetch a remote image through the egress ladder and serve the bytes.

    Publishers commonly block hotlinking or bot fetches (403). This endpoint
    fetches the image server-side — direct first, then the rotating
    residential pool — and serves it with long-lived cache headers so the
    browser (and the feed SPA) always get a thumbnail. 404 (never 500) when
    the target is not a fetchable image.
    """
    if not _ssrf_safe(url):
        raise HTTPException(status_code=400, detail="invalid thumbnail url")

    cache_key = f"media:thumb:{hashlib.sha256(url.encode()).hexdigest()}"
    try:
        from app.core.redis_client import get_redis

        cached = await get_redis().get(cache_key)
        if cached:
            return Response(
                content=cached,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=86400, immutable"},
            )
    except Exception as exc:
        logger.debug("thumb cache read failed (fail-open): %s", exc)

    data, ctype = await _fetch_remote_image(url)
    if data is None:
        raise HTTPException(status_code=404, detail="thumbnail unavailable")

    try:
        from app.core.redis_client import get_redis

        await get_redis().setex(cache_key, _THUMB_CACHE_TTL, data)
    except Exception as exc:
        logger.debug("thumb cache write failed: %s", exc)

    return Response(
        content=data,
        media_type=ctype or "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
