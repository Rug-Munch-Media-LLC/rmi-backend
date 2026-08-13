"""Scam School image support — pure content-schema tests.

Verifies the additive ``image`` / ``image_alt`` fields added to the
course and lesson content dicts (app/routers/scam_school/content/*.py)
and the optional ``CourseImage`` validation model (models.py).

Pure: imports only the embedded content data — no app startup, no network.
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from app.routers.scam_school.content import COURSES
from app.routers.scam_school.models import CourseImage

ALLOWED_IMAGE_HOSTS = {"commons.wikimedia.org"}


def _course_image_fields(course: dict) -> dict:
    return {"image": course.get("image"), "image_alt": course.get("image_alt")}


def _module_image_fields(module: dict) -> dict:
    return {"image": module.get("image"), "image_alt": module.get("image_alt")}


def _assert_valid_image_url(value: str) -> None:
    assert isinstance(value, str), "image URL must be a string"
    assert value.strip(), "image URL must be non-empty"
    parsed = urlparse(value)
    assert parsed.scheme == "https", f"image URL must use https: {value}"
    assert parsed.netloc in ALLOWED_IMAGE_HOSTS, f"unexpected image host: {value}"
    assert parsed.path, f"image URL must point to a file path: {value}"


@pytest.mark.parametrize("course", COURSES, ids=lambda c: c["id"])
def test_course_content_schema_accepts_image_field(course: dict) -> None:
    """The content schema accepts the image field — with and without it."""
    with_image = CourseImage.model_validate(_course_image_fields(course))
    assert with_image.image is not None or with_image.image_alt is not None

    # Content without image fields still validates (additive schema).
    without_image = CourseImage.model_validate({"image": None, "image_alt": None})
    assert without_image.image is None
    assert without_image.image_alt is None


def test_every_course_has_a_valid_image() -> None:
    """Each course carries a non-empty https image URL with alt text."""
    assert len(COURSES) == 3
    for course in COURSES:
        _assert_valid_image_url(course["image"])
        assert course["image_alt"].strip(), f"course {course['id']} missing image_alt"


def test_lesson_images_are_valid_where_defined() -> None:
    """Lesson (module) images are https and non-empty wherever present."""
    for course in COURSES:
        for module in course["modules"]:
            if "image" in module:
                _assert_valid_image_url(module["image"])
                assert module["image_alt"].strip(), f"module {module['id']} missing image_alt"


def test_image_and_alt_are_paired() -> None:
    """Wherever an image exists, alt text exists too (and vice versa)."""
    for course in COURSES:
        assert ("image" in course) == ("image_alt" in course)
        for module in course["modules"]:
            assert ("image" in module) == ("image_alt" in module)


def test_lesson_image_starter_set_is_populated() -> None:
    """Starter set: ~1 image per course plus a few per course on lessons."""
    course_images = sum(1 for c in COURSES if c.get("image"))
    lesson_images = sum(1 for c in COURSES for m in c["modules"] if m.get("image"))
    assert course_images == len(COURSES), "every course should have a hero image"
    assert lesson_images >= 5, "expected a few lesson images across the courses"
