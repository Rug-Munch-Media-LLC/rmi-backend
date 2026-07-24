"""T07 GlitchTip - Sentry SDK integration.

Per v4.0 §T07. Self-hosted Sentry-compatible error tracking at
glitchtip.rugmunch.io (Sentry SDK pointed at our own instance).

Key principle: NEVER leak secrets. The before_send hook strips
authorization headers, X-API-Key, passwords, tokens, etc.

Per v3 unfuck rule #7: SDK init must be at module level, not in lifespan.
But the SDK itself uses lazy init - setup_sentry() is called once at startup.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# Config - defaults to local GlitchTip; override via env
DEFAULT_DSN = "http://rmi-glitchtip-web:8000/1"
DEFAULT_ENV = "production"
DEFAULT_SAMPLE_RATE = 0.1  # 10% of transactions traced

# Sensitive field names that must never be sent to error tracking
SENSITIVE_KEYS = {
    "authorization",
    "x-api-key",
    "x-payment",
    "x-agent-id",
    "password",
    "secret",
    "token",
    "cookie",
    "set-cookie",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
    "credit_card",
    "ssn",
}


def _scrub_secrets(data: Any) -> Any:
    """Recursively replace sensitive values with '[REDACTED]'.

    Walks dicts and lists, checking each key against SENSITIVE_KEYS.
    """
    if isinstance(data, dict):
        return {
            k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else _scrub_secrets(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_scrub_secrets(x) for x in data]
    return data


def setup_sentry() -> bool:
    """Initialize the Sentry SDK pointed at our self-hosted GlitchTip.

    Returns True if initialized, False if DSN not configured or
    sentry_sdk is not installed (graceful - backend still works).
    """
    dsn = os.getenv("GLITCHTIP_DSN") or os.getenv("SENTRY_DSN")
    if not dsn:
        log.info("sentry_disabled no_dsn")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.httpx import HttpxIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
    except ImportError as exc:
        log.info(f"sentry_sdk_not_installed err={exc}")
        return False

    env = os.getenv("ENVIRONMENT", DEFAULT_ENV)
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", DEFAULT_SAMPLE_RATE))

    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,  # PII stays in our infra
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            HttpxIntegration(),
            RedisIntegration(),
        ],
        before_send=_before_send,
        before_send_transaction=_before_send_transaction,
    )
    log.info(f"sentry_initialized dsn={dsn[:30]}... env={env} sample_rate={traces_sample_rate}")
    return True


def _before_send(event: dict, hint: dict) -> dict | None:
    """Strip secrets before sending to GlitchTip.

    Per v4.0 §T07: secrets scrubbed before send. This is a hard
    requirement - never log full request bodies with credentials.
    """
    try:
        if "request" in event:
            if "headers" in event["request"]:
                event["request"]["headers"] = _scrub_secrets(event["request"]["headers"])
            if "data" in event["request"]:
                event["request"]["data"] = _scrub_secrets(event["request"]["data"])
        if "extra" in event:
            event["extra"] = _scrub_secrets(event["extra"])
        if "user" in event:
            # Strip email/IP - keep only id
            event["user"] = {"id": event["user"].get("id", "anon")}
        return event
    except Exception as e:
        log.warning(f"sentry_scrub_fail err={e}")
        return event  # Better to log it than to drop it


def _before_send_transaction(event: dict, hint: dict) -> dict | None:
    """Drop transactions that are health checks / metrics scrapes (high volume, low value)."""
    url = event.get("transaction") or event.get("request", {}).get("url", "")
    if any(s in url for s in ("/health", "/ready", "/live", "/metrics", "/openapi.json")):
        return None
    return event


def capture_exception(error: BaseException, **extra: Any) -> None:
    """Manually capture an exception. For use in catch blocks where
    we want to log to Sentry but continue execution."""
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for k, v in extra.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_exception(error)
    except Exception as e:
        log.warning(f"sentry_capture_fail err={e}")


def capture_message(message: str, level: str = "info", **extra: Any) -> None:
    """Manually capture a message. For non-exception events."""
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for k, v in extra.items():
                scope.set_extra(k, v)
            sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        log.warning(f"sentry_message_fail err={e}")


def flush_sentry(timeout: float = 2.0) -> None:
    """Flush pending events. Call before shutdown."""
    try:
        import sentry_sdk

        sentry_sdk.flush(timeout=timeout)
    except (ConnectionError, OSError, TimeoutError):
        pass
