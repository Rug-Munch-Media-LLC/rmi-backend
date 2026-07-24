"""
Scam School — user progress, lesson completion, exam submission, and leaderboard endpoints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request

from app.core.errors import AuthError, NotFoundError
from app.core.redis import get_redis
from app.routers.scam_school.content import COURSES
from app.routers.scam_school.models import (
    CourseProgress,
    ExamSubmit,
    LessonComplete,
    LessonProgress,
)
from app.routers.scam_school.progress import (
    ACHIEVEMENTS,
    BADGES,
    _award_badge,
    _award_xp,
    _calculate_level,
    _check_achievements,
    _generate_certificate,
    _get_progress,
    _save_progress,
    _update_streak,
    get_current_user,
)

progress_router = APIRouter(tags=["scam-school"])

@progress_router.get("/scam-school/progress")
async def get_progress(request: Request) -> dict:
    """Get current user's full progress."""
    user = await get_current_user(request)
    if not user:
        raise AuthError("Authentication required")

    progress = _get_progress(user["id"])
    level_data = _calculate_level(progress.total_xp)

    return {
        "user_id": progress.user_id,
        "xp": progress.total_xp,
        "level": level_data,
        "streak": {
            "current": progress.streak_days,
            "longest": progress.longest_streak,
        },
        "badges": [BADGES[b] for b in progress.badges if b in BADGES],
        "achievements": [a for a in ACHIEVEMENTS if a["id"] in progress.achievements],
        "certificates": progress.certificates,
        "courses_progress": progress.courses_progress,
        "time_spent_total_minutes": progress.time_spent_total_minutes,
    }

@progress_router.post("/scam-school/lessons/{course_id}/{lesson_id}/complete")
async def complete_lesson(
    course_id: str, lesson_id: str, req: LessonComplete, request: Request
) -> dict:
    """Mark a lesson as complete and award XP."""
    user = await get_current_user(request)
    if not user:
        raise AuthError("Authentication required")

    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise NotFoundError("Course not found")
    module = next((m for m in course["modules"] if m["id"] == lesson_id), None)
    if not module:
        raise NotFoundError("Lesson not found")

    progress = _get_progress(user["id"])
    _update_streak(progress)

    # Calculate score from quiz answers if provided
    score = req.score or 0
    if req.answers and "content" in module and "slides" in module["content"]:
        quiz_slides = [s for s in module["content"]["slides"] if s.get("type") == "quiz"]
        correct = 0
        for ans in req.answers:
            if ans.question_index < len(quiz_slides):
                quiz = quiz_slides[ans.question_index]
                if quiz.get("correct") == ans.answer:
                    correct += 1
        if quiz_slides:
            score = int((correct / len(quiz_slides)) * 100)

    # Award XP based on score
    base_xp = module.get("xp", 50)
    xp_earned = int(base_xp * (score / 100)) if score > 0 else base_xp

    # Update lesson progress
    lesson_key = f"{course_id}:{lesson_id}"
    progress.lessons_progress[lesson_key] = LessonProgress(
        lesson_id=lesson_id,
        course_id=course_id,
        completed=True,
        score=score,
        xp_earned=xp_earned,
        time_spent_seconds=req.time_spent_seconds,
        completed_at=datetime.now(UTC).isoformat(),
        attempts=1,
    )

    # Update course progress
    if course_id not in progress.courses_progress:
        progress.courses_progress[course_id] = CourseProgress(
            course_id=course_id,
            total_lessons=len(course["modules"]),
            started_at=datetime.now(UTC).isoformat(),
        )

    course_prog = progress.courses_progress[course_id]
    if lesson_id not in course_prog.completed_lessons:
        course_prog.completed_lessons.append(lesson_id)

    # Check if course complete
    if len(course_prog.completed_lessons) >= course_prog.total_lessons:
        course_prog.completed_at = datetime.now(UTC).isoformat()
        # Award course badge
        badge = _award_badge(progress, course.get("badge_reward", ""))
        # Award course XP bonus
        bonus_xp = course.get("xp_reward", 0)
        xp_result = _award_xp(progress, bonus_xp, f"Course complete: {course['title']}")
    else:
        xp_result = _award_xp(progress, xp_earned, f"Lesson complete: {module['title']}")
        badge = None

    progress.time_spent_total_minutes += req.time_spent_seconds // 60

    # Check achievements
    new_achievements = _check_achievements(progress)
    for ach in new_achievements:
        _award_xp(progress, ach["xp"], f"Achievement: {ach['name']}")

    _save_progress(progress)

    return {
        "status": "ok",
        "xp_earned": xp_earned,
        "xp_result": xp_result,
        "score": score,
        "badge_awarded": badge,
        "achievements": new_achievements,
        "level": _calculate_level(progress.total_xp),
        "streak": progress.streak_days,
    }

