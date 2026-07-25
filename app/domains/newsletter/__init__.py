"""Newsletter domain - auto-send, crypto payments, cross-platform publishing.

Architecture plan:
  models.py    - SQLAlchemy ORM models (newsletters, subscribers, payments, deliveries, crossposts)
  schemas.py   - Pydantic v2 request/response schemas
  service.py   - NewsletterService (CRUD, auto-send, scheduling, analytics) [TODO]
  publisher.py - CrossPlatformPublisher (Ghost, Mirror, Paragraph, Medium, Substack, Nostr,
                 Farcaster, Bluesky, Mastodon, Email) [stub: publisher skeleton present]
  router.py    - FastAPI APIRouter at /api/v1/newsletter/* [TODO]

Status: models + schemas + publisher skeleton complete. Router and service pending.
"""

from __future__ import annotations

__all__: list[str] = []
