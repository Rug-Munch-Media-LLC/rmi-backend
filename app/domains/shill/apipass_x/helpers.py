"""Shared config, rate limiter, key resolution, cache, and small utilities.

Split out of the former single-file ``apipass_x`` module so fetchers,
analyzers, and formatters share one dependency-free foundation.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from collections import deque
from typing import Any

logger = logging.getLogger("rmi_backend.shill.apipass_x")

# ══════════════════════════════════════════════════════════════════════════
# CONFIG (env-overridable, all optional)
# ══════════════════════════════════════════════════════════════════════════

APIPASS_BASE_URL = os.getenv("APIPASS_BASE_URL", "https://api.apipass.dev").rstrip("/")
APIPASS_TWITTER_MODEL = os.getenv("APIPASS_TWITTER_MODEL", "twitter/simple")
#: Hard budget — max task creations per hour (default 5 — modest governor).
MAX_CALLS_PER_HOUR = int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", "5"))
#: Daily task-creation cap (account limit 50/day) — soft, in-memory.
MAX_CALLS_PER_DAY = int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_DAY", "40"))
#: Free-credit allowance + per-call cost (usage ledger / dashboard).
FREE_CREDITS = float(os.getenv("RMI_APIPASS_FREE_CREDITS", "50"))
CREDITS_PER_CALL = float(os.getenv("RMI_APIPASS_CREDITS_PER_CALL", "0.1"))
#: Per-request timeouts (seconds).
CREATE_TIMEOUT_S = float(os.getenv("APIPASS_CREATE_TIMEOUT", "15"))
POLL_TIMEOUT_S = float(os.getenv("APIPASS_POLL_TIMEOUT", "10"))
#: Poll cadence for a task (task completion is async server-side).
POLL_INTERVAL_S = float(os.getenv("APIPASS_POLL_INTERVAL", "2.5"))
POLL_TOTAL_S = float(os.getenv("APIPASS_POLL_TOTAL", "90"))
#: Semantic-dedup cosine threshold — below this the text is "new".
DEDUP_COSINE_THRESHOLD = float(os.getenv("APIPASS_DEDUP_COSINE", "0.90"))
#: Max recent vectors kept for semantic dedup (bounded memory).
DEDUP_MAX_VECTORS = int(os.getenv("APIPASS_DEDUP_MAX_VECTORS", "200"))

#: Persistent cache for screen_name → rich X profile.
APIPASS_CACHE_DIR = os.getenv(
    "RMI_APIPASS_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "rmi", "apipass_cache"),
)
#: Profile cache TTL (seconds).
APIPASS_PROFILE_CACHE_TTL = float(os.getenv("RMI_APIPASS_PROFILE_CACHE_TTL", "86400"))
#: How long a verbatim "unavailable/suspended" answer is cached.
APIPASS_SUSPEND_CACHE_TTL = float(os.getenv("RMI_APIPASS_SUSPEND_CACHE_TTL", "604800"))

#: Canonical key file for the ApiPass key (never in git).
_KEY_FILE = os.getenv("RMI_APIPASS_KEY_FILE") or os.path.join(
    os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "apipass_api_key"
)
#: Canonical gopass path for the ApiPass key (never in git).
_GOPASS_PATH = "rmi/apipass/api_key"
_ENV_KEYS = ("APIPASS_API_KEY",)

#: The model id actually sent to createTask for the profile lane.
_MODEL_USER_BY_SCREEN_NAME = "twitter/graphql/user-by-screen-name"


# ══════════════════════════════════════════════════════════════════════════
# MODEL CATALOG — what ApiPass X (Twitter) endpoints exist
# ══════════════════════════════════════════════════════════════════════════
MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "twitter/graphql/user-by-screen-name": {
        "input": ["variables.screen_name"],
        "confirmed": True,
        "note": "RICHEST single-pull: full profile + metrics + verification.",
    },
    "twitter/graphql/user-by-rest-id": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW] not re-verified this run
        "note": "Resolve by numeric id (profile, same rich shape).",
    },
    "twitter/graphql/user-tweets": {
        "input": ["variables.user_id", "variables.count"],
        "confirmed": False,  # [REVIEW]
        "note": "User media timeline (latest tweets + media).",
    },
    "twitter/graphql/tweet-detail": {
        "input": ["variables.tweet_id"],
        "confirmed": False,  # [REVIEW]
        "note": "One tweet by id (graphql variant).",
    },
    "twitter/graphql/user-followers": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW]
        "note": "Followers list (user_id required).",
    },
    "twitter/graphql/user-following": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW]
        "note": "Following list (user_id required).",
    },
    "twitter/simple/user-tweets": {
        "input": ["user_id", "count"],
        "confirmed": True,
        "note": "Recent user timeline (needs numeric user_id).",
    },
    "twitter/simple/tweet-detail": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "One tweet by id.",
    },
    "twitter/simple/search": {
        "input": ["q", "count"],
        "confirmed": True,
        "note": "Keyword search.",
    },
    "twitter/simple/quotes": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "Quotes of a tweet.",
    },
    "twitter/simple/retweeters": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "Retweeters of a tweet.",
    },
}


# ══════════════════════════════════════════════════════════════════════════
# RATE LIMITER — ≤ MAX_CALLS_PER_HOUR + ≤ MAX_CALLS_PER_DAY (in-memory window)
# ══════════════════════════════════════════════════════════════════════════
_task_created_at: deque[float] = deque(maxlen=MAX_CALLS_PER_HOUR or 1)
_day_created_at: deque[float] = deque(maxlen=MAX_CALLS_PER_DAY or 1)


def _task_creates_this_hour() -> int:
    now = time.monotonic()
    while _task_created_at and now - _task_created_at[0] >= 3600.0:
        _task_created_at.popleft()
    return len(_task_created_at)


def _task_creates_this_day() -> int:
    now = time.monotonic()
    while _day_created_at and now - _day_created_at[0] >= 86400.0:
        _day_created_at.popleft()
    return len(_day_created_at)


def task_creates_remaining() -> int:
    """Task-creation calls left in the current hour window (0..MAX)."""
    if MAX_CALLS_PER_HOUR <= 0:
        return 0
    return max(0, MAX_CALLS_PER_HOUR - _task_creates_this_hour())


def _task_create_allowed() -> bool:
    if MAX_CALLS_PER_HOUR <= 0:
        logger.debug("apipass_x:disabled_by_budget max_calls_per_hour=0")
        return False
    if _task_creates_this_hour() >= MAX_CALLS_PER_HOUR:
        logger.warning(
            "apipass_x:rate_limited used=%s max=%s",
            _task_creates_this_hour(),
            MAX_CALLS_PER_HOUR,
        )
        return False
    if MAX_CALLS_PER_DAY > 0 and _task_creates_this_day() >= MAX_CALLS_PER_DAY:
        logger.warning(
            "apipass_x:rate_limited_day used=%s max=%s",
            _task_creates_this_day(),
            MAX_CALLS_PER_DAY,
        )
        return False
    return True


def _note_task_created() -> None:
    _task_created_at.append(time.monotonic())
    _day_created_at.append(time.monotonic())


def _meter_usage(endpoint: str = "", task_id: str = "") -> None:
    """Record one task creation in the credit ledger (fail-open, log-only)."""
    logger.debug(
        "apipass_x:meter endpoint=%s model=%s credits=%.2f task=%s",
        endpoint,
        APIPASS_TWITTER_MODEL,
        CREDITS_PER_CALL,
        task_id,
    )


# ══════════════════════════════════════════════════════════════════════════
# KEY RESOLUTION — env → key file → gopass, never logged, never in git
# ══════════════════════════════════════════════════════════════════════════


def _resolve_api_key() -> str:
    """ApiPass key: env ``APIPASS_API_KEY`` → key file → gopass. UNCACHED."""
    for key in _ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    try:
        with open(_KEY_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"_resolve_api_key failed: {e}")
    try:
        value = (
            subprocess.check_output(  # noqa: S603 — gopass is the canonical secret store
                ["gopass", "show", _GOPASS_PATH],  # noqa: S607 — fixed tool, arg is a secret name
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            .decode()
            .strip()
        )
        if value:
            return value
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"_resolve_api_key failed: {e}")
    return ""


def api_key_configured() -> bool:
    """True when an ApiPass key is available (env or gopass)."""
    return bool(_resolve_api_key())


def _auth_headers() -> dict[str, str]:
    key = _resolve_api_key()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


async def _sleep(seconds: float) -> None:
    """Sleep without importing asyncio at module load."""
    import asyncio

    await asyncio.sleep(seconds)


# ══════════════════════════════════════════════════════════════════════════
# SMALL UTILITIES
# ══════════════════════════════════════════════════════════════════════════


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _tweet_url(handle: str, tweet_id: str) -> str:
    h = str(handle or "").strip().lstrip("@")
    if h and tweet_id:
        return f"https://x.com/{h}/status/{tweet_id}"
    return f"https://x.com/i/status/{tweet_id}" if tweet_id else ""


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decode_user_id(value: Any) -> str:
    """Coerce an X user identifier to a plain numeric id string."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.startswith("User:"):
        return s[len("User:") :].strip()
    try:
        decoded = base64.b64decode(s).decode("utf-8")
        if decoded.startswith("User:"):
            return decoded[len("User:") :].strip()
    except ValueError as e:
        logger.warning(f"_decode_user_id failed: {e}")
    return s


