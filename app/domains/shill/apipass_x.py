"""ApiPass X (Twitter) data source — real tweet intel at ~0.5 credit/call.

This is the backend form of the bot's ``rugmunchbot.tools.apipass_x`` client,
moved here so shill/KOL/call intel is served from ONE backend service (the
bot + frontend consume it via ``/api/v1/shill/*``).

What changed vs. the bot copy
-----------------------------
* ``semantic_dedup`` no longer imports the bot's ``_vendor.rag_pkg``; it
  lazily embeds via the backend ``app.embedding_router`` and degrades to
  exact-text/URL dedup when the embedder is unavailable.
* ``rerank_tweets`` lazily uses ``app.rag.rerank.rerank_results`` and keeps
  input order on any failure (fail-open).
* ``_meter_usage`` only logs (the bot's sqlite usage ledger is not the
  backend's responsibility; the governor is, and it still applies here).

Budget doctrine (hard, enforced here)
-------------------------------------
* ≤ ``RMI_APIPASS_MAX_CALLS_PER_HOUR`` task creations per hour (default 5 —
  a modest governor so a small dollar envelope lasts). Polling is free.
* "FEWER, RICHER pulls": ``fetch_x_profile`` returns the FULL X profile in one
  createTask and is served from a disk-backed cache
  (``RMI_APIPASS_CACHE_DIR``) so a given screen_name is pulled once.
* Fail-open everywhere: no key, rate-limited, API error, malformed payload →
  ``None`` / ``[]``. The key never enters git (env ``APIPASS_API_KEY`` →
  key file ``$RMI_SECRETS_DIR/apipass_api_key`` → gopass — never logged).

ApiPass task flow (OpenAPI-verified 2026-08-29)
-----------------------------------------------
1. ``POST /api/v1/jobs/createTask``  body ``{model, input}``, header
   ``Authorization: Bearer <key>`` → ``{data: {taskId}}``
2. ``GET  /api/v1/jobs/recordInfo?taskId=<id>`` → ``{data: {state,
   resultJson, failMsg, ...}}`` — poll until a terminal state.

The ``model`` field sent to createTask IS the full endpoint path —
``twitter/simple/user-tweets``, ``twitter/simple/tweet-detail``,
``twitter/simple/search``, etc. ``input`` carries that endpoint's params.
There is no ``twitter-api-simple`` model id — it is the brand/category wrapper.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import math
import os
import subprocess
import tempfile
import time
from collections import deque
from typing import Any

import httpx

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


# ══════════════════════════════════════════════════════════════════════════
# TASK API (create → poll)
# ══════════════════════════════════════════════════════════════════════════


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


_TERMINAL_STATES: frozenset[str] = frozenset({"success", "succeeded", "completed", "complete", "done", "finished"})
_FAIL_STATES: frozenset[str] = frozenset({"failed", "failure", "error", "canceled", "cancelled"})


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


async def _sleep(seconds: float) -> None:
    """Sleep without importing asyncio at module load."""
    import asyncio

    await asyncio.sleep(seconds)


# ══════════════════════════════════════════════════════════════════════════
# NORMALIZATION — hostile/messy provider payloads → one clean tweet shape
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


def normalize_tweet(raw: Any) -> dict[str, Any] | None:
    """Normalize one raw tweet payload into the shared tweet shape."""
    if not isinstance(raw, dict):
        return None
    nested = raw.get("tweet")
    tweet: dict[str, Any] = nested if isinstance(nested, dict) else raw
    if not isinstance(tweet, dict):
        return None
    tweet_id = str(_first(tweet, "tweet_id", "id", "id_str", "rest_id", default="") or "").strip()
    text = str(_first(tweet, "text", "full_text", "tweet_text", "content", default="") or "").strip()
    if not text or not tweet_id:
        return None

    author = _as_dict(tweet.get("author"))
    user = _as_dict(tweet.get("user"))
    core_user = None
    core = tweet.get("core")
    if isinstance(core, dict):
        _ur = core.get("user_results")
        if isinstance(_ur, dict):
            core_user = _ur.get("result") if isinstance(_ur.get("result"), dict) else None
    legacy = None
    if isinstance(core_user, dict) and isinstance(core_user.get("legacy"), dict):
        legacy = core_user["legacy"]
    handle = (
        str(
            _first(author, "username", "screen_name", "handle", default="")
            or _first(user, "username", "screen_name", "handle", default="")
            or _first(tweet, "username", "screen_name", "handle", default="")
            or (str(legacy.get("screen_name") or "") if legacy else "")
        )
        .strip()
        .lstrip("@")
    )
    author_name = (
        str(
            _first(author, "name", "display_name", "full_name", default="")
            or _first(user, "name", "display_name", default="")
            or _first(tweet, "name", "display_name", default="")
            or (str(legacy.get("name") or "") if legacy else "")
        ).strip()
        or handle
    )

    created = str(_first(tweet, "created_at", "createdAt", "timestamp", default="") or "").strip()
    metrics = tweet.get("engagement") if isinstance(tweet.get("engagement"), dict) else {}
    if not metrics and isinstance(tweet.get("public_metrics"), dict):
        metrics = tweet["public_metrics"]
    if not metrics and isinstance(tweet.get("metrics"), dict):
        metrics = tweet["metrics"]
    if not metrics:
        metrics = {
            "retweet_count": _first(tweet, "retweet_count", "retweets", default=0),
            "like_count": _first(tweet, "like_count", "likes", "favorite_count", default=0),
            "reply_count": _first(tweet, "reply_count", "replies", default=0),
            "quote_count": _first(tweet, "quote_count", "quotes", default=0),
        }

    return {
        "tweet_id": tweet_id,
        "text": text[:500],
        "handle": handle,
        "author_name": author_name,
        "created_at": created,
        "retweet_count": _as_int(_first(metrics, "retweet_count", "retweets", "retweetCount")),
        "like_count": _as_int(_first(metrics, "like_count", "likes", "likeCount", "favorite_count")),
        "reply_count": _as_int(_first(metrics, "reply_count", "replies", "replyCount")),
        "quote_count": _as_int(_first(metrics, "quote_count", "quotes")),
        "url": _tweet_url(handle, tweet_id),
        "source": "apipass_x",
    }


def _extract_tweets(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull every tweet-shaped dict out of a provider resultJson (tolerant)."""
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            norm = normalize_tweet(value)
            if norm is not None:
                tid = norm["tweet_id"]
                if tid not in seen:
                    seen.add(tid)
                    out.append(norm)
                return
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)

    _walk(payload)
    return out


