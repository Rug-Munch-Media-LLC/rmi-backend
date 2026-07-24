"""
Scam School — course content data and gamification configuration.
All data is embedded (no external dependencies).
"""

from __future__ import annotations

import os

API_BASE = os.getenv("API_BASE", "https://rugmunch.io")

from app.routers.scam_school.content.advanced_forensics import advanced_forensics
from app.routers.scam_school.content.crypto_basics import crypto_basics
from app.routers.scam_school.content.scam_detection import scam_detection

COURSES = [crypto_basics, scam_detection, advanced_forensics]
