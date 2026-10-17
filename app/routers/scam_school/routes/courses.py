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


def _module_id(module: dict[str, Any], index: int) -> str:
    """Stable id for a course module.

    36 of the authored modules ship without an ``id`` key; the list endpoint
    used to raise KeyError: 'id' and 500 the whole Scam School page. Derive a
    deterministic slug from the title so the id is stable across restarts and
    matches what the detail endpoint can look up.
    """
    explicit = str(module.get("id") or "").strip()
    if explicit:
        return explicit
    title = str(module.get("title") or "").strip().lower()
    slug = "".join(c if c.isalnum() else "-" for c in title).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or f"module-{index + 1}"


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
                "image": c.get("image"),
                "image_alt": c.get("image_alt"),
                "modules": [
                    {
                        "id": _module_id(m, i),
                        "title": m["title"],
                        "type": m["type"],
                        "duration": m["duration"],
                        "xp": m["xp"],
                    }
                    for i, m in enumerate(c["modules"])
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
