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

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

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
