"""Shared FastAPI router for auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
