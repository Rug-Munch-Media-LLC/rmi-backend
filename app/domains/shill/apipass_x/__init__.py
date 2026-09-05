"""ApiPass X (Twitter) data source — real tweet intel at ~0.5 credit/call.

This is the backend form of the bot's ``rugmunchbot.tools.apipass_x`` client,
moved here so shill/KOL/call intel is served from ONE backend service (the
bot + frontend consume it via ``/api/v1/shill/*``).

Split into sub-modules (this package):
* ``helpers``   — config, rate limiter, key resolution, cache, utilities
* ``fetchers``  — ApiPass task API + X/Twitter data fetching
* ``analyzers`` — semantic dedup + relevance reranking
* ``formatters``— payload normalization (tweets, profiles)

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

from app.domains.shill.apipass_x.analyzers import (
    rerank_tweets,
    reset_embed_availability,
    semantic_dedup,
)
from app.domains.shill.apipass_x.fetchers import (
    create_task,
    fetch_tweet_detail,
    fetch_user_tweets,
    fetch_x_profile,
    get_task,
    run_task,
    search_tweets,
)
from app.domains.shill.apipass_x.formatters import normalize_profile, normalize_tweet
from app.domains.shill.apipass_x.helpers import (
    APIPASS_BASE_URL,
    APIPASS_CACHE_DIR,
    APIPASS_TWITTER_MODEL,
    CREDITS_PER_CALL,
    DEDUP_COSINE_THRESHOLD,
    FREE_CREDITS,
    MAX_CALLS_PER_HOUR,
    MODEL_CATALOG,
    api_key_configured,
    task_creates_remaining,
)

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
