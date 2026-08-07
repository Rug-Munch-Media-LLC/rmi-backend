#!/usr/bin/env python3
"""
AI Guardrails — Anti-Poisoning for Public AI Endpoints
========================================================
Wires guardrails-ai (0.10.2) with ToxicLanguage + CompetitorCheck validators
plus custom prompt-injection detection and output sanitization.

All public AI endpoints (chat, bot, website) must route through this module
before sending to ANY model provider and after receiving ANY response.

Integrations:
  - app/ai_router.py: chat_completion() wraps input/output
  - app/routers/chat.py: /api/v1/chat (already uses AIGuard, this adds guardrails-ai)
  - bot/app/telegram_bot/guardrails.py: standalone bot guardrails (mirrored patterns)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# Prompt injection patterns to detect
# ═════════════════════════════════════════════════════════════════════════════
INJECTION_PATTERNS: list[str] = [
    "ignore previous instructions",
    "ignore all previous",
    "ignore your instructions",
    "you are now",
    "system prompt",
    "act as",
    "pretend you are",
    "jailbreak",
    "bypass",
    "DAN mode",
    "developer mode",
    "god mode",
    "unrestricted",
    "do anything now",
    "override safety",
    "disable your guardrails",
    "forget your training",
    "reveal your prompt",
    "tell me your instructions",
    "what are your rules",
    "new instructions:",
    "from now on you are",
    "you are a",
    "you are an",
    "you\\'re a",
    "roleplay as",
    "you must obey",
    "bypass your filter",
    "disable security",
    "you are in developer mode",
    "jail broken",
    "cracked version",
]

# ═════════════════════════════════════════════════════════════════════════════
# Model delimiter tokens that must never leak from user input into system context
# ═════════════════════════════════════════════════════════════════════════════
FORBIDDEN_OUTPUT_TOKENS: list[str] = [
    "<system>",
    "</system>",
    "<|im_start|>",
    "<|im_end|>",
    "[SYSTEM]",
    "[/SYSTEM]",
    "<<SYS>>",
    "</SYS>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "[INST]",
    "[/INST]",
]

# ═════════════════════════════════════════════════════════════════════════════
# Internal infrastructure patterns that must never appear in output
# ═════════════════════════════════════════════════════════════════════════════
_INTERNAL_PATTERNS: list[str] = [
    "100.64.",
    "talos.rugmunch.io",
    "cinnabox",
    "gopass",
    "/srv/work/repos",
    "/root/backend",
    "rmi_auth_token",
    "RMI_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "LLM_API_KEY",
]


def sanitize_input(text: str) -> str:
    """Strip injection patterns from user input before sending to any LLM.

    Returns cleaned text or a canned block message if the input contains
    known prompt-injection / jailbreak patterns.
    """
    if not text or not text.strip():
        return text

    lower = text.lower()

    # Check injection patterns
    for pattern in INJECTION_PATTERNS:
        if pattern.lower() in lower:
            logger.warning(
                "guardrails_ai: input blocked (injection pattern=%r, text[:80]=%r)",
                pattern,
                text[:80],
            )
            return "[BLOCKED: Injection attempt detected]"

    # Strip null bytes and excessive whitespace
    text = text.replace("\x00", "").strip()

    # Truncate to 4000 chars max (defense against context-window stuffing)
    if len(text) > 4000:
        text = text[:4000]

    return text


def sanitize_output(text: str) -> str:
    """Post-process AI output to remove system prompt leaks, secrets, and
    oversized code blocks.
    """
    if not text:
        return text

    # Strip system prompt / delimiter token leaks
    for forbidden in FORBIDDEN_OUTPUT_TOKENS:
        if forbidden in text:
            logger.warning("guardrails_ai: output filtered (delimiter token leak)")
            return "[Response filtered for safety]"

    # Strip internal infrastructure identifiers
    for pattern in _INTERNAL_PATTERNS:
        if pattern in text:
            logger.warning("guardrails_ai: output filtered (internal identifier leak)")
            return "[Response filtered for safety]"

    # Remove oversized code blocks (> 50 lines) to prevent code execution
    lines = text.split("\n")
    code_count = 0
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        elif in_code:
            code_count += 1
            if code_count > 50:
                logger.warning("guardrails_ai: output filtered (large code block)")
                return "[Large code block removed for safety]"

    return text


# ═════════════════════════════════════════════════════════════════════════════
# Optional guardrails-ai validators (requires guardrails-ai packages)
# ═════════════════════════════════════════════════════════════════════════════
try:
    from guardrails import Guard
    from guardrails.hub import CompetitorCheck, ToxicLanguage

    _toxic_guard = Guard().use(ToxicLanguage(on_fail="fix"))
    _competitor_guard = Guard().use(CompetitorCheck(on_fail="fix"))
    GUARDRAILS_AI_LOADED = True
except ImportError:
    _toxic_guard = None
    _competitor_guard = None
    GUARDRAILS_AI_LOADED = False

    # Log once at module load so ops can see if the packages are truly installed.
    logger.info("guardrails_ai: guardrails-ai packages not available; using regex-only mode")


def validate_toxic(text: str) -> dict[str, Any]:
    """Check for toxic language via guardrails-ai ToxicLanguage validator.

    Returns dict with ``safe`` flag and ``cleaned`` text.
    Falls back to passthrough if guardrails-ai is not installed.
    """
    if not GUARDRAILS_AI_LOADED or _toxic_guard is None:
        return {"safe": True, "cleaned": text}

    try:
        result = _toxic_guard.validate(text)
        if result.validation_passed:
            return {"safe": True, "cleaned": text}
        return {"safe": False, "cleaned": result.validated_output or text}
    except Exception as exc:
        logger.warning("guardrails_ai: ToxicLanguage validation error: %s", exc)
        return {"safe": True, "cleaned": text}


def validate_competitor(text: str) -> dict[str, Any]:
    """Check for competitor mentions via guardrails-ai CompetitorCheck.

    Returns dict with ``safe`` flag and ``cleaned`` text.
    """
    if not GUARDRAILS_AI_LOADED or _competitor_guard is None:
        return {"safe": True, "cleaned": text}

    try:
        result = _competitor_guard.validate(text)
        if result.validation_passed:
            return {"safe": True, "cleaned": text}
        return {"safe": False, "cleaned": result.validated_output or text}
    except Exception as exc:
        logger.warning("guardrails_ai: CompetitorCheck validation error: %s", exc)
        return {"safe": True, "cleaned": text}


def full_pipeline(text: str) -> dict[str, Any]:
    """Run the complete guardrails pipeline: injection check → toxic → competitor.

    Returns dict with:
        - safe: bool
        - text: cleaned text (or block message)
        - blocked: bool (True = completely blocked)
        - reasons: list of rejection reasons
    """
    result: dict[str, Any] = {"safe": True, "blocked": False, "text": text, "reasons": []}

    # Layer 1: Injection patterns (hard block)
    cleaned = sanitize_input(text)
    if cleaned.startswith("[BLOCKED"):
        result["safe"] = False
        result["blocked"] = True
        result["text"] = cleaned
        result["reasons"].append("prompt_injection")
        return result

    # Layer 2: Toxic language (fix if possible)
    tox = validate_toxic(cleaned)
    if not tox["safe"]:
        result["reasons"].append("toxic_language")
    cleaned = tox["cleaned"]

    # Layer 3: Competitor check
    comp = validate_competitor(cleaned)
    if not comp["safe"]:
        result["reasons"].append("competitor_mention")
    cleaned = comp["cleaned"]

    result["text"] = cleaned
    result["safe"] = len(result["reasons"]) == 0
    return result
