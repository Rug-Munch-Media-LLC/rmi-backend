"""Article engagement domain — reactions, threaded comments, moderation, sentiment.

One article id space shared by every content surface (news items, legacy news,
curated posts), so a client has a single contract for reacting and commenting.
"""

from __future__ import annotations

from app.domains.engagement.router import router

__all__ = ["router"]