# ══════════════════════════════════════════════════════════════════════════
# EMBED (lazy backend embedder) → SEMANTIC DEDUP (URL/identity fallback)
# ══════════════════════════════════════════════════════════════════════════

_recent_vectors: deque[tuple[str, list[float]]] = deque(maxlen=DEDUP_MAX_VECTORS)
_embed_available: bool | None = None
_embed_retry_after: float = 0.0
_EMBED_RETRY_SECONDS: float = float(os.getenv("APIPASS_EMBED_RETRY_SECONDS", "300"))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _remember_vector(text: str, vector: list[float]) -> None:
    global _embed_available
    _recent_vectors.append((text, vector))
    _embed_available = True


def reset_embed_availability() -> None:
    """Force re-probe of the embedder (tests / after an outage)."""
    global _embed_available, _embed_retry_after
    _embed_available = None
    _embed_retry_after = 0.0


def _semantic_is_duplicate(text: str, vector: list[float]) -> bool:
    for stored_text, stored_vector in _recent_vectors:
        if stored_text == text:
            return True
        if _cosine(vector, stored_vector) >= DEDUP_COSINE_THRESHOLD:
            return True
    return False


async def _maybe_embed(text: str) -> list[float] | None:
    """Embed *text* via the backend embedder; None when unavailable.

    Lazy import so the ApiPass client stays import-safe even when the
    embedding stack is down. The bot copy embedded via ``qwen3-4B``; here we
    route through ``app.embedding_router.embed_single`` (the backend's
    canonical embedder), which degrades to None on any failure — the caller
    then falls back to identity dedup.
    """
    try:
        from app.embedding_router import embed_single

        _vec, _meta = await embed_single(text)
        if isinstance(_vec, list) and _vec and isinstance(_vec[0], (int, float)):
            return [float(x) for x in _vec]
    except Exception as exc:  # fail-open by contract
        logger.debug("apipass_x:embed_failed err=%s", str(exc)[:120])
    return None


async def semantic_dedup(text: str, *, key: str = "") -> bool:
    """True when *text* is a semantic near-duplicate of a recent item.

    Degrades to identity (URL key) dedup when the embedder is unavailable —
    dedup degrades, never blocks. Re-probed on a cooldown after an outage.
    """
    global _embed_available, _embed_retry_after
    _ = key  # caller may pass a URL key for the identity fallback
    if _embed_available is False and time.monotonic() < _embed_retry_after:
        return False
    vector = await _maybe_embed(text)
    if not vector:
        if _embed_available is not False:
            logger.warning(
                "apipass_x:embed_unavailable semantic_dedup_disabled retry_seconds=%s",
                _EMBED_RETRY_SECONDS,
            )
        _embed_available = False
        _embed_retry_after = time.monotonic() + _EMBED_RETRY_SECONDS
        return False
    _embed_available = True
    if _semantic_is_duplicate(text, vector):
        return True
    _remember_vector(text, vector)
    return False


# ══════════════════════════════════════════════════════════════════════════
# RERANK (lazy backend reranker; fail-open keeps input order)
# ══════════════════════════════════════════════════════════════════════════


