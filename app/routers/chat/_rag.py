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
# ONLY real, populated Qdrant collections are fanned out. Phantom legacy
# names (``token_analysis``, ``wallet_profiles``, ``forensic_reports``) have
# no Qdrant collection — they would silently resolve to ``rag_documents``
# while the ``sources`` label claimed the requested name. Empty collections
# (``scanned_tokens``, ``whale_transfers``) return nothing and only waste
# concurrent requests. Those names are handled by
# ``app.rag._qdrant._COLLECTION_ALIASES`` when requested directly by other
# code paths (aliases land on the live corpus and hits are labeled with the
# ACTUAL resolved collection via ``metadata["resolved_collection"]``).
RAG_COLLECTIONS = [
    "threat_intel",  # unified scanner + SENTINEL high-risk findings (alias → threat_intel_v2, 11.9K pts, live)
    "scam_intel",  # legacy scam intelligence (alias → scam_intel_v2, 600 pts, live)
    "news_v2",  # news articles and market intelligence (9.6K pts, live)
    "defi_exploits",  # historical DeFi exploits / protocol hacks (13.6K pts, live)
    "coding_knowledge",  # code-level security knowledge (1.8K pts)
    "scam_patterns",  # scam pattern documentation + rug_timeline alias (9.9K pts, historical)
    "regulation_enforcement",  # regulatory actions + enforcement intel (16 pts, live)
    "wallet_v2",  # wallet labels, risk profiles, entity clusters (13.5K pts, live)
    "contract_audits",  # smart-contract audit findings (502 pts)
    "stablecoin_market",  # DeFiLlama stablecoin market caps (329 pts)
]
RAG_TOKEN_COLLECTIONS = ["scanner_v2"]  # scan-to-RAG memory (191 pts — added when a token address is present)
RAG_TOP_K = 10
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
    source quality. Truncates to ``max_chars`` total.
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
        block = f"[{i}] Source: {col} (score: {score:.3f})\n{snippet}"
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
# Analytical / forensic / contract-level questions ("why", "explain",
# "investigate", on-chain terms, honeypot/deployer/tokenomics, long
# messages) go to the stronger qwen-analysis 9B (Qwen3.5) model; simple /
# definitional / quick questions stay on the fast qwen-chat 4B (Qwen3.5).
# Tightened 2026-08-15 (kept in sync with the bot's _route_local_model in
# handlers/chat_ai/_llm.py): removed "rug"/"whale" term triggers so
# "riskiest/sketchiest ... rug potential" list asks stay on the fast 4B;
# added explicit contract/forensics terms ("contract", "deployer",
# "honeypot", "tokenomics", "audit") so genuine asks use the 9B.
_ANALYSIS_QUESTION_RE = re.compile(
    r"\b(?:why|explain|how does|compare|analyze|investigate|trace|what happened|deep|"
    r"contract|deployer|honeypot|tokenomics|audit)\b",
    re.IGNORECASE,
)
_ANALYSIS_TERMS = ("on-chain", "forensic", "mev", "exploit")
_ANALYSIS_LENGTH_THRESHOLD = 120


def _route_task(message: str) -> str:
    """Route ``message`` to task type ``analysis`` or ``chat``.

    Heuristics (pure function of the message text):
    - ``analysis`` when the message asks why/explain/how does/compare/analyze/
      investigate/trace/what happened/deep, or references on-chain/forensic/
      MEV/exploit terms, or names contract/forensics concepts (contract,
      deployer, honeypot, tokenomics, audit), or is long (>120 chars).
    - ``chat`` otherwise (simple, definitional, quick questions, risk-rank
      list asks like "riskiest picks of the day").
    """
    low = message.lower().strip()
    if not low:
        return "chat"
    if len(low) > _ANALYSIS_LENGTH_THRESHOLD:
        return "analysis"
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


def _cache_key(message: str) -> str:
    """Deterministic cache key: sha256 of the lowercased, whitespace-normalized message.

    Case- and whitespace-insensitive so "SOL price?", "SOL PRICE" and
    "  sol   price  " all hit the same entry.
    """
    normalized = _WHITESPACE_RE.sub(" ", message.lower().strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
