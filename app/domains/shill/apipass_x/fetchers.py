"""X/Twitter data fetching via the ApiPass task API (create → poll)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.shill.apipass_x.formatters import _extract_tweets, normalize_profile
from app.domains.shill.apipass_x.helpers import (
    _MODEL_USER_BY_SCREEN_NAME,
    APIPASS_BASE_URL,
    APIPASS_PROFILE_CACHE_TTL,
    APIPASS_SUSPEND_CACHE_TTL,
    APIPASS_TWITTER_MODEL,
    CREATE_TIMEOUT_S,
    POLL_INTERVAL_S,
    POLL_TIMEOUT_S,
    POLL_TOTAL_S,
    _auth_headers,
    _cache_read,
    _cache_write,
    _meter_usage,
    _note_task_created,
    _resolve_api_key,
    _sleep,
    _task_create_allowed,
    logger,
)

_TERMINAL_STATES: frozenset[str] = frozenset({"success", "succeeded", "completed", "complete", "done", "finished"})
_FAIL_STATES: frozenset[str] = frozenset({"failed", "failure", "error", "canceled", "cancelled"})


def _build_input(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Input body for createTask — the endpoint's own params, top-level."""
    del endpoint
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    return clean


async def create_task(model: str, input_body: dict[str, Any]) -> str | None:
    """Create one ApiPass task; returns ``taskId`` or None (fail-open)."""
    if not _resolve_api_key():
        logger.debug("apipass_x:create_skipped_no_key")
        return None
    if not _task_create_allowed():
        return None
    payload = {"model": model, "input": input_body}
    try:
        async with httpx.AsyncClient(timeout=CREATE_TIMEOUT_S) as client:
            r = await client.post(
                f"{APIPASS_BASE_URL}/api/v1/jobs/createTask",
                json=payload,
                headers=_auth_headers(),
            )
            if r.status_code != 200:
                logger.warning("apipass_x:create_http status=%s err=%s", r.status_code, r.text[:160])
                return None
            body = r.json() or {}
            data = body.get("data") or {}
            task_id = str(data.get("taskId") or "").strip()
            if not task_id:
                logger.warning("apipass_x:create_no_task_id body=%s", str(body)[:200])
                return None
            _note_task_created()
            _meter_usage(endpoint=model, task_id=task_id)
            logger.info("apipass_x:task_created model=%s task=%s", model, task_id)
            return task_id
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.warning("apipass_x:create_failed err=%s", str(exc)[:160])
        return None


async def get_task(task_id: str) -> dict[str, Any] | None:
    """Poll one task record; returns the ``data`` dict or None (fail-open)."""
    if not task_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S) as client:
            r = await client.get(
                f"{APIPASS_BASE_URL}/api/v1/jobs/recordInfo",
                params={"taskId": task_id},
                headers=_auth_headers(),
            )
            if r.status_code != 200:
                logger.debug("apipass_x:poll_http status=%s task=%s", r.status_code, task_id)
                return None
            body = r.json() or {}
            return body.get("data") if isinstance(body.get("data"), dict) else None
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.debug("apipass_x:poll_failed task=%s err=%s", task_id, str(exc)[:120])
        return None


async def run_task(
    model: str, input_body: dict[str, Any], *, total_timeout: float | None = None
) -> dict[str, Any] | None:
    """Create + poll until terminal; returns ``resultJson`` dict or None."""
    task_id = await create_task(model, input_body)
    if not task_id:
        return None
    budget_s = float(total_timeout or POLL_TOTAL_S)
    deadline = time.monotonic() + budget_s
    while True:
        await _sleep(POLL_INTERVAL_S)
        record = await get_task(task_id)
        if record is None:
            if time.monotonic() >= deadline:
                logger.warning("apipass_x:task_timeout task=%s", task_id)
                return None
            continue
        state = str(record.get("state") or "").strip().lower()
        if state in _FAIL_STATES:
            logger.warning(
                "apipass_x:task_failed task=%s code=%s msg=%s",
                task_id,
                record.get("failCode") or "",
                str(record.get("failMsg") or "")[:200],
            )
            return None
        if state in _TERMINAL_STATES:
            result = record.get("resultJson")
            return result if isinstance(result, dict) else None
        if time.monotonic() >= deadline:
            logger.warning("apipass_x:task_timeout task=%s state=%s", task_id, state)
            return None


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC FETCHERS
# ══════════════════════════════════════════════════════════════════════════