async def rerank_tweets(tweets: list[dict[str, Any]], query: str, top_n: int = 5) -> list[dict[str, Any]]:
    """Rank *tweets* by relevance to *query* via the backend reranker.

    Fail-open: reranker unavailable/empty → input order unchanged. Returns up
    to ``top_n`` tweets.
    """
    if not tweets:
        return []
    documents = [str(t.get("text") or "")[:500] for t in tweets]
    try:
        from app.rag.rerank import rerank_results

        ranked = await rerank_results(query, documents, top_n=min(top_n, len(documents)), tier="live")
    except Exception as exc:  # fail-open by contract
        logger.debug("apipass_x:rerank_failed err=%s", str(exc)[:120])
        return tweets[:top_n]
    if not ranked:
        return tweets[:top_n]
    ordered: list[dict[str, Any]] = []
    for item in sorted(ranked, key=lambda r: float(r.get("relevance_score") or 0.0), reverse=True):
        idx = int(item.get("index") or 0)
        if 0 <= idx < len(tweets):
            ordered.append(tweets[idx])
    return ordered[:top_n] or tweets[:top_n]


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC FETCHERS
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


def _find_user_node(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    def _candidate(v: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6 or not isinstance(v, dict):
            return None
        if any(k in v for k in ("legacy", "core", "rest_id", "id_str")):
            return v
        if v.get("__typename") == "UserUnavailable" or "reason" in v or "message" in v:
            return v
        for key in ("result", "user", "data", "variables"):
            inner = v.get(key)
            if isinstance(inner, dict):
                found = _candidate(inner, depth + 1)
                if found:
                    return found
        for val in v.values():
            if isinstance(val, dict):
                found = _candidate(val, depth + 1)
                if found:
                    return found
        return None

    return _candidate(payload)


def normalize_profile(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a ``user-by-screen-name`` resultJson into the rich X profile."""
    node = _find_user_node(payload)
    if not isinstance(node, dict):
        return None
    if node.get("__typename") == "UserUnavailable" or "reason" in node or "message" in node:
        return {
            "available": False,
            "reason": str(node.get("reason") or node.get("__typename") or ""),
            "message": str(node.get("message") or ""),
            "__typename": str(node.get("__typename") or ""),
        }
    core = _as_dict(node.get("core"))
    avatar = _as_dict(node.get("avatar"))
    loc = _as_dict(node.get("location"))
    ver = _as_dict(node.get("verification"))
    leg = _as_dict(node.get("legacy"))
    handle = str(core.get("screen_name") or "").strip().lstrip("@")
    if not handle:
        handle = str(node.get("screen_name") or node.get("username") or "").strip().lstrip("@")
    rest_id = _decode_user_id(node.get("rest_id") or node.get("id") or "")
    vinfo = _as_dict(node.get("verification_info"))
    vreason = _as_dict(vinfo.get("reason"))
    vdesc = _as_dict(vreason.get("description"))
    pinned = [str(x) for x in (leg.get("pinned_tweet_ids_str") or []) if x]
    return {
        "available": True,
        "handle": handle,
        "user_id": rest_id,
        "name": str(core.get("name") or handle),
        "screen_name": str(core.get("screen_name") or handle),
        "created_at": str(core.get("created_at") or ""),
        "avatar_url": str(avatar.get("image_url") or ""),
        "banner_url": str(leg.get("profile_banner_url") or ""),
        "bio": str(leg.get("description") or ""),
        "location": str(loc.get("location") or ""),
        "website": str(leg.get("url") or ""),
        "verified": bool(ver.get("verified") or False),
        "is_blue_verified": bool(node.get("is_blue_verified") or False),
        "verification_reason": str(vdesc.get("text") or ""),
        "metrics": {
            "followers": _as_int(leg.get("followers_count")),
            "following": _as_int(leg.get("friends_count")),
            "statuses": _as_int(leg.get("statuses_count")),
            "likes": _as_int(leg.get("favourites_count")),
            "listed": _as_int(leg.get("listed_count")),
            "media": _as_int(leg.get("media_count")),
            "fast_followers": _as_int(leg.get("fast_followers_count")),
            "normal_followers": _as_int(leg.get("normal_followers_count")),
        },
        "pinned_tweet_ids": pinned,
        "pinned_url": _tweet_url(handle, pinned[0]) if pinned else "",
        "url": f"https://x.com/{handle}",
        "source": "apipass_x",
    }


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
    "APIPASS_BASE_URL",
    "APIPASS_CACHE_DIR",
    "APIPASS_TWITTER_MODEL",
    "CREDITS_PER_CALL",
    "DEDUP_COSINE_THRESHOLD",
    "FREE_CREDITS",
    "MAX_CALLS_PER_HOUR",
    "MODEL_CATALOG",
    "api_key_configured",
    "create_task",
    "fetch_tweet_detail",
    "fetch_user_tweets",
    "fetch_x_profile",
    "get_task",
    "normalize_profile",
    "normalize_tweet",
    "rerank_tweets",
    "reset_embed_availability",
    "run_task",
    "search_tweets",
    "semantic_dedup",
    "task_creates_remaining",
]
