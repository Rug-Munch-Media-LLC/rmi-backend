"""
Intelligence Router - Supabase Helpers
=======================================
Async Supabase REST client for intelligence data storage.
"""

from __future__ import annotations

import json
import os

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def _supabase_is_configured() -> bool:
    """Check if Supabase credentials are configured."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


async def _supabase_request(
    method: str,
    table: str,
    data: dict[str, object] | list[dict[str, object]] | None = None,
    params: dict[str, str] | None = None,
    jwt_token: str | None = None,
) -> list[dict[str, object]]:
    """Make an async request to the Supabase REST API (PostgREST).

    Args:
        method: HTTP method (GET, POST, PATCH)
        table: Supabase table name
        data: JSON body for POST/PATCH
        params: Query parameters for PostgREST filtering (e.g. ``{"id": "eq.123"}``)
        jwt_token: Optional user JWT for user-scoped requests

    Returns:
        List of result rows (empty on error or no results).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    headers: dict[str, str] = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {jwt_token or SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method,
                f"{SUPABASE_URL}/rest/v1/{table}",
                json=data,
                params=params,
                headers=headers,
            )
            if resp.status_code >= 400:
                logger.error(
                    "Supabase API error %d on %s %s: %s",
                    resp.status_code,
                    method,
                    table,
                    resp.text[:300],
                )
                return []
            if resp.status_code == 204:
                return []
            result: list[dict[str, object]] = resp.json()
            return result
        except httpx.RequestError as e:
            logger.error("Supabase request failed (%s %s): %s", method, table, e)
            return []


async def _supabase_get_user(jwt_token: str) -> dict[str, object] | None:
    """Get the authenticated user from Supabase Auth via REST API.

    Args:
        jwt_token: The user's JWT (without ``Bearer `` prefix).

    Returns:
        User dict with ``id``, ``email`` etc., or ``None`` on failure.
    """
    if not SUPABASE_URL or not SUPABASE_KEY or not jwt_token:
        return None

    headers: dict[str, str] = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {jwt_token}",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers=headers,
            )
            if resp.status_code == 200:
                user_result: dict[str, object] = resp.json()
                return user_result
            return None
        except httpx.RequestError as e:
            logger.error("Supabase auth request failed: %s", e)
            return None


async def _get_current_user_id(authorization: str | None = None) -> str | None:
    """Extract the current user ID from a JWT via Supabase Auth.

    Args:
        authorization: Full ``Authorization`` header value (e.g. ``Bearer <token>``).

    Returns:
        User ID string or ``None``.
    """
    if not authorization:
        return None
    try:
        user_data = await _supabase_get_user(authorization.replace("Bearer ", ""))
        if user_data and isinstance(user_data, dict):
            user_id = user_data.get("id")
            return str(user_id) if user_id else None
        return None
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"User auth lookup failed: {e}")
        return None
