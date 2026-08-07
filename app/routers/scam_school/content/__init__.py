"""
Scam School — course content data and gamification configuration.
All data is embedded (no external dependencies).
"""

from __future__ import annotations

import os

API_BASE = os.getenv("API_BASE", "https://rugmunch.io")

from app.routers.scam_school.content.advanced_forensics import advanced_forensics  # noqa: E402 — module docstring precedes import block
from app.routers.scam_school.content.crypto_basics import crypto_basics  # noqa: E402 — module docstring precedes import block
from app.routers.scam_school.content.scam_detection import scam_detection  # noqa: E402 — module docstring precedes import block

COURSES = [crypto_basics, scam_detection, advanced_forensics]
