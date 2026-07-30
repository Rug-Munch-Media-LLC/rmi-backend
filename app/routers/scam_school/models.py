"""
Scam School — Pydantic request/response models.
"""

from __future__ import annotations

from pydantic import BaseModel


class LessonProgress(BaseModel):
    lesson_id: str
    course_id: str
    completed: bool = False
    score: int | None = None
    xp_earned: int = 0
    time_spent_seconds: int = 0
    completed_at: str | None = None
    attempts: int = 0


class CourseProgress(BaseModel):
    course_id: str
    completed_lessons: list[str] = []
    total_lessons: int
    exam_score: int | None = None
    exam_passed: bool = False
    started_at: str
    completed_at: str | None = None


class UserProgress(BaseModel):
    user_id: str
    total_xp: int = 0
    level: int = 1
    level_title: str = "Novice"
    streak_days: int = 0
    longest_streak: int = 0
    last_activity: str | None = None
    courses_progress: dict[str, CourseProgress] = {}
    lessons_progress: dict[str, LessonProgress] = {}
    badges: list[str] = []
    achievements: list[str] = []
    certificates: list[dict] = []
    time_spent_total_minutes: int = 0


class QuizAnswer(BaseModel):
    question_index: int
    answer: int


class ExamSubmit(BaseModel):
    answers: list[QuizAnswer]
    time_spent_seconds: int = 0


class LessonComplete(BaseModel):
    lesson_id: str
    course_id: str
    score: int | None = None
    time_spent_seconds: int = 0
    answers: list[QuizAnswer] | None = None
