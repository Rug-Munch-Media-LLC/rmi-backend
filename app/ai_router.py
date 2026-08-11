#!/usr/bin/env python3
"""
AI Router — DIRECT LOCAL MODELS
=================================
All consumer chat (Telegram bot, website, rugmaps, rugcharts) routes DIRECTLY
to the self-hosted llama.cpp servers. NO litellm proxy, NO paid/external APIs.

Local models (llama.cpp, OpenAI-compatible /v1 endpoints):
  - qwen-chat          (Qwen3-4B-Abliterated,   llama.cpp :8085, 8k ctx)
  - deepseek-analysis  (DeepSeek-R1-8B-Abliterated, llama.cpp :8086, 8k ctx)
"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import httpx

from fastapi import APIRouter

router = APIRouter(tags=["AI Router"])

# ── Local model registry ──
# model_name → llama.cpp port on the docker bridge gateway.
LOCAL_MODEL_PORTS = {
    "qwen-chat": 8085,
    "deepseek-analysis": 8086,
}
DEFAULT_MODEL = "qwen-chat"

# Backend runs in Docker on the rmi_network bridge (172.22.0.0/16); llama.cpp
# runs on the HOST, so the container reaches it via the docker bridge gateway
# 172.22.0.1 — NOT 127.0.0.1 (that's the container itself).
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")

# ── Task → Model routing map ──
TASK_MODEL_MAP = {
    "chat": "qwen-chat",
    "analysis": "deepseek-analysis",
    "simple": "qwen-chat",
    "complex": "deepseek-analysis",
    "code": "deepseek-analysis",
    "bulk": "qwen-chat",
    "chart": "deepseek-analysis",
    "contract": "deepseek-analysis",
}

logger = get_logger("ai_router")

# Model tiers (compat shim for /ai/models — reflects the local reality)
MODEL_TIERS = {
    "T0": {"name": "Local-Deep", "models": ["deepseek-analysis"], "max_cost_per_1k": 0.0},
    "T1": {"name": "Local-Deep", "models": ["deepseek-analysis"], "max_cost_per_1k": 0.0},
    "T2": {"name": "Local-Fast", "models": ["qwen-chat"], "max_cost_per_1k": 0.0},
    "T3": {"name": "Local-Fast", "models": ["qwen-chat"], "max_cost_per_1k": 0.0},
    "T4": {"name": "Local-Fast", "models": ["qwen-chat"], "max_cost_per_1k": 0.0},
}

PROVIDERS = {
    "llama.cpp": {"host": LLAMA_HOST, "ports": LOCAL_MODEL_PORTS, "local": True},
}


def _strip_think_tags(text: str) -> str:
    """Strip <think> tags from model output (Qwen3/DeepSeek R1 reasoning).

    Handles both complete blocks (<think>...</think>) and unterminated blocks
    that get cut off when max_tokens is reached mid-think (strip everything from
    <think> onward in that case).
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Unterminated think block (truncated by max_tokens) — drop it entirely
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _resolve_model(model: str | None, task_type: str | None) -> str:
    """Resolve to a LOCAL model name. Non-local names are coerced to default."""
    if model and model in LOCAL_MODEL_PORTS:
        return model
    if task_type and task_type in TASK_MODEL_MAP:
        return TASK_MODEL_MAP[task_type]
    return DEFAULT_MODEL


class _ThinkStreamFilter:
    """Incrementally strip <think>...</think> blocks from a token stream."""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        self._buf = ""

    def feed(self, token: str) -> str:
        self._buf += token
        out: list[str] = []
        while self._buf:
            if self._in_think:
                end = self._buf.find(self.CLOSE)
                if end == -1:
                    break
                self._buf = self._buf[end + len(self.CLOSE) :]
                self._in_think = False
                continue
            start = self._buf.find(self.OPEN)
            if start == -1:
                emit = self._buf
                self._buf = ""
                for i in range(1, len(self.OPEN)):
                    if emit.endswith(self.OPEN[:i]):
                        self._buf = emit[-i:]
                        emit = emit[:-i]
                        break
                if emit:
                    out.append(emit)
                break
            if start:
                out.append(self._buf[:start])
            self._buf = self._buf[start + len(self.OPEN) :]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        leftover = "" if self._in_think else self._buf
        self._buf = ""
        self._in_think = False
        return leftover


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


