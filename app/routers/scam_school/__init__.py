"""
Scam School API — Interactive Crypto Education Platform
=========================================================
Backend for the RMI Scam School: courses, lessons, progress tracking,
gamification (XP, levels, badges, streaks), certificates, and quizzes.

All data stored in Redis + Supabase backup.
"""

from fastapi import APIRouter

from app.core.logging import get_logger
from app.routers.scam_school.routes.courses import courses_router
from app.routers.scam_school.routes.progress import progress_router

logger = get_logger("scam_school")

router = APIRouter(tags=["scam-school"])
router.include_router(courses_router)
router.include_router(progress_router)
