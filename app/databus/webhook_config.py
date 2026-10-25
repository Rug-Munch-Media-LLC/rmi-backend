"""Webhook service configuration and shared Redis/error plumbing.

Extracted from ``app.databus.webhooks`` (2026-10-25) so the receiver, the
verifier and the outbound registration each import one small module instead of
one 349-line god module.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import redis
from redis.exceptions import RedisError

from app.core.logging import get_logger

logger = get_logger("webhooks")

#: Failure surface of a webhook path: network/transport errors must never 5xx
#: the receiver — they degrade to a logged skip.
_WEBHOOK_ERRORS = (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException)
#: Redis errors are not builtin ConnectionError subclasses; always include both.
_REDIS_ERRORS = (RedisError, *_WEBHOOK_ERRORS)

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Webhook configurations per service
WEBHOOK_CONFIGS: dict[str, dict[str, Any]] = {
    "arkham": {
        "secret_env": "ARKHAM_WEBHOOK_SECRET",
        "verify_header": "X-Arkham-Signature",
        "events": ["entity_updated", "transaction_detected", "label_added"],
        "cache_ttl": 3600,
    },
    "helius": {
        "secret_env": "HELIUS_WEBHOOK_SECRET",
        "verify_header": "x-helius-signature",
        "auth_env": "HELIUS_WEBHOOK_SECRET",  # Helius sends authHeader as Bearer token — verify it
        "events": ["transaction", "nft_event", "token_transfer", "account_update"],
        "cache_ttl": 600,
    },
    "moralis": {
        "secret_env": "MORALIS_WEBHOOK_SECRET",
        "verify_header": "x-signature",
        "events": ["txs", "logs", "nft_transfers", "erc20_transfers"],
        "cache_ttl": 900,
    },
    "alchemy": {
        "secret_env": "ALCHEMY_WEBHOOK_SECRET",
        "verify_header": "X-Alchemy-Signature",
        "events": ["address_activity", "nft_activity", "tx_status"],
        "cache_ttl": 600,
    },
}


def _redis() -> Any:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )
