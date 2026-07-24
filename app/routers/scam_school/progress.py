"""
Scam School — progress tracking: XP, levels, badges, streaks, achievements,
certificates, and Redis persistence helpers.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.routers.scam_school.content import API_BASE, COURSES
from app.routers.scam_school.models import UserProgress

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger("scam_school")

# ── Gamification Config ──

LEVELS = [
    {"level": 1, "title": "Novice", "xp_required": 0, "color": "#9ca3af"},
    {"level": 2, "title": "Learner", "xp_required": 300, "color": "#6366f1"},
    {"level": 3, "title": "Analyst", "xp_required": 800, "color": "#8b5cf6"},
    {"level": 4, "title": "Detective", "xp_required": 1500, "color": "#ec4899"},
    {"level": 5, "title": "Hunter", "xp_required": 2500, "color": "#f59e0b"},
    {"level": 6, "title": "Expert", "xp_required": 4000, "color": "#ef4444"},
    {"level": 7, "title": "Master", "xp_required": 6000, "color": "#10b981"},
    {"level": 8, "title": "Legend", "xp_required": 9000, "color": "#3b82f6"},
    {"level": 9, "title": "Oracle", "xp_required": 13000, "color": "#fbbf24"},
    {"level": 10, "title": "Sentinel", "xp_required": 18000, "color": "#dc2626"},
]

BADGES = {
    "crypto-cadet": {
        "name": "Crypto Cadet",
        "description": "Completed Crypto Basics",
        "icon": "shield",
        "color": "#6366f1",
        "rarity": "common",
    },
    "scam-hunter": {
        "name": "Scam Hunter",
        "description": "Completed Scam Detection Masterclass",
        "icon": "crosshair",
        "color": "#ef4444",
        "rarity": "rare",
    },
    "forensic-analyst": {
        "name": "Forensic Analyst",
        "description": "Completed Advanced Forensics",
        "icon": "microscope",
        "color": "#f59e0b",
        "rarity": "epic",
    },
    "perfect-score": {
        "name": "Perfect Score",
        "description": "100% on any exam",
        "icon": "star",
        "color": "#fbbf24",
        "rarity": "legendary",
    },
    "speed-learner": {
        "name": "Speed Learner",
        "description": "Completed 3 lessons in one day",
        "icon": "zap",
        "color": "#10b981",
        "rarity": "rare",
    },
    "streak-master": {
        "name": "Streak Master",
        "description": "7-day learning streak",
        "icon": "flame",
        "color": "#f97316",
        "rarity": "epic",
    },
    "community-guardian": {
        "name": "Community Guardian",
        "description": "Reported 5 verified scams",
        "icon": "users",
        "color": "#3b82f6",
        "rarity": "legendary",
    },
    "first-blood": {
        "name": "First Blood",
        "description": "First lesson completed",
        "icon": "target",
        "color": "#ec4899",
        "rarity": "common",
    },
}

ACHIEVEMENTS = [
    {
        "id": "first-lesson",
        "name": "First Steps",
        "description": "Complete your first lesson",
        "xp": 50,
        "icon": "footprints",
    },
    {
        "id": "first-course",
        "name": "Course Complete",
        "description": "Complete your first course",
        "xp": 200,
        "icon": "graduation-cap",
    },
    {
        "id": "exam-pass",
        "name": "Exam Passed",
        "description": "Pass any exam with 80%+",
        "xp": 150,
        "icon": "check-circle",
    },
    {
        "id": "perfect-exam",
        "name": "Flawless",
        "description": "Get 100% on an exam",
        "xp": 500,
        "icon": "crown",
    },
    {
        "id": "streak-3",
        "name": "On Fire",
        "description": "3-day learning streak",
        "xp": 100,
        "icon": "flame",
    },
    {
        "id": "streak-7",
        "name": "Unstoppable",
        "description": "7-day learning streak",
        "xp": 300,
        "icon": "fire",
    },
    {
        "id": "streak-30",
        "name": "Dedicated",
        "description": "30-day learning streak",
        "xp": 1000,
        "icon": "award",
    },
    {
        "id": "all-courses",
        "name": "Scholar",
        "description": "Complete all courses",
        "xp": 2000,
        "icon": "book-open",
    },
    {
        "id": "help-others",
        "name": "Mentor",
        "description": "Help 3 other learners in community",
        "xp": 200,
        "icon": "heart",
    },
    {
        "id": "report-scam",
        "name": "Whistleblower",
        "description": "Report a verified scam",
        "xp": 300,
        "icon": "megaphone",
    },
]

# ── Auth Helper ──

async def get_current_user(request: Request) -> dict | None:
    """Extract user from JWT."""
    from app.auth import get_current_user as _get_user
    return await _get_user(request)

# ── Redis Persistence ──

def _get_progress(user_id: str) -> UserProgress:
    """Get or create user progress."""
    r = get_redis()
    data = r.hget("rmi:scam_school:progress", user_id)
    if data:
        d = json.loads(data)
        return UserProgress(**d)
    return UserProgress(user_id=user_id, started_at=datetime.now(UTC).isoformat())

def _save_progress(progress: UserProgress) -> None:
    """Save user progress to Redis."""
    r = get_redis()
    r.hset("rmi:scam_school:progress", progress.user_id, progress.model_dump_json())

# ── XP & Level Helpers ──

def _calculate_level(xp: int) -> dict:
    """Calculate level from XP."""
    current_level = LEVELS[0]
    next_level = LEVELS[1] if len(LEVELS) > 1 else None
    for i, level in enumerate(LEVELS):
        if xp >= level["xp_required"]:
            current_level = level
            next_level = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    progress_pct = 0
    if next_level:
        range_size = next_level["xp_required"] - current_level["xp_required"]
        progress_in_range = xp - current_level["xp_required"]
        progress_pct = (
            min(100, int((progress_in_range / range_size) * 100)) if range_size > 0 else 100
        )
    return {
        "level": current_level["level"],
        "title": current_level["title"],
        "color": current_level["color"],
        "xp": xp,
        "xp_to_next": next_level["xp_required"] - xp if next_level else 0,
        "next_level_title": next_level["title"] if next_level else None,
        "progress_pct": progress_pct,
    }

def _award_xp(progress: UserProgress, amount: int, source: str) -> dict:
    """Award XP and check for level ups."""
    old_level = progress.level
    progress.total_xp += amount
    new_level_data = _calculate_level(progress.total_xp)
    progress.level = new_level_data["level"]
    progress.level_title = new_level_data["title"]

    leveled_up = progress.level > old_level
    return {
        "awarded": amount,
        "source": source,
        "new_total": progress.total_xp,
        "leveled_up": leveled_up,
        "old_level": old_level,
        "new_level": progress.level,
        "level_title": progress.level_title,
    }

def _award_badge(progress: UserProgress, badge_id: str) -> dict | None:
    """Award a badge if not already owned."""
    if badge_id in progress.badges:
        return None
    if badge_id not in BADGES:
        return None
    progress.badges.append(badge_id)
    badge = BADGES[badge_id]
    return {
        "badge_id": badge_id,
        "name": badge["name"],
        "description": badge["description"],
        "icon": badge["icon"],
        "color": badge["color"],
        "rarity": badge["rarity"],
    }

# ── Achievement / Streak Helpers ──

def _check_achievements(progress: UserProgress) -> list[dict]:
    """Check and award new achievements."""
    new_achievements = []

    # First lesson
    if "first-lesson" not in progress.achievements and len(progress.lessons_progress) > 0:
        if any(line.completed for line in progress.lessons_progress.values()):
            progress.achievements.append("first-lesson")
            new_achievements.append({"id": "first-lesson", "name": "First Steps", "xp": 50})

    # First course
    if "first-course" not in progress.achievements:
        completed_courses = sum(1 for c in progress.courses_progress.values() if c.completed_at)
        if completed_courses >= 1:
            progress.achievements.append("first-course")
            new_achievements.append({"id": "first-course", "name": "Course Complete", "xp": 200})

    # All courses
    if "all-courses" not in progress.achievements and len(progress.courses_progress) >= len(COURSES):
        all_done = all(c.completed_at for c in progress.courses_progress.values())
        if all_done:
            progress.achievements.append("all-courses")
            new_achievements.append({"id": "all-courses", "name": "Scholar", "xp": 2000})

    # Streak achievements
    if "streak-3" not in progress.achievements and progress.streak_days >= 3:
        progress.achievements.append("streak-3")
        new_achievements.append({"id": "streak-3", "name": "On Fire", "xp": 100})
    if "streak-7" not in progress.achievements and progress.streak_days >= 7:
        progress.achievements.append("streak-7")
        new_achievements.append({"id": "streak-7", "name": "Unstoppable", "xp": 300})
        _award_badge(progress, "streak-master")
    if "streak-30" not in progress.achievements and progress.streak_days >= 30:
        progress.achievements.append("streak-30")
        new_achievements.append({"id": "streak-30", "name": "Dedicated", "xp": 1000})

    return new_achievements

def _update_streak(progress: UserProgress) -> None:
    """Update learning streak."""
    now = datetime.now(UTC)
    if progress.last_activity:
        last = datetime.fromisoformat(progress.last_activity)
        days_diff = (now - last).days
        if days_diff == 0:
            return  # Already active today
        elif days_diff == 1:
            progress.streak_days += 1
        else:
            progress.streak_days = 1  # Reset
        progress.longest_streak = max(progress.longest_streak, progress.streak_days)
    else:
        progress.streak_days = 1
    progress.last_activity = now.isoformat()

# ── Certificate Helper ──

def _generate_certificate(user_id: str, course_id: str, score: int) -> dict:
    """Generate a certificate."""
    cert_id = f"cert-{course_id}-{user_id[:8]}-{secrets.token_hex(4)}"
    course = next((c for c in COURSES if c["id"] == course_id), None)
    return {
        "id": cert_id,
        "course_id": course_id,
        "course_title": course["title"] if course else "Unknown Course",
        "user_id": user_id,
        "score": score,
        "issued_at": datetime.now(UTC).isoformat(),
        "verify_url": f"{API_BASE}/verify-cert/{cert_id}",
        "hash": hashlib.sha256(f"{cert_id}:{user_id}:{course_id}".encode()).hexdigest()[:16],
    }