# ══════════════════════════════════════════════════════════════════════════
# PROFILE CACHE (disk-backed, screen_name → rich X profile)
# ══════════════════════════════════════════════════════════════════════════

_CACHE_OK = False
_CACHE_PROBED = False


def _cache_path(handle: str) -> str:
    return os.path.join(APIPASS_CACHE_DIR, f"{hashlib.sha256(handle.lower().encode()).hexdigest()[:24]}.json")


def _cache_probe() -> bool:
    global _CACHE_OK, _CACHE_PROBED
    if _CACHE_PROBED:
        return _CACHE_OK
    _CACHE_PROBED = True
    try:
        os.makedirs(APIPASS_CACHE_DIR, exist_ok=True)
        _CACHE_OK = os.path.isdir(APIPASS_CACHE_DIR) and os.access(APIPASS_CACHE_DIR, os.W_OK)
    except OSError:
        _CACHE_OK = False
    return _CACHE_OK


def _cache_read(handle: str) -> dict[str, Any] | None:
    if not _cache_probe():
        return None
    path = _cache_path(handle)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        ttl = float(data.get("ttl") or 0.0)
        fetched = float(data.get("fetched_at_ms") or 0.0)
        if ttl <= 0 or (time.time() * 1000.0 - fetched) > ttl * 1000.0:
            with contextlib.suppress(OSError):
                os.remove(path)
            return None
        return dict(data.get("profile") or {})
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _cache_write(handle: str, profile: dict[str, Any], ttl: float) -> bool:
    if not _cache_probe():
        return False
    try:
        payload = {
            "handle": handle.lower().lstrip("@"),
            "fetched_at_ms": time.time() * 1000.0,
            "ttl": float(ttl),
            "profile": profile,
        }
        with open(_cache_path(handle), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


def _cache_clear(handle: str | None = None) -> int:
    removed = 0
    if not _cache_probe():
        return 0
    try:
        if handle:
            p = _cache_path(handle)
            if os.path.isfile(p):
                os.remove(p)
                removed = 1
        else:
            for name in os.listdir(APIPASS_CACHE_DIR):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(APIPASS_CACHE_DIR, name))
                        removed += 1
                    except OSError as e:
                        logger.warning(f"_cache_clear failed: {e}")
    except OSError as e:
        logger.warning(f"_cache_clear failed: {e}")
    return removed


__all__ = [
    "APIPASS_BASE_URL",
    "APIPASS_CACHE_DIR",
    "APIPASS_PROFILE_CACHE_TTL",
    "APIPASS_SUSPEND_CACHE_TTL",
    "APIPASS_TWITTER_MODEL",
    "CREDITS_PER_CALL",
    "DEDUP_COSINE_THRESHOLD",
    "DEDUP_MAX_VECTORS",
    "FREE_CREDITS",
    "MAX_CALLS_PER_DAY",
    "MAX_CALLS_PER_HOUR",
    "MODEL_CATALOG",
    "api_key_configured",
    "task_creates_remaining",
]
