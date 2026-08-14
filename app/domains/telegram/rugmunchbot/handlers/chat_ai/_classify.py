"""Question classification for the free-text AI chat pipeline.

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: address
detection in free text, the gibberish gate, greeting/thanks/capability/
self-improvement/deep-question detectors, and the self-improvement regex the
tier-2 canned rules share with ``_local_ai.py``. Pure regex/string logic —
no ``Update``/``Context`` state, no handler registration. ``_helpers.py``
re-imports everything it needs from here; behavior is byte-for-byte identical.
"""

from __future__ import annotations

import re

# cap address-triggered scans per message
MAX_ADDRESS_HINTS = 2

# ══════════════════════════════════════════════
# ADDRESS DETECTION (free-text search, mirrors app/routers/chat.py)
# ══════════════════════════════════════════════
_EVM_ADDR_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOL_ADDR_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def _plausible_solana(addr: str) -> bool:
    """Base58 address-likeness: real Solana addresses almost always mix digits
    or uppercase with lowercase, so this filters word-shaped false positives
    ("supercalifragilisticexpialidocious") without dropping genuine tokens."""
    return any(ch.isdigit() or ch.isupper() for ch in addr)


def _extract_addresses(text: str) -> list[str]:
    """Return token addresses (EVM 0x... / Solana base58) found in free text.

    Capped at ``MAX_ADDRESS_HINTS`` so a single message cannot trigger a scan
    storm. Solana matches that overlap an EVM match are skipped.
    """
    addresses: list[str] = []
    for m in _EVM_ADDR_RE.finditer(text):
        addresses.append(m.group(0))
        if len(addresses) >= MAX_ADDRESS_HINTS:
            return addresses
    for m in _SOL_ADDR_RE.finditer(text):
        addr = m.group(0)
        if addr not in addresses and _plausible_solana(addr):
            addresses.append(addr)
        if len(addresses) >= MAX_ADDRESS_HINTS:
            break
    return addresses

# ══════════════════════════════════════════════
# GIBBERISH GATE (was handlers/message.py tier-1b gate)
# ══════════════════════════════════════════════


def _is_meaningful_question(text: str) -> bool:
    """Return False for gibberish / one-liners not worth an LLM call."""
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    words = re.findall(r"[A-Za-z]{3,}", stripped)
    if not words:
        return False
    if len(stripped) > 0 and sum(1 for ch in stripped if ch.isalpha()) / len(stripped) < 0.35:
        return False
    # Repeated single word / character spam: "asdfasdfasdf", "lllll"
    return not (len(set(words)) == 1 and len(words) > 4)

_SELF_IMPROVEMENT_RE = re.compile(
    r"(?:"
    r"\bwould help you\b"
    r"|\bcould you use\b"
    r"|\bif i added\b"
    r"|\bas your developer\b"
    r"|\bwhat do you need\b"
    r"|\bhow (?:could|can) (?:you improve|i improve you|i make you better)\b"
    r"|\bwhat would make you better\b"
    r"|\bwhat (?:capabilit\w*|features|tools|data) (?:should|could) i add\b"
    r"|\bwhat (?:capabilit\w*|features|tools|data) could you use\b"
    r"|\b(?:capabilit\w*|features|tools|data) you could use\b"
    r"|\bwhat (?:would|could) (?:help|improve|make) you\b"
    r"|\bwhat (?:could|should) i add\b"
    r"|\bto perform better\b"
    r"|\bperform your job better\b"
    r"|\bdo your job better\b"
    r"|\bimprove (?:yourself|the bot)\b"
    r")",
    re.IGNORECASE,
)

def _is_greeting_or_thanks(text: str) -> bool:
    """Check if the message is a greeting or thanks (canned reply worthy)."""
    return bool(
        re.search(
            r"\b(hello|hi|hey|yo|sup|howdy|good morning|good evening|gm|gn|"
            r"what'?s up|ping|wake up|"
            r"thank|thanks|ty|thx|appreciate|good bot|nice|great)\b",
            text.lower(),
        )
    )


def _is_capability_question(text: str) -> bool:
    """Check if the message asks about bot capabilities (canned reply worthy).

    Stays intentionally BROAD — it is the gate that routes both the command
    menu AND the self-improvement answer to the instant canned path. The
    distinction between "what can you do" (→ menu) and "what would help you"
    (→ self-improvement wishlist) is made by :func:`_is_self_improvement_question`
    + rule order inside :func:`local_ai_response`, not by this gate.
    """
    return bool(
        re.search(
            r"\b(help|can you|capabilities|features|commands|what do|what are|"
            r"who are you|who created you|who made you|who built you|who "
            r"developed you|who runs you|who is your creator|who is behind you|"
            r"tell me about you|about you|about this bot|"
            r"introduce yourself|describe yourself|what can you do|what else can you|"
            r"show me what you|guide|tutorial|docs|documentation)\b",
            text.lower(),
        )
    )


def _is_self_improvement_question(text: str) -> bool:
    """True when the user asks what the BOT needs / could add to do better.

    Distinct from :func:`_is_capability_question`: "what can you do" is a
    menu question, but "what would help you / what capabilities could I add /
    as your developer" is a self-improvement question answered with the
    specific wishlist instead of the command menu.
    """
    return bool(_SELF_IMPROVEMENT_RE.search(text))


def _is_deep_question(text: str) -> bool:
    """Detect analysis/report requests that deserve a longer, deeper answer.

    Short queries get a snappy 1-2s answer; deep ones get up to 1200 tokens
    and a structured-analysis system prompt.
    """
    low = text.lower()
    if len(low.split()) > 40:  # long multi-part question → deep
        return True
    return bool(
        re.search(
            r"\b(analyze|analysis|explain|explain how|why|how does|how do|compare|"
            r"risk|risks|detail|detailed|technical|report|investigate|research|"
            r"break down|walk me through|what happens if|is it safe|should i|"
            r"how to|step by step|deep dive|tokenomics|audit|vulnerabilit)\b",
            low,
        )
    )

__all__ = [
    "MAX_ADDRESS_HINTS",
    "_extract_addresses",
    "_is_capability_question",
    "_is_deep_question",
    "_is_greeting_or_thanks",
    "_is_meaningful_question",
    "_is_self_improvement_question",
]
