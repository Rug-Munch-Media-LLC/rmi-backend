"""Free-only, never-raising completion chain for the brand chat.

Contract (``complete``):

    result = await complete(messages, max_tokens=512, retrieval=[...])
    {
      "text": str,            # always usable, never empty
      "provider": str,        # provider label, or "retrieval_only"
      "model": str,
      "latency_ms": int,      # wall clock for the whole chain attempt
      "fallback_used": bool,  # True when anything after step 1 answered
      "errors": [{"provider", "model", "error"}],
      "tokens": {"prompt_tokens", "completion_tokens", "total_tokens"},
      "cost_usd": 0.0,        # every lane here is a free tier
      "attempts": int,
      "degraded": bool,       # True when no model answered
    }

``retrieval`` contract: each item is ``{"text": str, "source": str,
"url": str | None}``. When it is supplied the final fallback is a deterministic
citation block. When it is ``None`` (caller keeps its own retrieval layer) the
fallback text is ``""`` so the caller renders its own citations — never a
boilerplate "no sources" answer masquerading as content.

Order: ``providers.CHAIN`` (free lanes only) -> ``retrieval_only``.
Per provider: one retry for transient failures, a 20 s timeout (6 s local),
and a circuit breaker that opens after 3 consecutive failures for 5 minutes.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from app.core.logging import get_logger
from app.domains.chat.breaker import BREAKER
from app.domains.chat.providers import RETRIEVAL_PROVIDER, refusal_reasons, resolve_chain
from app.domains.chat.retrieval_fallback import retrieval_only_answer
from app.domains.chat.transport import ProviderError, request_provider

logger = get_logger(__name__)

MAX_ATTEMPTS = 2  # initial call + one retry
EMPTY_TOKENS = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


async def complete(
    messages: list[dict],
    max_tokens: int = 512,
    retrieval: Sequence[dict] | None = None,
    timeout_s: float | None = None,
) -> dict:
    """Try every free lane in order; degrade to citations. Never raises.

    ``timeout_s`` is an overall wall-clock budget across the whole chain; when
    it is exhausted the remaining lanes are skipped and the fallback is served.
    """
    started = time.perf_counter()
    errors: list[dict] = [
        {"provider": RETRIEVAL_PROVIDER, "model": "-", "error": reason}
        for reason in refusal_reasons()
    ]
    attempts = 0

    try:
        chain = resolve_chain()
        for index, spec in enumerate(chain):
            if timeout_s is not None and time.perf_counter() - started > timeout_s:
                errors.append(
                    {
                        "provider": spec.provider,
                        "model": spec.model,
                        "error": f"skipped: {timeout_s}s chain budget exhausted",
                    }
                )
                continue
            if not BREAKER.allow(spec.key):
                errors.append(
                    {"provider": spec.provider, "model": spec.model, "error": "circuit_open"}
                )
                continue

            budget = MAX_ATTEMPTS if spec.retry else 1
            for attempt in range(1, budget + 1):
                attempts += 1
                try:
                    text, tokens = await request_provider(spec, messages, max_tokens)
                except ProviderError as exc:
                    errors.append(
                        {"provider": spec.provider, "model": spec.model, "error": str(exc)}
                    )
                    retry_left = spec.retry and exc.retryable and attempt < MAX_ATTEMPTS
                    if not retry_left:
                        BREAKER.record_failure(spec.key)
                        break
                    continue
                except Exception as exc:  # chain must never raise
                    errors.append(
                        {
                            "provider": spec.provider,
                            "model": spec.model,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    BREAKER.record_failure(spec.key)
                    break

                BREAKER.record_success(spec.key)
                return {
                    "text": text,
                    "provider": spec.provider,
                    "model": spec.model,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "fallback_used": index > 0,
                    "errors": errors,
                    "tokens": tokens,
                    "cost_usd": 0.0,
                    "attempts": attempts,
                    "degraded": False,
                }

        logger.warning("chat_chain_exhausted", attempts=attempts, errors=len(errors))
    except Exception as exc:  # absolute last resort
        errors.append(
            {"provider": RETRIEVAL_PROVIDER, "model": "-", "error": f"chain_crash: {exc}"}
        )

    return {
        "text": retrieval_only_answer(retrieval) if retrieval is not None else "",
        "provider": RETRIEVAL_PROVIDER,
        "model": "-",
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "fallback_used": True,
        "errors": errors,
        "tokens": dict(EMPTY_TOKENS),
        "cost_usd": 0.0,
        "attempts": attempts,
        "degraded": True,
    }
