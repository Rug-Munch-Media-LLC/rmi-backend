"""
Scam School — course content delivery endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.core.redis import get_redis
from app.routers.scam_school.content import COURSES
from app.routers.scam_school.progress import ACHIEVEMENTS, BADGES, LEVELS

courses_router = APIRouter(tags=["scam-school"])


@courses_router.get("/scam-school/courses")
async def list_courses() -> dict[str, Any]:
    """List all available courses with metadata."""
    return {
        "courses": [
            {
                "id": c["id"],
                "title": c["title"],
                "subtitle": c["subtitle"],
                "description": c["description"],
                "difficulty": c["difficulty"],
                "duration_minutes": c["duration_minutes"],
                "lessons_count": c["lessons_count"],
                "xp_reward": c["xp_reward"],
                "badge_reward": c["badge_reward"],
                "color": c["color"],
                "icon": c["icon"],
                "modules": [
                    {
                        "id": m["id"],
                        "title": m["title"],
                        "type": m["type"],
                        "duration": m["duration"],
                        "xp": m["xp"],
                    }
                    for m in c["modules"]
                ],
            }
            for c in COURSES
        ]
    }


@courses_router.get("/scam-school/courses/{course_id}")
async def get_course(course_id: str) -> Any:
    """Get full course content including all lesson slides."""
    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise NotFoundError("Course not found")
    return course


@courses_router.get("/scam-school/courses/{course_id}/modules/{module_id}")
async def get_module(course_id: str, module_id: str) -> Any:
    """Get a specific module's content."""
    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise NotFoundError("Course not found")
    module = next((m for m in course["modules"] if m["id"] == module_id), None)
    if not module:
        raise NotFoundError("Module not found")
    return module


@courses_router.get("/scam-school/badges")
async def list_badges() -> dict[str, Any]:
    """List all available badges."""
    return {"badges": BADGES}


@courses_router.get("/scam-school/achievements")
async def list_achievements() -> dict[str, Any]:
    """List all achievements."""
    return {"achievements": ACHIEVEMENTS}


@courses_router.get("/scam-school/levels")
async def list_levels() -> dict[str, Any]:
    """List level progression."""
    return {"levels": LEVELS}


@courses_router.get("/scam-school/verify-cert/{cert_id}")
async def verify_certificate(cert_id: str) -> dict[str, Any]:
    """Verify a certificate's authenticity."""
    import json

    r = get_redis()
    if r is None:
        raise NotFoundError("Certificate not found")
    all_progress = r.hgetall("rmi:scam_school:progress")

    for user_id, data in all_progress.items():
        try:
            d = json.loads(data)
            for cert in d.get("certificates", []):
                if cert["id"] == cert_id:
                    return {
                        "valid": True,
                        "certificate": cert,
                        "user_id": user_id,
                        "verified_at": datetime.now(UTC).isoformat(),
                    }
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    raise NotFoundError("Certificate not found")