@progress_router.post("/scam-school/exams/{course_id}/{exam_id}")
async def submit_exam(course_id: str, exam_id: str, req: ExamSubmit, request: Request) -> dict:
    """Submit an exam and get results."""
    user = await get_current_user(request)
    if not user:
        raise AuthError("Authentication required")

    course = next((c for c in COURSES if c["id"] == course_id), None)
    if not course:
        raise NotFoundError("Course not found")
    exam = next((m for m in course["modules"] if m["id"] == exam_id and m["type"] == "exam"), None)
    if not exam:
        raise NotFoundError("Exam not found")

    progress = _get_progress(user["id"])

    # Grade exam
    slides = exam.get("content", {}).get("slides", [])
    quiz_slides = [s for s in slides if s.get("type") == "quiz"]
    correct = 0
    results = []

    for i, ans in enumerate(req.answers):
        if i < len(quiz_slides):
            quiz = quiz_slides[i]
            is_correct = quiz.get("correct") == ans.answer
            if is_correct:
                correct += 1
            results.append(
                {
                    "question_index": i,
                    "correct": is_correct,
                    "correct_answer": quiz.get("correct"),
                    "your_answer": ans.answer,
                    "explanation": quiz.get("explanation", ""),
                }
            )

    total = len(quiz_slides)
    score = int((correct / total) * 100) if total > 0 else 0
    passing = exam.get("passing_score", 80)
    passed = score >= passing

    # Update exam progress
    exam_key = f"{course_id}:{exam_id}"
    progress.lessons_progress[exam_key] = LessonProgress(
        lesson_id=exam_id,
        course_id=course_id,
        completed=passed,
        score=score,
        xp_earned=0,
        time_spent_seconds=req.time_spent_seconds,
        completed_at=datetime.now(UTC).isoformat() if passed else None,
        attempts=1,
    )

    if course_id not in progress.courses_progress:
        progress.courses_progress[course_id] = CourseProgress(
            course_id=course_id,
            total_lessons=len(course["modules"]),
            started_at=datetime.now(UTC).isoformat(),
        )

    course_prog = progress.courses_progress[course_id]
    course_prog.exam_score = score
    course_prog.exam_passed = passed

    xp_earned = 0
    badge = None
    certificate = None
    new_achievements = []

    if passed:
        # Award exam XP
        base_xp = exam.get("xp", 100)
        xp_earned = base_xp
        if score == 100:
            xp_earned = int(base_xp * 1.5)  # Bonus for perfect score
            badge = _award_badge(progress, "perfect-score")
            new_achievements.append({"id": "perfect-exam", "name": "Flawless", "xp": 500})

        xp_result = _award_xp(progress, xp_earned, f"Exam passed: {exam['title']}")

        # Award course badge on exam pass
        if not course_prog.completed_at:
            course_prog.completed_at = datetime.now(UTC).isoformat()
            badge_course = _award_badge(progress, course.get("badge_reward", ""))
            if badge_course:
                badge = badge_course
            # Generate certificate
            certificate = _generate_certificate(user["id"], course_id, score)
            progress.certificates.append(certificate)
    else:
        xp_result = None

    # Check achievements
    ach = _check_achievements(progress)
    for a in ach:
        _award_xp(progress, a["xp"], f"Achievement: {a['name']}")
    new_achievements.extend(ach)

    _save_progress(progress)

    return {
        "passed": passed,
        "score": score,
        "passing_score": passing,
        "correct": correct,
        "total": total,
        "results": results,
        "xp_earned": xp_earned,
        "xp_result": xp_result,
        "badge_awarded": badge,
        "certificate": certificate,
        "achievements": new_achievements,
        "level": _calculate_level(progress.total_xp),
    }

@progress_router.get("/scam-school/leaderboard")
async def get_leaderboard(limit: int = Query(50, ge=1, le=100)) -> dict:
    """Get global leaderboard by XP."""
    r = get_redis()
    all_progress = r.hgetall("rmi:scam_school:progress")

    entries = []
    for user_id, data in all_progress.items():
        try:
            d = json.loads(data)
            entries.append(
                {
                    "user_id": user_id,
                    "xp": d.get("total_xp", 0),
                    "level": d.get("level", 1),
                    "level_title": d.get("level_title", "Novice"),
                    "badges_count": len(d.get("badges", [])),
                    "courses_completed": sum(
                        1 for c in d.get("courses_progress", {}).values() if c.get("completed_at")
                    ),
                    "streak": d.get("streak_days", 0),
                }
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    entries.sort(key=lambda x: x["xp"], reverse=True)

    # Add rank
    for i, e in enumerate(entries[:limit]):
        e["rank"] = i + 1

    return {"leaderboard": entries[:limit], "total_learners": len(entries)}
