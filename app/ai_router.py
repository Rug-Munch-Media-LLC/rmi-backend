#!/usr/bin/env python3
"""
AI Router — REMOTE PROVIDERS
=============================
All consumer chat (Telegram bot, website, rugmaps, rugcharts) routes through
the free remote LLM providers configured in ``app.llm_config``. The former
local model defaults were decommissioned 2026-08-16.

Provider tiers:
  - snappy  → ``get_snappy_config()`` — OpenRouter (laguna snappy), fast
              short chat answers.
  - long    → ``get_long_config()`` — OpenRouter (nemotron long), deep
              reasoning / analysis answers.
  - fallback→ ``get_fast_config()`` — direct DeepSeek API, last resort.

Routing: ``_resolve_model`` maps the legacy local model aliases
(``qwen-chat`` → snappy, ``qwen-analysis`` → long) and the task types to a
tier; the tier selects the config. Every provider is called with httpx
Bearer auth; when the configured API key is missing the call fails OPEN with
a clear message instead of crashing the endpoint.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx

from app.core.ai_guardrails import ThinkStreamFilter, strip_think_tags
from app.core.logging import get_logger
from app.databus.proxy_rotator import get_proxy_rotator

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import APIRouter

router = APIRouter(tags=["AI Router"])

# ── Tier registry ──
# Backward-compat aliases: callers may still pass model="qwen-chat"/"qwen-analysis".
TIER_ALIASES = {
    "qwen-chat": "snappy",
    "qwen-analysis": "long",
}

# Task → tier routing map (mirrors the old local task→model map).
TASK_TIER_MAP = {
    "chat": "snappy",
    "simple": "snappy",
    "bulk": "snappy",
    "analysis": "long",
    "complex": "long",
    "code": "long",
    "chart": "long",
    "contract": "long",
}

DEFAULT_TIER = "snappy"

# Remote read timeout (s). These are cloud providers — generation is fast;
# the long (nemotron) tier can still take a while on deep reasoning prompts.
REMOTE_TIMEOUT = 60.0

logger = get_logger("ai_router")

# Model tiers (compat shim for /ai/models — reflects the remote reality)
MODEL_TIERS = {
    **{f"T{i}": {"name": "Snappy", "models": ["poolside/laguna-s-2.1:free"], "max_cost_per_1k": 0.0} for i in range(3)},
    **{
        f"T{i}": {"name": "Long", "models": ["nvidia/nemotron-3-ultra-550b-a55b:free"], "max_cost_per_1k": 0.0}
        for i in (3, 4)
    },
}

PROVIDERS = {
    "zen": {"host": "opencode.ai", "tier": "snappy", "local": False},
    "deepseek": {"host": "api.deepseek.com", "tier": "fallback", "local": False},
}


def _tier_config(tier: str) -> dict[str, Any]:
    """Return the ``app.llm_config`` dict for a tier.

    Fail-open: an unknown tier falls back to the snappy config so callers
    never hit a KeyError from routing drift.
    """
    from app import llm_config

    if tier == "long":
        return llm_config.get_long_config()
    return llm_config.get_snappy_config()


# Degenerate-output guard: a model can emit a bare markdown bullet ("- "),
# empty output, or punctuation-only on vague, context-free questions.
# Replace those with a helpful fallback instead of surfacing "- " to users.
_FALLBACK_RESPONSE = (
    "I couldn't find enough context to answer that. "
    "Try pasting a token address or ticker ($SYMBOL), or ask about a specific "
    "chain, wallet, or market event."
)

_DEGENERATE_RE = re.compile(r"^\s*[-*\u2022\u00b7\u2013\u2014]+\s*$")


def _is_degenerate(text: str) -> bool:
    """True when an LLM response carries no usable content."""
    stripped = (text or "").strip()
    if not stripped:
        return True
    if _DEGENERATE_RE.match(stripped):
        return True
    # No alphanumeric content at all (pure punctuation/symbols/emoji) — degenerate.
    return not any(c.isalnum() for c in stripped)


def _resolve_model(model: str | None, task_type: str | None, tier: str | None = None) -> str:
    """Resolve to a TIER name (``snappy`` | ``long``).

    Explicit legacy aliases win (``qwen-chat`` → snappy, ``qwen-analysis`` →
    long); any other explicit model string is coerced to the task-type tier
    (matching the old behavior of coercing unknown names to the default). The
    API ``tier`` code (T0-T4) is the last-resort fallback: T3/T4 → long, all
    others → snappy.
    """
    if model and model in TIER_ALIASES:
        return TIER_ALIASES[model]
    if task_type and task_type in TASK_TIER_MAP:
        return TASK_TIER_MAP[task_type]
    if tier:
        return "long" if str(tier).upper() in ("T3", "T4") else "snappy"
    return DEFAULT_TIER


def _provider_label(tier: str) -> str:
    """Stable provider label for the response ``_provider`` field."""
    return "zen-snappy" if tier == "snappy" else "kilo-long"


# ── Compat router endpoints (mounted by other routers) ──


@router.post("/ai/completions")
async def ai_completions(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") or [{"role": "user", "content": request.get("prompt", "")}]
    return await chat_completion(messages=messages, model=request.get("model"), task_type=request.get("task_type"))


@router.post("/ai/chat")
async def ai_chat(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") or [{"role": "user", "content": request.get("prompt", "")}]
    return await chat_completion(messages=messages, model=request.get("model"), task_type=request.get("task_type"))


@router.get("/ai/providers")
async def list_providers() -> dict[str, list[Any]]:
    return {"providers": list(PROVIDERS.keys())}


@router.get("/ai/models")
async def list_models() -> dict[str, Any]:
    return {"tiers": MODEL_TIERS}


# ── Provider call helpers ──


def _is_proxyable_destination(url: str) -> bool:
    """True when ``url`` targets a public host that should ride the proxy tier.

    Private / loopback destinations (localhost, docker bridge, internal
    gateways like a local LLM gateway on 127.0.0.1) are never proxied: a
    residential proxy cannot reach RFC1918 space, so sending those through
    the tier would only burn a rotation and add latency.
    """
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    # RFC1918 private ranges: 10/8, 172.16/12 (docker bridge), 192.168/16
    return not host.startswith(("10.", "172.", "192.168."))


def _build_ai_client(rotator: Any, url: str) -> tuple[Any, str | None]:
    """Build the httpx client for one chat request.

    When the residential proxy rotator is enabled AND ``url`` is a public
    destination, the client is routed through the next rotating proxy
    (proxy-first, gmgn_provider style). Otherwise — rotator disabled, every
    proxy on cooldown, or a private/loopback destination — the client is
    direct.

    Returns ``(client, proxy_url)`` where ``proxy_url`` is None for a direct
    client; the caller passes both to :func:`_post_chat` so a blocked proxy
    can be marked failed and retried direct.
    """
    proxy_url: str | None = None
    if rotator.enabled and _is_proxyable_destination(url):
        proxy_url = rotator.get_rotating_proxy()
    return rotator.get_client(proxy=proxy_url), proxy_url


async def _post_chat(
    client: Any,
    url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    provider: str,
    *,
    proxy_url: str | None = None,
    rotator: Any = None,
    api_key: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """POST a chat completion; return normalized dict or None on failure.

    When the client is proxy-routed (``proxy_url`` set), a 403/429/5xx
    response or a transport error marks the proxy failed (cooldown) and
    retries the request once against a direct client — the same
    proxy→direct fallback as :mod:`app.databus.gmgn_provider`, so a
    blocked/banned residential egress degrades to the datacenter IP instead
    of failing the call.

    ``api_key`` adds the Bearer Authorization header required by every
    remote provider.

    ``timeout`` (optional) overrides the client's default read timeout for
    THIS request. ``None`` keeps the client-default timeout (proxied cloud
    calls stay on the rotator's data-fetch budget).
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    _headers = {"Content-Type": "application/json"}
    if api_key:
        _headers["Authorization"] = f"Bearer {api_key}"

    _request_kwargs: dict[str, Any] = {"json": payload, "headers": _headers}
    if timeout is not None:
        _request_kwargs["timeout"] = httpx.Timeout(timeout, connect=10.0)

    async def _do_post(post_client: Any) -> httpx.Response:
        return await post_client.post(url, **_request_kwargs)

    proxied = proxy_url is not None and rotator is not None

    try:
        resp = await _do_post(client)
    except (httpx.HTTPError, ConnectionError, TimeoutError):
        if not proxied:
            logger.warning("llm_unreachable", provider=provider, url=url[:120])
            return None
        logger.warning("llm_proxy_transport_error", provider=provider, url=url[:120])
        rotator.mark_failed(proxy_url)
        direct = rotator.get_client(proxy=None)
        try:
            resp = await _do_post(direct)
        except (httpx.HTTPError, ConnectionError, TimeoutError):
            return None
        finally:
            await direct.aclose()

    if proxied and (resp.status_code in (403, 429) or resp.status_code >= 500):
        logger.warning(
            "llm_proxy_blocked",
            provider=provider,
            status=resp.status_code,
            url=url[:120],
        )
        rotator.mark_failed(proxy_url)
        direct = rotator.get_client(proxy=None)
        try:
            resp = await _do_post(direct)
        finally:
            await direct.aclose()

    if resp.status_code != 200:
        logger.warning(
            "llm_non_200",
            provider=provider,
            status=resp.status_code,
            body=resp.text[:300],
            model=model,
        )
        return None
    data = resp.json()
    choices = data.get("choices", [])
    content = ""
    if choices and "message" in choices[0]:
        content = strip_think_tags(choices[0]["message"].get("content", ""))
    return {
        "id": data.get("id", "chat-unknown"),
        "content": content,
        "choices": choices,
        "model": data.get("model", model),
        "usage": data.get("usage", {}),
        "_provider": provider,
        "_latency_ms": int(resp.elapsed.total_seconds() * 1000),
        "_routed_model": model,
    }


def _missing_key_message(tier: str, config: dict[str, Any]) -> str:
    """Clear, actionable message when a provider API key is not configured."""
    key_env = "OPENROUTER_BACKUP_API_KEY"
    return (
        f"AI provider unavailable: {key_env} is not configured (tier={tier}, "
        f"model={config.get('model', '?')}). Set the key in the environment to "
        "enable AI chat."
    )


# ── Direct chat completion (used by app.routers.chat) ──


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str | None = None,
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> dict[str, Any]:
    """Call a remote LLM provider via tier-based routing.

    Chain:
      1. Primary tier (snappy = laguna / long = nemotron via OpenRouter)
      2. The other tier (cross-provider redundancy)
      3. Direct DeepSeek API fallback (``get_fast_config``)
      4. ExternalAPIError with a clear message

    Fails OPEN with a clear message when the provider API key is missing.
    """
    from app.core.ai_guardrails import sanitize_input as _guard_input, sanitize_output as _guard_output
    from app.core.errors import ExternalAPIError

    resolved_tier = _resolve_model(model, task_type, tier)

    # ── GUARDRAILS: Sanitize last user message before sending ──
    messages_safe = list(messages)
    for i in range(len(messages_safe) - 1, -1, -1):
        if messages_safe[i].get("role") == "user":
            original = messages_safe[i].get("content", "")
            cleaned = _guard_input(original)
            if cleaned.startswith("[BLOCKED"):
                logger.warning("guardrails_blocked_input", tier=resolved_tier, reason=cleaned)
                return {
                    "id": "guardrails-blocked",
                    "content": cleaned,
                    "choices": [{"message": {"content": cleaned}}],
                    "model": "guardrails",
                    "usage": {},
                    "_provider": "guardrails",
                    "_latency_ms": 0,
                }
            messages_safe[i]["content"] = cleaned
            break

    primary_cfg = _tier_config(resolved_tier)
    if not primary_cfg.get("api_key"):
        raise ExternalAPIError(message=_missing_key_message(resolved_tier, primary_cfg))

    # Client is proxy-first when the residential rotator is enabled and the
    # destination is public (see _build_ai_client); the local LLM gateway
    # (127.0.0.1) and private bridges stay direct. _post_chat retries direct
    # on a blocked/banned proxy.
    rotator = get_proxy_rotator()
    client, proxy_url = _build_ai_client(rotator, primary_cfg["base_url"])

    async with client:
        # ── 1: Primary tier ──
        try:
            result = await _post_chat(
                client,
                primary_cfg["base_url"],
                primary_cfg["model"],
                messages_safe,
                temperature,
                max_tokens,
                provider=_provider_label(resolved_tier),
                proxy_url=proxy_url,
                rotator=rotator,
                api_key=primary_cfg["api_key"],
                timeout=REMOTE_TIMEOUT,
            )
            if result:
                content = _guard_output(result["content"])
                result["content"] = _FALLBACK_RESPONSE if _is_degenerate(content) else content
                return result
        except Exception as e:
            logger.warning("llm_primary_unreachable", tier=resolved_tier, error=str(e))

        # ── 2: Cross-tier fallback (other provider) ──
        other_tier = "long" if resolved_tier == "snappy" else "snappy"
        other_cfg = _tier_config(other_tier)
        if other_cfg.get("api_key"):
            try:
                result = await _post_chat(
                    client,
                    other_cfg["base_url"],
                    other_cfg["model"],
                    messages_safe,
                    temperature,
                    max_tokens,
                    provider=_provider_label(other_tier),
                    proxy_url=proxy_url,
                    rotator=rotator,
                    api_key=other_cfg["api_key"],
                    timeout=REMOTE_TIMEOUT,
                )
                if result:
                    content = _guard_output(result["content"])
                    result["content"] = _FALLBACK_RESPONSE if _is_degenerate(content) else content
                    return result
            except Exception as e:
                logger.warning("llm_cross_unreachable", tier=other_tier, error=str(e))

        # ── 3: Direct DeepSeek API fallback ──
        try:
            from app import llm_config

            _cfg = llm_config.get_fast_config()
            if _cfg.get("api_key") and _cfg.get("base_url"):
                _resp = await _post_chat(
                    client,
                    _cfg["base_url"],
                    _cfg["model"],
                    messages_safe,
                    temperature,
                    max_tokens,
                    provider="deepseek-api",
                    api_key=_cfg["api_key"],
                    proxy_url=proxy_url,
                    rotator=rotator,
                )
                if _resp:
                    content = _guard_output(_resp["content"])
                    _resp["content"] = _FALLBACK_RESPONSE if _is_degenerate(content) else content
                    return _resp
        except Exception as e:
            logger.warning("deepseek_api_fallback_failed", error=str(e))

        # ── 4: Hugging Face Inference API (free tier) — community chat last
        # resort for ANALYSIS questions. Same OpenAI-compatible shape; bounded
        # (30s timeout) + fail-open. The main chat stays on OpenRouter — HF
        # only answers when every OpenRouter/DeepSeek tier is down.
        try:
            from app import llm_config as _llm_cfg

            hf_cfg = _llm_cfg.get_hf_config()
            if hf_cfg.get("base_url") and hf_cfg.get("model"):
                _hf = await _post_chat(
                    client,
                    hf_cfg["base_url"],
                    hf_cfg["model"],
                    messages_safe,
                    temperature,
                    max_tokens,
                    provider="huggingface",
                    api_key=hf_cfg.get("api_key") or None,
                    proxy_url=proxy_url,
                    rotator=rotator,
                    timeout=30.0,
                )
                if _hf:
                    content = _guard_output(_hf["content"])
                    _hf["content"] = _FALLBACK_RESPONSE if _is_degenerate(content) else content
                    return _hf
        except Exception as e:
            logger.warning("hf_fallback_failed", error=str(e))

    raise ExternalAPIError(
        message="AI providers unavailable: primary and fallback tiers all failed. "
        "Check the OPENROUTER_BACKUP_API_KEY configuration."
    )


# ── Streaming chat completion ──


async def _parse_openai_sse(resp: httpx.Response) -> AsyncGenerator[str, None]:
    """Yield content deltas from an OpenAI-compatible SSE stream."""
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices: list[dict[str, Any]] = chunk.get("choices") or []
        if not choices:
            continue
        delta: dict[str, Any] = choices[0].get("delta") or {}
        token: Any = delta.get("content")
        if token:
            yield token


async def _stream_from(
    client: Any,
    url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    provider: str,
    *,
    api_key: str | None = None,
    timeout: float | None = None,
) -> AsyncGenerator[str, None] | None:
    """Open an SSE stream; yield think-stripped tokens, or None on failure."""
    try:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        stream_kwargs: dict[str, Any] = {}
        if timeout is not None:
            stream_kwargs["timeout"] = httpx.Timeout(timeout, connect=10.0)
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers=headers,
            **stream_kwargs,
        ) as resp:
            if resp.status_code != 200:
                logger.warning(
                    "llm_stream_non_200",
                    provider=provider,
                    status=resp.status_code,
                    body=(await resp.aread())[:300],
                    model=model,
                )
                return
            think_filter = ThinkStreamFilter()
            async for chunk in _parse_openai_sse(resp):
                visible = think_filter.feed(chunk)
                if visible:
                    yield visible
            tail = think_filter.flush()
            if tail:
                yield tail
    except Exception as e:
        logger.warning("llm_stream_unreachable", provider=provider, model=model, error=str(e))
        return


