"""Pure RAG / web-context / routing / cache-key helpers for the chat router.

Extracted from ``app/routers/chat/__init__.py`` during the de-godding round 5
pass: deterministic text helpers that build RAG queries, format context
blocks, route messages to model tiers and derive cache keys. No I/O — the
router keeps the async search/orchestration in place and re-exports every
name here so all importers and tests keep working against the same objects.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from ._prompts import (
    _EVM_ADDRESS_RE,  # re-exported for existing importers
    _SOLANA_ADDRESS_RE,  # re-exported for existing importers
    _SYMBOL_RE,  # re-exported for existing importers
)

__all__ = [
    "RAG_COLLECTIONS",
    "RAG_CONTEXT_MAX_CHARS",
    "RAG_HIT_MAX_CHARS",
    "RAG_SEARCH_TIMEOUT",
    "RAG_TOKEN_COLLECTIONS",
    "RAG_TOP_K",
    "TOOL_RESULT_LABEL",
    "TOOL_RESULT_MAX_CHARS",
    "WEB_CONTEXT_MAX_CHARS",
    "WEB_HIT_MAX_CHARS",
    "_MAX_TOKEN_HINTS",
    "_build_rag_query",
    "_cache_key",
    "_extract_token_hints",
    "_format_rag_context",
    "_format_web_context",
    "_inject_tool_result",
    "_is_market_query",
    "_route_task",
]

# ── RAG collections (searched for every message) ─────────────────────────
# FOCUSED KNOWLEDGE SET (consolidated 2026-08-15 — was 13 collections).
# Every fan-out collection costs ONE Infinity embed + ONE Qdrant search +
# ONE mandatory cross-encoder rerank per message, so the set is pruned to
# the intent space the chat/bot actually grounds on, with no redundancy.
# All 5 kept collections are verified populated AND searchable via the RAG
# facade (all 1024d bge-m3 cosine — a single query embedding serves them).
#
# KEPT:
#   security_patterns  — curated vuln patterns (408 pts; SWC registry,
#                        verdict/severity labels) — "is X vulnerable /
#                        how does the attack work" corpus.
#   codebase_reference — secure reference snippets from reputable libs
#                        (997 pts; verdict=clean) — "how to write it right".
#   defi_exploits      — historical exploit writeups / PoCs (13.6K pts).
#   contract_security  — verdict-labeled good/bad contract patterns (221 pts).
#   scam_patterns      — scam pattern docs + rug_timeline alias (9.9K pts);
#                        pattern/entry documentation, distinct from
#                        defi_exploits' incident writeups — both match bot
#                        intents ("rugpull / scam patterns" + "exploit
#                        write-ups"); cross-collection dups collapse in dedup.
#
# DROPPED (redundant / thin / data-class — not knowledge):
#   threat_intel (11.9K)        — live scanner findings, not knowledge;
#                                 token-address asks use scanner_v2 instead.
#   scam_intel (600)            — thin legacy intel; subsumed by scam_patterns.
#   news_v2 (9.6K)              — news/market intel; market asks skip the bot's
#                                 knowledge gate; current events via live web.
#   coding_knowledge (1.8K)     — generic code KB; SWC content duplicated in
#                                 security_patterns, snippets in codebase_reference.
#   regulation_enforcement (16) — too thin to matter.
#   wallet_v2 (13.5K)           — entity/risk-profile data, not knowledge.
#   contract_audits (502)       — audit stubs; overlaps contract_security.
#   audit_findings (10)         — too thin; incident class in defi_exploits.
#   stablecoin_market (329)     — market data; market asks skip the gate.
#
# Phantom legacy names (``token_analysis``, ``wallet_profiles``,
# ``forensic_reports``) have no Qdrant collection and empty collections
# (``scanned_tokens``, ``whale_transfers``) are never fanned out — they are
# handled by ``app.rag._qdrant._COLLECTION_ALIASES`` when requested directly
# by other code paths (hits labeled with the ACTUAL resolved collection via
# ``metadata["resolved_collection"]``).
RAG_COLLECTIONS = [
    "security_patterns",  # curated vuln patterns (408 pts; SWC registry, verdict/severity)
    "codebase_reference",  # secure reference snippets, reputable libs (997 pts)
    "defi_exploits",  # historical exploit writeups / PoCs (13.6K pts)
    "contract_security",  # verdict-labeled good/bad contract patterns (221 pts)
    "scam_patterns",  # scam pattern docs + rug_timeline alias (9.9K pts)
]
RAG_TOKEN_COLLECTIONS = ["scanner_v2"]  # scan-to-RAG memory (191 pts — added when a token address is present)
# Per-collection candidate cap. 5 collections x 5 = <=25 candidates → dedup →
# top-5 formatted (RAG_CONTEXT_MAX_CHARS binds long before the format count,
# so fewer hits per collection does not shrink the visible context). This
# halves the MANDATORY cross-encoder rerank's input load vs the old
# 13 collections x top 10 (130 → 25 candidate reranks per message).
RAG_TOP_K = 5
RAG_SEARCH_TIMEOUT = 5.0  # overall budget (seconds) — web chat stays snappy
RAG_CONTEXT_MAX_CHARS = 1800  # keeps the combined system context under the 2000-char cap
RAG_HIT_MAX_CHARS = 700  # per-hit snippet

# Token hints for targeted RAG queries: EVM addresses, Solana base58
# addresses (no 0/O/I/l chars), and $SYMBOL mentions. The regexes themselves
# live in ._prompts (shared with the vague-question guard).
_MAX_TOKEN_HINTS = 3


def _extract_token_hints(message: str) -> tuple[list[str], list[str]]:
    """Return (addresses, symbols) mentioned in the user message.

    Addresses are EVM ``0x...`` (40 hex) or Solana base58 (32-44 chars).
    Symbols are ``$TICKER`` mentions. Both are capped to ``_MAX_TOKEN_HINTS``
    so a single message cannot bloat the RAG query.
    """
    addresses: list[str] = []
    for m in _EVM_ADDRESS_RE.finditer(message):
        addresses.append(m.group(0))
        if len(addresses) >= _MAX_TOKEN_HINTS:
            break
    if len(addresses) < _MAX_TOKEN_HINTS:
        for m in _SOLANA_ADDRESS_RE.finditer(message):
            addr = m.group(0)
            if addr not in addresses:
                addresses.append(addr)
            if len(addresses) >= _MAX_TOKEN_HINTS:
                break
    symbols: list[str] = []
    for m in _SYMBOL_RE.finditer(message):
        sym = m.group(1)
        if sym not in symbols:
            symbols.append(sym)
        if len(symbols) >= _MAX_TOKEN_HINTS:
            break
    return addresses, symbols


def _build_rag_query(message: str, addresses: list[str], symbols: list[str]) -> str:
    """Build the RAG search query: the raw message plus explicit token hints."""
    query = message.strip()[:500]
    hints: list[str] = []
    if addresses:
        hints.append(f"token address {' '.join(addresses)}")
    if symbols:
        hints.append("token " + " ".join(f"${s}" for s in symbols))
    if hints:
        query = f"{query}\n{'; '.join(hints)}"
    return query[:600]


def _format_rag_context(hits: list[dict[str, Any]], max_chars: int = RAG_CONTEXT_MAX_CHARS) -> str:
    """Render top RAG hits as a compact, LLM-friendly context block.

    Each hit is labeled with its collection + score so the LLM can weigh
    source quality. When a hit carries a security label (``verdict`` from the
    labeled good/bad contract corpus, or a hit-level/metadata ``severity``),
    that label is appended so the model can distinguish secure-reference
    (verdict=good) from flagged (verdict=bad / severity=critical) evidence.
    Truncates to ``max_chars`` total.
    """
    parts: list[str] = []
    total = 0
    for i, h in enumerate(hits, 1):
        content = (h.get("content") or "").strip()
        if not content:
            continue
        snippet = content[:RAG_HIT_MAX_CHARS]
        col = h.get("collection") or "unknown"
        score = float(h.get("score") or 0.0)
        block = f"[{i}] Source: {col} (score: {score:.3f})"
        labels: list[str] = []
        metadata = h.get("metadata") or {}
        verdict = h.get("verdict") or metadata.get("verdict")
        if verdict:
            labels.append(f"verdict={verdict}")
        severity = h.get("severity") or metadata.get("severity")
        if severity:
            labels.append(f"severity={severity}")
        if labels:
            block += f" [{' | '.join(labels)}]"
        block += f"\n{snippet}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


# ── Live web search context formatting ──────────────────────────────────
WEB_CONTEXT_MAX_CHARS = 1800  # mirrors RAG_CONTEXT_MAX_CHARS
WEB_HIT_MAX_CHARS = 400  # per-result snippet


def _format_web_context(results: list[dict[str, str]], max_chars: int = WEB_CONTEXT_MAX_CHARS) -> str:
    """Render SearXNG hits as a compact "Web results (live)" context block.

    Each result is labeled with its index + title + URL + snippet so the LLM
    can attribute live claims. Truncates to ``max_chars`` total.
    """
    parts: list[str] = []
    total = 0
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (r.get("content") or "").strip()[:WEB_HIT_MAX_CHARS]
        block = f"[{i}] {title}\n{url}" if title else f"[{i}] {url}"
        if content:
            block += f"\n{content}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


# ── Complexity-based model routing (pure function of message text) ──────
# Analytical / forensic / contract-level questions go to the stronger
# qwen-analysis 9B (Qwen3.5) model; simple / definitional / quick questions
# stay on the fast qwen-chat 4B (Qwen3.5).
#
# Refined 2026-08-15 (kept in sync with the bot's _route_local_model in
# handlers/chat_ai/_llm.py): a DEFINITION ("what is X", "explain X",
# "define X", "how does X work") is NOT forensics. The 9B is reserved for a
# real analysis/forensics signal:
#   * an address/hash is present (EVM 0x…40/64-hex or Solana base58), or
#   * an explicit forensics term (analyze/investigate/trace/contract/
#     deployer/tokenomics/audit), or
#   * a non-definitional analysis ask ("why", "compare", "what happened",
#     "deep", "honeypot", on-chain/forensic/mev/exploit).
# Definitional questions with NONE of those signals stay on the fast 4B —
# "what is a honeypot token" was measured routing to the 9B for an 87.6s
# cold answer (31.5s slower than the 4B's 56.1s) for a definition. Also
# keeps "riskiest/sketchiest ... rug potential" list asks on the fast 4B
# (no "rug"/"whale" triggers).
_ADDRESS_OR_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{40,64}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# Explicit forensics/contract signals — analysis even inside a definition.
_ANALYSIS_FORENSIC_RE = re.compile(
    r"\b(?:analyze|investigate|trace|contract|deployer|tokenomics|audit)\b",
    re.IGNORECASE,
)
# Definitional questions — "what is / explain / define / how does X work":
# an educational ask, NOT forensics. Overrides the weaker keyword triggers
# below when no address/hash or forensics term is present.
_DEFINITIONAL_RE = re.compile(
    r"\b(?:what is|what's|what are|explain|define|how does|how do|how to|how is)\b",
    re.IGNORECASE,
)
# Remaining weak analysis triggers — analysis only when the message is NOT
# a definitional question (checked after _DEFINITIONAL_RE).
_ANALYSIS_QUESTION_RE = re.compile(
    r"\b(?:why|compare|what happened|deep|honeypot)\b",
    re.IGNORECASE,
)
_ANALYSIS_TERMS = ("on-chain", "forensic", "mev", "exploit")
_ANALYSIS_LENGTH_THRESHOLD = 120


def _route_task(message: str) -> str:
    """Route ``message`` to task type ``analysis`` or ``chat``.

    Heuristics (pure function of the message text):
    - ``analysis`` when the message carries a real analysis/forensics signal:
      an address/hash present (EVM/Solana), an explicit forensics term
      (analyze/investigate/trace/contract/deployer/tokenomics/audit), a
      non-definitional analysis ask ("why", "compare", "what happened",
      "deep", "honeypot", on-chain/forensic/mev/exploit), or is long
      (>120 chars).
    - ``chat`` otherwise — including definitional questions ("what is /
      explain / define / how does X work") with no address and no forensics
      term, and simple / quick / risk-rank list asks like "riskiest picks of
      the day".
    """
    low = message.lower().strip()
    if not low:
        return "chat"
    if len(low) > _ANALYSIS_LENGTH_THRESHOLD:
        return "analysis"
    if _ADDRESS_OR_HASH_RE.search(low):
        return "analysis"
    if _ANALYSIS_FORENSIC_RE.search(low):
        return "analysis"
    if _DEFINITIONAL_RE.search(low):
        return "chat"
    if _ANALYSIS_QUESTION_RE.search(low):
        return "analysis"
    if any(term in low for term in _ANALYSIS_TERMS):
        return "analysis"
    return "chat"


# ── Response cache keys (deterministic, case/whitespace-insensitive) ─────
_CACHE_MAX_ENTRIES = 256
_CACHE_TTL_MARKET = 60  # seconds — price/mcap/chart/now/today queries go stale fast
_CACHE_TTL_DEFAULT = 3600  # seconds — everything else
_MARKET_KEYWORDS = ("price", "mcap", "chart", "now", "today")

_WHITESPACE_RE = re.compile(r"\s+")


def _cache_key(message: str, *, grounding: str | None = None) -> str:
    """Deterministic cache key: sha256 of the lowercased, whitespace-normalized message.

    Case- and whitespace-insensitive so "SOL price?", "SOL PRICE" and
    "  sol   price  " all hit the same entry. When ``grounding`` is provided
    (the caller-supplied ``context_override.system_context`` — e.g. the
    Telegram bot's persona), it is folded into the digest so a cached answer
    can never ignore a DIFFERENT persona/RAG block, while repeated questions
    with the SAME grounding still cache-hit.
    """
    normalized = _WHITESPACE_RE.sub(" ", message.lower().strip())
    digest = hashlib.sha256(normalized.encode("utf-8"))
    if grounding:
        digest.update(b"\0grounding\0")
        digest.update(_WHITESPACE_RE.sub(" ", grounding.strip()).encode("utf-8"))
    return digest.hexdigest()


def _is_market_query(message: str) -> bool:
    """True for market-ish queries (price/mcap/chart/now/today) — shorter TTL."""
    low = message.lower()
    return any(k in low for k in _MARKET_KEYWORDS)


# ── Tool-use context injection ───────────────────────────────────────────
TOOL_RESULT_LABEL = "TOOL RESULT (live data):"
TOOL_RESULT_MAX_CHARS = 1200  # cap the injected tool block — keeps the prompt lean


def _inject_tool_result(system_context: str, tool_result: str | None) -> str:
    """Append the live tool-result block to ``system_context`` (or return it unchanged).

    The block is capped at ``TOOL_RESULT_MAX_CHARS`` chars so a verbose
    executor payload cannot blow the system-prompt budget.
    """
    if not tool_result or not tool_result.strip():
        return system_context
    extra = f"{TOOL_RESULT_LABEL}\n{tool_result.strip()[:TOOL_RESULT_MAX_CHARS]}"
    return f"{system_context}\n\n{extra}" if system_context else extra
