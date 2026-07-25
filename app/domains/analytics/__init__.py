"""Analytics domain - creator analytics, audience insights, content performance.

Architecture plan:
  models.py    - SQLAlchemy ORM models (CreatorProfile, ContentPost, ViewEvent, EngagementEvent, AudienceFollower)
  service.py   - AnalyticsService (queries, aggregations, reports) [TODO]
  router.py    - FastAPI APIRouter at /api/v1/analytics/* [TODO]

Status: models complete (5 tables). Service and router pending.
"""

from __future__ import annotations

__all__: list[str] = []
