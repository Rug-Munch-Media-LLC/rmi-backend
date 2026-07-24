"""
Query Transformation Pipeline for RMI RAG System
===================================================
Improves search recall by transforming queries before retrieval.

Three strategies:
  1. HyDE -- Hypothetical Document Embedding (generate hypothetical answer, embed that)
  2. Query Expansion -- Generate variant phrasings, search all, merge with RRF
  3. Step-Back Prompting -- Abstract the query to a broader form, retrieve for both

All strategies have LLM-backed and rule-based fallback paths.
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger
from typing import Any

logger = get_logger(__name__)

# -- Redis config (same as rag_service) --
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
# Decode base64 LLM key if present, otherwise use plain LLM_API_KEY
if os.getenv("LLM_API_KEY_B64"):
    import base64 as _b64

    os.environ["LLM_API_KEY"] = _b64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
AI_BASE = LLM_BASE_URL if LLM_API_KEY else "https://openrouter.ai/api/v1/chat/completions"
AI_MODEL = os.getenv("QUERY_TRANSFORM_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-flash"))


# ======================================================================
# DATA MODEL
# ======================================================================


@dataclass
class TransformedQuery:
    original_query: str
    strategy: str  # "hyde" | "expand" | "step_back" | "none"
    transformed_queries: list[str]  # list of strings to embed & search
    metadata: dict = field(default_factory=dict)


# ======================================================================
# REDIS HELPER
# ======================================================================


async def _get_redis() -> Any:
    import redis.asyncio as ai_redis

    return ai_redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        db=0,
        decode_responses=True,
    )


# ======================================================================
# LLM HELPER
# ======================================================================


async def _call_llm(prompt: str, max_tokens: int = 512) -> str | None:
    """Call LLM chat API (DeepSeek primary, OpenRouter fallback). Returns None if unavailable."""
    if not LLM_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return None


# ======================================================================
# CRYPTO SYNONYM MAPPINGS (rule-based fallback for query expansion)
# ======================================================================

CRYPTO_SYNONYMS: dict[str, list[str]] = {
    "rug pull": ["honeypot", "token scam", "liquidity drain", "exit scam", "pull liquidity"],
    "wash trade": ["volume manipulation", "fake trading", "artificial volume", "self-trading"],
    "honeypot": ["rug pull", "sell-disabled token", "trapped buyers", "cant-sell token"],
    "pump and dump": ["pump dump", "price manipulation", "artificial inflation", "dump scheme"],
    "flash loan": [
        "instant loan",
        "uncollateralized loan",
        "flash loan attack",
        "atomic arbitrage",
    ],
    "sandwich attack": ["MEV attack", "front-running", "sandwich bot", "frontrun"],
    "money laundering": ["mixer", "tornado cash", "coin mixing", "layered transactions"],
    "sybil attack": ["fake identities", "multi-account", "sock puppet", "astroturfing"],
    "dusting attack": ["dust attack", "micro-deposit", "privacy violation", "address poisoning"],
    "reentrancy": ["recursive call", "reentrancy attack", "call-back vulnerability"],
    "front running": ["MEV", "sandwich", "transaction ordering", "priority gas auction"],
    "impersonation": ["phishing", "fake project", "clone scam", "counterfeit token"],
    "exit scam": ["rug pull", "abandonment", "vanishing act", "team disappearance"],
    "liquidity lock": ["locked liquidity", "LP lock", "liquidity freeze", "trustlock"],
    "token drain": ["wallet drainer", "approval scam", "sweep tokens", "drain contract"],
    "smart contract vulnerability": [
        "code exploit",
        "contract bug",
        "security flaw",
        "audit finding",
    ],
    "whale manipulation": ["whale dump", "large holder sell", "whale influence", "big wallet move"],
    "defi exploit": ["protocol hack", "vault drain", "yield farm exploit", "pool exploit"],
    "bridge exploit": ["cross-chain attack", "bridge hack", "relayer vulnerability"],
    "governance attack": [
        "dao takeover",
        "voting manipulation",
        "governance exploit",
        "flash loan governance",
    ],
}

# Token symbol pattern: $SYMBOL
_TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})")

# Category mappings for step-back
_TOKEN_CATEGORIES = {
    # Major chains
    "SOL": "Solana ecosystem token",
    "ETH": "Ethereum ecosystem token",
    "BTC": "Bitcoin ecosystem token",
    "BNB": "Binance Smart Chain token",
    "MATIC": "Polygon ecosystem token",
    "AVAX": "Avalanche ecosystem token",
    "ARB": "Arbitrum ecosystem token",
    "OP": "Optimism ecosystem token",
    "BASE": "Base ecosystem token",
}


# ======================================================================
# 1. HyDE -- HYPOTHETICAL DOCUMENT EMBEDDING
# ======================================================================


async def hyde_transform(query: str) -> str:
    """
    Generate a hypothetical answer document for the query.
    The hypothetical doc is what we'd *want* to retrieve.
    Cache results in Redis (key: hyde:{hash}, TTL 24h).
    """
    # Check Redis cache
    query_hash = hashlib.sha256(query.encode()).hexdigest()[:32]
    cache_key = f"hyde:{query_hash}"
    try:
        r = await _get_redis()
        cached = await r.get(cache_key)
        if cached:
            logger.debug(f"HyDE cache hit: {query[:60]}")
            return cached
    except (ConnectionError, OSError, TimeoutError):
        pass

    # Try LLM-based HyDE
    llm_result = await _call_llm(
        f"You are a cryptocurrency scam detection expert. Write a detailed, factual paragraph "
        f"that would be the ideal answer to this question. Include specific details, indicators, "
        f"and evidence that a real investigation report would contain.\n\n"
        f"Question: {query}\n\n"
        f"Ideal answer paragraph:",
        max_tokens=300,
    )

    if llm_result:  # noqa: SIM108
        result = llm_result
    else:
        # Rule-based fallback: prepend crypto-specific context
        result = _hyde_rule_based(query)

    # Cache in Redis (24h TTL)
    try:
        r = await _get_redis()
        await r.setex(cache_key, 86400, result)
    except (ConnectionError, OSError, TimeoutError):
        pass

    return result


def _hyde_rule_based(query: str) -> str:
    """Rule-based HyDE: prepend crypto investigation context."""
    query_lower = query.lower()

    prefixes = []
    if any(w in query_lower for w in ["rug", "pull", "drain", "liquidity"]):
        prefixes.append("Cryptocurrency rug pull investigation:")
    elif any(w in query_lower for w in ["honeypot", "can't sell", "sell disabled"]):
        prefixes.append("Honeypot token analysis reveals:")
    elif any(w in query_lower for w in ["wash", "volume", "fake trading"]):
        prefixes.append("Wash trading detection report:")
    elif any(w in query_lower for w in ["phishing", "drainer", "approval", "wallet drain"]):
        prefixes.append("Wallet drainer and approval scam investigation:")
    elif any(w in query_lower for w in ["mint", "unlimited", "supply", "dilution"]):
        prefixes.append("Unlimited mint vulnerability detection:")
    else:
        prefixes.append("Cryptocurrency scam detection analysis:")

    return f"{prefixes[0]} {query}"


# ======================================================================
# 2. QUERY EXPANSION
# ======================================================================


async def expand_query(query: str) -> list[str]:
    """
    Generate 3-5 variant phrasings of the same query.
    All variants will be searched and results merged with RRF.
    """
    # Try LLM-based expansion
    llm_result = await _call_llm(
        f"Generate 3 to 5 alternative phrasings of this crypto fraud detection query. "
        f"Each variant should use different terminology but seek the same information. "
        f"Return ONLY a JSON array of strings, nothing else.\n\n"
        f"Query: {query}\n\n"
        f"Alternative phrasings (JSON array):",
        max_tokens=256,
    )

    if llm_result:
        try:
            json_match = re.search(r"\[.*\]", llm_result, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, list) and len(parsed) >= 2:
                    return [query] + [str(v) for v in parsed[:5] if v]
        except Exception as e:
            logger.warning(f"Failed to parse expansion LLM output: {e}")

    # Rule-based fallback
    return _expand_rule_based(query)


def _expand_rule_based(query: str) -> list[str]:
    """Rule-based query expansion using crypto synonym mappings."""
    variants = [query]
    query_lower = query.lower()

    for key, synonyms in CRYPTO_SYNONYMS.items():
        if key in query_lower:
            added = 0
            for syn in synonyms:
                if syn.lower() not in query_lower and added < 3:
                    variant = re.sub(re.escape(key), syn, query, flags=re.IGNORECASE)
                    if variant != query and variant not in variants:
                        variants.append(variant)
                        added += 1

    # Also add a broader version if query mentions token symbol
    tokens = _TOKEN_RE.findall(query)
    if tokens and len(variants) < 3:
        symbol = tokens[0].upper()
        category = _TOKEN_CATEGORIES.get(symbol, "cryptocurrency token")
        broader = re.sub(r"\$" + re.escape(symbol), category, query, flags=re.IGNORECASE)
        broader = re.sub(r"\?$", " indicators?", broader)
        if broader != query and broader not in variants:
            variants.append(broader)

    # Deduplicate case-insensitively
    seen = set()
    unique = []
    for v in variants:
        key = v.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique[:6]


# ======================================================================
# 3. STEP-BACK PROMPTING
# ======================================================================


async def step_back_query(query: str) -> str:
    """
    Generate a broader abstraction of the query.
    e.g. "Is $SOL a rug pull?" -> "What are indicators of a rug pull on Solana?"
    Retrieve for BOTH the original and the step-back query.
    """
    llm_result = await _call_llm(
        f"Given this specific cryptocurrency fraud detection query, generate a broader, "
        f"more general version of the question that would retrieve relevant background "
        f"knowledge. The broader question should focus on the CATEGORY or TYPE of issue "
        f"rather than the specific instance.\n\n"
        f"Specific query: {query}\n\n"
        f"Broader general query:",
        max_tokens=150,
    )

    if llm_result:
        return llm_result.strip()

    # Rule-based fallback
    return _step_back_rule_based(query)


def _step_back_rule_based(query: str) -> str:
    """Rule-based step-back: replace specific tokens with broader categories."""
    result = query
    chain_used = None

    # Replace $SYMBOL with "a token" and note the chain for context suffix
    tokens = _TOKEN_RE.findall(query)
    for symbol in tokens:
        symbol_upper = symbol.upper()
        _TOKEN_CATEGORIES.get(symbol_upper, "a cryptocurrency token")
        result = result.replace(f"${symbol}", "a token")
        result = result.replace(f"${symbol_upper}", "a token")
        chain_used = symbol_upper

    # Replace specific addresses with "an address"
    addr_pattern = re.compile(r"0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}")
    if addr_pattern.search(result):
        result = addr_pattern.sub("an address", result)

    # Replace specific percentages with general terms
    result = re.sub(r"\b\d+\.?\d*%\b", "significant percentage", result)

    # Broader phrasing replacements - make it generic
    broader_map = {
        "is this": "what are indicators of",
        "is it": "what are characteristics of",
        "check if": "how to detect",
        "detect if": "how to identify",
        "verify if": "how to verify",
        "is a token": "what are indicators of a token being",
        "is an address": "what are indicators of an address being",
    }
    result_lower = result.lower()
    for specific, broad in broader_map.items():
        if specific in result_lower:
            result = result_lower.replace(specific, broad, 1)
            if result:
                result = result[0].upper() + result[1:]
            break

    # Make it more question-like if it isn't already
    if not result.endswith("?") and "?" not in result:
        result = f"What are indicators of {result.lower()}?"

    # If we replaced a token symbol, add chain context
    if chain_used:
        chain_names = {
            "SOL": "Solana",
            "ETH": "Ethereum",
            "BTC": "Bitcoin",
            "BNB": "Binance Smart Chain",
            "MATIC": "Polygon",
            "AVAX": "Avalanche",
            "ARB": "Arbitrum",
            "OP": "Optimism",
            "BASE": "Base",
        }
        chain_name = chain_names.get(chain_used, chain_used)
        if chain_name.lower() not in result.lower():
            result = result.rstrip("?") + f" on {chain_name}?"

    return result


# ======================================================================
# 4. ROUTER -- auto-select the best strategy
# ======================================================================


async def transform_query(query: str, strategy: str = "auto") -> TransformedQuery:
    """
    Transform a query using the specified strategy.

    strategy:
      "auto"      -- pick the best strategy based on query characteristics
      "hyde"      -- Hypothetical Document Embedding
      "expand"    -- Query Expansion (multiple variant searches)
      "step_back" -- Step-Back Prompting (broader abstraction)
      "none"      -- no transformation, use query as-is

    Returns TransformedQuery with the list of strings to embed and search.
    """
    if strategy == "none":
        return TransformedQuery(
            original_query=query,
            strategy="none",
            transformed_queries=[query],
            metadata={"reason": "no transformation requested"},
        )

    if strategy == "auto":
        strategy = _auto_select_strategy(query)

    if strategy == "hyde":
        hypo_doc = await hyde_transform(query)
        return TransformedQuery(
            original_query=query,
            strategy="hyde",
            transformed_queries=[hypo_doc],
            metadata={"hypothetical_doc": hypo_doc[:200]},
        )

    elif strategy == "expand":
        variants = await expand_query(query)
        return TransformedQuery(
            original_query=query,
            strategy="expand",
            transformed_queries=variants,
            metadata={"variant_count": len(variants)},
        )

    elif strategy == "step_back":
        stepped = await step_back_query(query)
        return TransformedQuery(
            original_query=query,
            strategy="step_back",
            transformed_queries=[query, stepped],
            metadata={"step_back_query": stepped},
        )

    # Fallback: no transformation
    return TransformedQuery(
        original_query=query,
        strategy="none",
        transformed_queries=[query],
        metadata={"reason": f"unknown strategy: {strategy}"},
    )


def _auto_select_strategy(query: str) -> str:
    """
    Auto-select the best query transformation strategy based on query characteristics.

    Heuristics:
      - Short factual queries (< 6 words, no question marks) -> "none"
      - Ambiguous queries (broad terms, generic questions) -> "hyde"
      - Specific entity queries (addresses, token symbols) -> "expand"
      - Broad/exploratory queries (how/what/why + topic) -> "step_back"
    """
    q = query.strip()
    word_count = len(q.split())
    q_lower = q.lower()

    # Check for specific entities
    has_address = bool(re.search(r"0x[a-fA-F0-9]{20,}|[1-9A-HJ-NP-Za-km-z]{32,44}", q))
    has_token_symbol = bool(_TOKEN_RE.search(q))

    # Check for question patterns
    starts_with_question_word = q_lower.startswith(
        ("what", "how", "why", "is", "are", "can", "does", "do")
    )
    has_question_mark = "?" in q

    # Check for ambiguity
    broad_indicators = [
        "scam",
        "fraud",
        "suspicious",
        "safe",
        "legit",
        "trustworthy",
        "risk",
        "dangerous",
        "honest",
        "real",
    ]
    is_ambiguous = (
        any(w in q_lower for w in broad_indicators) and not has_address and not has_token_symbol
    )

    # Decision logic
    if has_address or has_token_symbol:
        return "expand"

    if is_ambiguous:
        return "hyde"

    if word_count < 6 and not has_question_mark and not starts_with_question_word:
        return "none"

    if starts_with_question_word or has_question_mark:
        return "step_back"

    return "none"