async def stream_chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str | None = None,
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> AsyncGenerator[str, None]:
    """Stream a remote LLM response token-by-token (SSE-compatible).

    Same primary → other-tier → deepseek fallback chain as chat_completion.
    Fails OPEN with a clear ``[ERROR ...]`` token when the provider API key
    is missing or every tier is unreachable.
    """
    import httpx

    from app.core.ai_guardrails import sanitize_input as _guard_input

    resolved_tier = _resolve_model(model, task_type, tier)

    messages_safe = list(messages)
    for i in range(len(messages_safe) - 1, -1, -1):
        if messages_safe[i].get("role") == "user":
            original = messages_safe[i].get("content", "")
            cleaned = _guard_input(original)
            if cleaned.startswith("[BLOCKED"):
                yield f"[ERROR guardrails_blocked: {cleaned}]"
                return
            messages_safe[i]["content"] = cleaned
            break

    primary_cfg = _tier_config(resolved_tier)
    if not primary_cfg.get("api_key"):
        from app.core.errors import ExternalAPIError

        yield f"[ERROR {ExternalAPIError(message=_missing_key_message(resolved_tier, primary_cfg))!s}]"
        return

    async with httpx.AsyncClient(timeout=httpx.Timeout(REMOTE_TIMEOUT, connect=10.0)) as client:
        # ── 1: Primary tier (streaming) ──
        produced = False
        gen = _stream_from(
            client,
            primary_cfg["base_url"],
            primary_cfg["model"],
            messages_safe,
            temperature,
            max_tokens,
            provider=_provider_label(resolved_tier),
            api_key=primary_cfg["api_key"],
            timeout=REMOTE_TIMEOUT,
        )
        if gen is not None:
            async for token in gen:
                produced = True
                yield token
            if produced:
                return

        # ── 2: Cross-tier fallback (streaming) ──
        other_tier = "long" if resolved_tier == "snappy" else "snappy"
        other_cfg = _tier_config(other_tier)
        if other_cfg.get("api_key"):
            produced = False
            gen = _stream_from(
                client,
                other_cfg["base_url"],
                other_cfg["model"],
                messages_safe,
                temperature,
                max_tokens,
                provider=_provider_label(other_tier),
                api_key=other_cfg["api_key"],
                timeout=REMOTE_TIMEOUT,
            )
            if gen is not None:
                async for token in gen:
                    produced = True
                    yield token
                if produced:
                    return

        # ── 3: Direct DeepSeek API fallback (streaming) ──
        try:
            from app import llm_config

            _cfg = llm_config.get_fast_config()
            if _cfg.get("api_key") and _cfg.get("base_url"):
                produced = False
                gen = _stream_from(
                    client,
                    _cfg["base_url"],
                    _cfg["model"],
                    messages_safe,
                    temperature,
                    max_tokens,
                    provider="deepseek-api",
                    api_key=_cfg["api_key"],
                )
                if gen is not None:
                    async for token in gen:
                        produced = True
                        yield token
                    if produced:
                        return
        except Exception as e:
            logger.warning("deepseek_api_stream_fallback_failed", error=str(e))

    yield "[ERROR AI providers unavailable: primary and fallback tiers all failed.]"
