"""DataBus provider contract — typed errors, retry, timeout, metrics.

Enforced by ``tests/contract/test_databus_provider_contract.py``. A provider may
NOT: write to the DB, read secrets from ``os.environ``, log an API key, build an
``httpx.AsyncClient`` without a timeout, or swallow an error silently.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.provider_contract")

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.25
RETRY_MAX_DELAY = 4.0


# ProviderError subclasses OSError on purpose: ProviderChain already catches
# OSError/TimeoutError/httpx.HTTPError, so typed errors flow through failover.


class ProviderError(OSError):
    """Base for every provider failure. ``retryable`` drives the retry loop."""

    error_code: str = "PROVIDER_ERROR"
    retryable: bool = False

    def __init__(self, provider: str, message: str, *, status: int | None = None) -> None:
        self.provider = provider
        self.message = message
        self.status = status
        super().__init__(f"[{provider}] {message}")


class ProviderTransientError(ProviderError):
    error_code = "PROVIDER_TRANSIENT"
    retryable = True


class ProviderTimeoutError(ProviderTransientError, TimeoutError):
    error_code = "PROVIDER_TIMEOUT"


class ProviderRateLimitedError(ProviderTransientError):
    error_code = "PROVIDER_RATE_LIMITED"

    def __init__(self, provider: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(provider, "rate limited", status=429)


class ProviderAuthError(ProviderError):
    error_code = "PROVIDER_AUTH"


class ProviderPermanentError(ProviderError):
    error_code = "PROVIDER_PERMANENT"


class ProviderContractError(ProviderError):
    """Upstream answered, but the payload violates the declared shape."""

    error_code = "PROVIDER_CONTRACT"


class ProviderConfigError(ProviderError):
    """Missing key/config — a wiring bug, never retried."""

    error_code = "PROVIDER_CONFIG"


_metrics: dict[str, dict[str, float]] = {}


def _record(name: str, *, ok: bool, ms: float, retry: bool = False) -> None:
    m = _metrics.setdefault(name, {"calls": 0.0, "failures": 0.0, "retries": 0.0, "total_ms": 0.0})
    m["calls"] += 1
    m["total_ms"] += ms
    if not ok:
        m["failures"] += 1
    if retry:
        m["retries"] += 1


def provider_metrics() -> dict[str, dict[str, float]]:
    """Snapshot of per-provider call/failure/retry counters (for /metrics export)."""
    return {k: dict(v) for k, v in _metrics.items()}


def reset_provider_metrics() -> None:
    _metrics.clear()



class DatabusProvider:
    """Shared base: bounded timeout, retry with jitter, typed errors, metrics."""

    name: str = "unnamed"
    base_url: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    tier: str = "free_api"
    requires_key: bool = False
    key_env: str = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or ""
        self.transport = transport
        if timeout is not None:
            self.timeout = timeout

    def __repr__(self) -> str:  # never leak a key into a log line
        return f"<{type(self).__name__} name={self.name!r} timeout={self.timeout}>"

    # -- contract surface -------------------------------------------------

    async def fetch(self, **kwargs: Any) -> Any | None:
        """Retry-wrapped entry point. Raises :class:`ProviderError` subclasses."""
        last: ProviderTransientError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                result = await self._call(**kwargs)
                _record(self.name, ok=True, ms=(time.perf_counter() - started) * 1000)
                return result
            except ProviderTransientError as exc:
                last = exc
                _record(self.name, ok=False, ms=(time.perf_counter() - started) * 1000, retry=attempt > 1)
                if attempt >= self.max_attempts:
                    break
                jitter = secrets.randbits(8) / 1024.0  # 0..0.25s de-synchronises clients
                delay = min(RETRY_BASE_DELAY * 2 ** (attempt - 1) + jitter, RETRY_MAX_DELAY)
                logger.warning(
                    "provider_retry",
                    provider=self.name,
                    attempt=attempt,
                    delay=round(delay, 3),
                    error_code=exc.error_code,
                )
                await asyncio.sleep(delay)
            except ProviderError as exc:
                _record(self.name, ok=False, ms=(time.perf_counter() - started) * 1000)
                logger.warning("provider_failed", provider=self.name, error_code=exc.error_code, status=exc.status)
                raise
        logger.error("provider_exhausted", provider=self.name, error_code=last.error_code if last else None)
        raise last or ProviderTransientError(self.name, "exhausted with no error recorded")

    # -- subclass hook ----------------------------------------------------

    async def _call(self, **kwargs: Any) -> Any | None:
        raise NotImplementedError

    # -- shared transport -------------------------------------------------

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        """One GET with the declared timeout, mapped to typed errors."""
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.get(url, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(self.name, f"timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ProviderTransientError(self.name, f"transport error: {type(exc).__name__}") from exc
        self.raise_for_status(resp)
        return resp

    def raise_for_status(self, resp: httpx.Response) -> None:
        """Map an HTTP status onto the taxonomy. 429 and 5xx stay retryable."""
        status = resp.status_code
        if status < 400:
            return
        if status == 429:
            retry_after = resp.headers.get("retry-after")
            raise ProviderRateLimitedError(self.name, retry_after=float(retry_after) if retry_after else None)
        if status >= 500:
            raise ProviderTransientError(self.name, f"upstream {status}", status=status)
        if status in (401, 403):
            raise ProviderAuthError(self.name, f"auth rejected ({status})", status=status)
        if status == 404:
            raise ProviderPermanentError(self.name, "not found", status=status)
        raise ProviderPermanentError(self.name, f"upstream {status}", status=status)
