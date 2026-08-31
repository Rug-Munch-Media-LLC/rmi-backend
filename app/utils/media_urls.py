"""Public media URL derivation (shared by routers + domain payloads).

Single source of truth for the public image-asset URL shape served by
``app.routers.media`` (``GET /api/v1/media/image/{name}``). Kept in
``app.utils`` so domain modules can derive public URLs without importing
router modules.
"""

from __future__ import annotations

import os
import re

#: Public URL prefix — must match the route in ``app.routers.media``.
PUBLIC_IMAGE_URL_PREFIX = "/api/v1/media/image"

#: Basenames only — separators/dotfile escapes are rejected.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

#: Only image extensions get a public URL.
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def public_image_url(image_path: str | None) -> str:
    """Derive the public URL for a server-side ``image_path`` ("" when unusable).

    Accepts an absolute server path or a bare filename; returns
    ``/api/v1/media/image/<basename>`` when the basename is a servable image
    name, else ``""``.
    """
    if not image_path:
        return ""
    name = os.path.basename(str(image_path).strip())
    if not name or not _SAFE_NAME_RE.match(name):
        return ""
    if os.path.splitext(name)[1].lower() not in _IMAGE_SUFFIXES:
        return ""
    return f"{PUBLIC_IMAGE_URL_PREFIX}/{name}"
