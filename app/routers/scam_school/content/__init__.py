"""
Scam School — course content data and gamification configuration.
All data is embedded (no external dependencies).
"""

from __future__ import annotations

import os
from typing import Any

API_BASE = os.getenv("API_BASE", "https://rugmunch.io")

from app.routers.scam_school.content.advanced_forensics import (  # noqa: E402 — imports follow env-based config at module top
    advanced_forensics,
)
from app.routers.scam_school.content.crypto_basics import (  # noqa: E402 — imports follow env-based config at module top
    crypto_basics,
)
from app.routers.scam_school.content.scam_detection import (  # noqa: E402 — imports follow env-based config at module top
    scam_detection,
)

# Explicit annotation: course dicts are heterogeneous (nested modules/slides);
# without it mypy infers the element type as object and every consumer errors.
COURSES: list[dict[str, Any]] = [crypto_basics, scam_detection, advanced_forensics]