async def _post_chat(
    client: Any,
    url: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    provider: str,
) -> dict[str, Any] | None:
    """POST a chat completion; return normalized dict or None on failure."""
    resp = await client.post(
        url,
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code != 200:
        logger.warning(
            "local_llm_non_200",
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
        content = _strip_think_tags(choices[0]["message"].get("content", ""))
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


def _other_local(model: str) -> str:
    """Return the other local model (cross-model redundancy)."""
    for name in LOCAL_MODEL_PORTS:
        if name != model:
            return name
    return DEFAULT_MODEL


# ── Direct chat completion (used by app.routers.chat) ──


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str | None = None,
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Call a LOCAL llama.cpp model directly via task-based routing.

    Chain (all local, no external APIs):
      1. Direct llama.cpp for the resolved model
      2. The other local llama.cpp server (cross-model redundancy)
      3. ExternalAPIError
    """
    import httpx

    from app.core.ai_guardrails import sanitize_input as _guard_input, sanitize_output as _guard_output

    resolved_model = _resolve_model(model, task_type)

    # ── GUARDRAILS: Sanitize last user message before sending ──
    messages_safe = list(messages)
    for i in range(len(messages_safe) - 1, -1, -1):
        if messages_safe[i].get("role") == "user":
            original = messages_safe[i].get("content", "")
            cleaned = _guard_input(original)
            if cleaned.startswith("[BLOCKED"):
                logger.warning("guardrails_blocked_input", model=resolved_model, reason=cleaned)
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

    async with httpx.AsyncClient(timeout=120.0) as client:
        # ── 1: Direct llama.cpp for the resolved model ──
        try:
            result = await _post_chat(
                client,
                f"http://{LLAMA_HOST}:{LOCAL_MODEL_PORTS[resolved_model]}/v1/chat/completions",
                resolved_model,
                messages_safe,
                temperature,
                max_tokens,
                provider="llama-direct",
            )
            if result:
                result["content"] = _guard_output(result["content"])
                return result
        except Exception as e:
            logger.warning("llama_direct_unreachable", model=resolved_model, error=str(e))

        # ── 2: Cross-model local fallback ──
        other = _other_local(resolved_model)
        try:
            result = await _post_chat(
                client,
                f"http://{LLAMA_HOST}:{LOCAL_MODEL_PORTS[other]}/v1/chat/completions",
                other,
                messages_safe,
                temperature,
                max_tokens,
                provider="llama-direct-cross",
            )
            if result:
                result["content"] = _guard_output(result["content"])
                return result
        except Exception as e:
            logger.warning("llama_cross_unreachable", model=other, error=str(e))

    from app.core.errors import ExternalAPIError

    raise ExternalAPIError(
        message="Local AI models unavailable. Check llama-chat (port 8085) / "
        "llama-analysis (port 8086) systemd services."
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
) -> AsyncGenerator[str, None] | None:
    """Open an SSE stream; yield think-stripped tokens, or None on failure."""
    try:
        async with client.stream(
            "POST",
            url,
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status_code != 200:
                logger.warning(
                    "local_llm_stream_non_200",
                    provider=provider,
                    status=resp.status_code,
                    body=(await resp.aread())[:300],
                    model=model,
                )
                return
            think_filter = _ThinkStreamFilter()
            async for chunk in _parse_openai_sse(resp):
                visible = think_filter.feed(chunk)
                if visible:
                    yield visible
            tail = think_filter.flush()
            if tail:
                yield tail
    except Exception as e:
        logger.warning("local_llm_stream_unreachable", provider=provider, model=model, error=str(e))
        return


async def stream_chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str | None = None,
    tier: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> AsyncGenerator[str, None]:
    """Stream a LOCAL llama.cpp response token-by-token (SSE-compatible).

    Same direct-local chain as chat_completion.
    """
    import httpx

    from app.core.ai_guardrails import sanitize_input as _guard_input

    resolved_model = _resolve_model(model, task_type)

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

    async with httpx.AsyncClient(timeout=120.0) as client:
        # ── 1: Direct llama.cpp (streaming) ──
        produced = False
        gen = _stream_from(
            client,
            f"http://{LLAMA_HOST}:{LOCAL_MODEL_PORTS[resolved_model]}/v1/chat/completions",
            resolved_model,
            messages_safe,
            temperature,
            max_tokens,
            provider="llama-direct",
        )
        if gen is not None:
            async for token in gen:
                produced = True
                yield token
            if produced:
                return

        # ── 2: Cross-model local fallback (streaming) ──
        other = _other_local(resolved_model)
        produced = False
        gen = _stream_from(
            client,
            f"http://{LLAMA_HOST}:{LOCAL_MODEL_PORTS[other]}/v1/chat/completions",
            other,
            messages_safe,
            temperature,
            max_tokens,
            provider="llama-direct-cross",
        )
        if gen is not None:
            async for token in gen:
                produced = True
                yield token
            if produced:
                return

    yield ("[ERROR Local AI models unavailable. Check llama-chat (8085) / llama-analysis (8086) services.]")
