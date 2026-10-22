"""Ports for the two sibling-owned modules: personas and the model chain.

``app.domains.chat.personas`` and ``app.domains.chat.model_chain`` are built
by other agents. This module resolves them lazily, accepts several plausible
shapes, and falls back to a built-in persona prompt / the shared ai_router so
the endpoint works before those modules land.

Kill switch: ``CHAT_PROVIDERS_DISABLED=1`` makes :func:`complete` return
``None`` — the service then serves the retrieval-only answer (proof path).
"""

from __future__ import annotations

import importlib
import os
from typing import Any, NamedTuple

from app.core.logging import get_logger

log = get_logger(__name__)

_TIMEOUT_S = 30.0

_FALLBACK_PROMPTS: dict[str, str] = {
    "desk": (
        "You are the RugMunch desk analyst. Explain on-chain risk, scanner "
        "verdicts and market structure plainly for a crypto-native operator."
    ),
    "security": (
        "You are the RugMunch security analyst. Focus on exploit mechanics, "
        "attack surface, and concrete defensive guidance, grounded strictly in "
        "the supplied passages."
    ),
    "news": (
        "You are the RugMunch news desk. Summarise what happened, who is "
        "affected, and why it matters, grounded strictly in supplied passages."
    ),
}


class ModelResult(NamedTuple):
    text: str
    provider: str


def _extract_prompt(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("system_prompt", "prompt", "system", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


def persona_prompt(persona: str) -> str:
    """Return the persona system prompt, preferring the sibling module."""
    try:
        module = importlib.import_module("app.domains.chat.personas")
    except Exception:  # noqa: BLE001 — module not landed yet
        return _FALLBACK_PROMPTS.get(persona, _FALLBACK_PROMPTS["desk"])
    for name in ("get_persona_system_prompt", "system_prompt", "get_prompt"):
        func = getattr(module, name, None)
        if callable(func):
            try:
                prompt = _extract_prompt(func(persona))
            except Exception:  # noqa: BLE001
                prompt = None
            if prompt:
                return prompt
    for name in ("PERSONAS", "PERSONA_PROMPTS"):
        mapping = getattr(module, name, None)
        if isinstance(mapping, dict):
            prompt = _extract_prompt(mapping.get(persona))
            if prompt:
                return prompt
    return _FALLBACK_PROMPTS.get(persona, _FALLBACK_PROMPTS["desk"])


def _providers_disabled() -> bool:
    for var in ("CHAT_PROVIDERS_DISABLED", "CHAT_MODEL_DISABLED"):
        if os.getenv(var, "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def _coerce(result: Any) -> ModelResult | None:
    """Normalise a model-chain return value into ``ModelResult``."""
    if result is None:
        return None
    if isinstance(result, str):
        return ModelResult(result, "model_chain") if result.strip() else None
    if isinstance(result, dict):
        text = result.get("text") or result.get("content") or ""
        provider = str(result.get("provider") or result.get("_provider") or "model_chain")
        return ModelResult(str(text), provider) if str(text).strip() else None
    text = getattr(result, "text", None) or getattr(result, "content", None)
    provider = str(getattr(result, "provider", None) or "model_chain")
    return ModelResult(str(text), provider) if text and str(text).strip() else None


async def _call_sibling(messages: list[dict[str, str]]) -> ModelResult | None:
    try:
        module = importlib.import_module("app.domains.chat.model_chain")
    except Exception:  # noqa: BLE001 — module not landed yet
        return None
    for name in ("complete", "chat", "generate"):
        func = getattr(module, name, None)
        if not callable(func):
            continue
        try:
            return _coerce(await func(messages, timeout_s=_TIMEOUT_S))
        except TypeError:
            try:
                return _coerce(await func(messages))
            except Exception as exc:  # noqa: BLE001
                log.warning("chat_model_chain_failed name=%s err=%s", name, type(exc).__name__)
                return None
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_model_chain_failed name=%s err=%s", name, type(exc).__name__)
            return None
    return None


async def complete(messages: list[dict[str, str]]) -> ModelResult | None:
    """Call the model chain; ``None`` means "no provider could answer"."""
    if _providers_disabled():
        log.warning("chat_providers_disabled_by_env")
        return None
    result = await _call_sibling(messages)
    if result is not None:
        return result
    try:
        from app.ai_router import chat_completion

        raw = await chat_completion(messages, temperature=0.2, max_tokens=400)
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_ai_router_failed err=%s", type(exc).__name__)
        return None
    return _coerce(raw)
