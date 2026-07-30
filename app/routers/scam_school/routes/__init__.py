"""
Scam School Routes
==================
API route handlers for the Scam School educational platform:
course content delivery, user progress tracking, and leaderboards.
"""

from __future__ import annotations

from app.routers.scam_school.routes.courses import courses_router
from app.routers.scam_school.routes.progress import progress_router

__all__ = [
    "courses_router",
    "progress_router",
]
