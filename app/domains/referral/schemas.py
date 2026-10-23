"""Request/response models for referral attribution endpoints."""

from __future__ import annotations

import uuid  # noqa: TC003 — Pydantic resolves annotations at runtime

from pydantic import BaseModel, Field


class ClickRequest(BaseModel):
    """Attribution payload. Identifiers are optional and always hashed."""

    partner: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    surface: str = Field(min_length=1, max_length=32)
    tier: str | None = Field(default=None, max_length=32)
    user_id: str | None = Field(default=None, max_length=256)
    chat_id: str | None = Field(default=None, max_length=256)


class ClickResponse(BaseModel):
    click_id: uuid.UUID
    partner: str
    target_url: str


class StatsBucket(BaseModel):
    partner: str
    code: str
    surface: str
    tier: str
    clicks: int