async def fetch_x_profile(handle: str, *, force: bool = False) -> dict[str, Any] | None:
    """RICH pull — one ApiPass createTask returns the full X profile + metrics.

    Served from the persistent cache first so a handle is pulled once and
    reused (repeat calls cost ZERO credits). ``force`` bypasses the cache.
    Fail-open: no key / rate limited / API error / malformed payload → None.
    """
    import asyncio

    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    if not _resolve_api_key():
        logger.debug("apipass_x:profile_skipped_no_key")
        return None
    if not force:
        cached = await asyncio.to_thread(_cache_read, handle)
        if cached is not None:
            logger.debug("apipass_x:profile_cache_hit handle=%s", handle)
            return dict(cached)
    try:
        result = await run_task(_MODEL_USER_BY_SCREEN_NAME, {"variables": {"screen_name": handle}})
    except Exception as exc:  # best-effort fetch, fail-open
        logger.debug("apipass_x:profile_fetch_failed handle=%s err=%s", handle, str(exc)[:120])
        return None
    profile = normalize_profile(result)
    if profile is None:
        logger.warning("apipass_x:profile_no_node handle=%s", handle)
        return None
    ttl = APIPASS_SUSPEND_CACHE_TTL if not profile.get("available") else APIPASS_PROFILE_CACHE_TTL
    await asyncio.to_thread(_cache_write, handle, profile, ttl)
    return profile


async def _resolve_user_id(handle: str) -> str | None:
    """Resolve an X screen name → numeric user id, reusing the cached profile."""
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    try:
        profile = await fetch_x_profile(handle)
    except Exception as exc:  # resolution is best-effort
        logger.debug("apipass_x:resolve_user_id_failed handle=%s err=%s", handle, str(exc)[:120])
        return None
    if profile and profile.get("available") and profile.get("user_id"):
        return str(profile["user_id"])
    return None


async def fetch_tweet_detail(tweet_id: str) -> dict[str, Any] | None:
    """One real tweet by id (the ApiPass 'tweet-detail' endpoint)."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return None
    input_body = _build_input("twitter/simple/tweet-detail", {"tweet_id": tweet_id})
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/tweet-detail", input_body)
    tweets = _extract_tweets(result)
    return tweets[0] if tweets else None


async def fetch_user_tweets(handle: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent real tweets from one X account ('user-tweets' endpoint)."""
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return []
    user_id = await _resolve_user_id(handle)
    if not user_id:
        return []
    input_body = _build_input("twitter/simple/user-tweets", {"user_id": user_id, "count": max(1, min(int(limit), 50))})
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/user-tweets", input_body)
    return _extract_tweets(result)[: max(1, min(int(limit), 50))]


async def search_tweets(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """X search results for *query* ('search' endpoint), newest-ish first."""
    query = str(query or "").strip()
    if not query:
        return []
    input_body = _build_input("twitter/simple/search", {"q": query, "count": max(1, min(int(limit) * 2, 50))})
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/search", input_body)
    tweets = _extract_tweets(result)
    tweets.sort(
        key=lambda t: int(t.get("like_count") or 0) * 2 + int(t.get("retweet_count") or 0),
        reverse=True,
    )
    return tweets[: max(1, min(int(limit), 50))]


__all__ = [
    "create_task",
    "fetch_tweet_detail",
    "fetch_user_tweets",
    "fetch_x_profile",
    "get_task",
    "run_task",
    "search_tweets",
]
