"""RAG scam check — vector-similarity scam detection using ChromaDB + fallback.

Queries the RMI ChromaDB security context (seeded with known scam patterns)
for semantic similarity to the token's name, symbol, and deployer profile.
Returns a scam probability score and matched patterns.

Architecture:
  1. Try ChromaDB via rmi-web3-security-context interface (primary)
  2. Fallback: pattern-match known scam name/symbol templates
  3. Final fallback: return neutral (no signal)
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Known scam indicator patterns (name/symbol based) ──────────────
SCAM_NAME_INDICATORS: list[str] = [
    "airdrop",
    "claim",
    "reward",
    "giveaway",
    "bonus",
    "free",
    "promo",
    "presale",
    "mint",
    "official",
    "verified",
    "sale",
    "launchpad",
    "ido",
    "igo",
]

SCAM_SYMBOL_PATTERNS: list[tuple[str, str]] = [
    ("SAFU", "Mimics Binance SAFU fund"),
    ("SHIB", "Mimics SHIB"),
    ("PEPE", "Mimics PEPE"),
    ("DOGE", "Mimics DOGE"),
    ("ETH", "Mimics Ethereum"),
    ("BTC", "Mimics Bitcoin"),
    ("SOL", "Mimics Solana"),
    ("BSC", "Mimics BSC"),
    ("BNB", "Mimics BNB"),
    ("USDT", "Mimics USDT"),
    ("USDC", "Mimics USDC"),
    ("DAI", "Mimics DAI"),
]


async def _score_by_pattern(name: str, symbol: str) -> dict[str, Any]:
    """Score token by name/symbol pattern matching.

    Returns a dict with risk_score (0-100), matched_indicators, and
    whether the name looks suspicious.
    """
    risk_score = 0
    matched_indicators: list[str] = []
    name_lower = (name or "").lower()
    symbol_upper = (symbol or "").upper()

    # Check name for scammy keywords
    for indicator in SCAM_NAME_INDICATORS:
        if indicator in name_lower:
            risk_score += 10
            matched_indicators.append(f"name_contains_{indicator}")

    # Check symbol for known mimic targets
    for mimic_target, _description in SCAM_SYMBOL_PATTERNS:
        if symbol_upper == mimic_target:
            risk_score += 0  # exact match to a real token = clean
            break
        if symbol_upper.startswith(mimic_target) and len(symbol_upper) > len(mimic_target):
            risk_score += 25
            matched_indicators.append(f"mimics_{mimic_target}_with_suffix")
        leet = symbol_upper.replace("1", "I").replace("0", "O").replace("3", "E").replace("4", "A")
        if leet == mimic_target and symbol_upper != mimic_target:
            risk_score += 20
            matched_indicators.append(f"mimics_{mimic_target}_leetspeak")
        if leet.startswith(mimic_target) and len(leet) > len(mimic_target):
            risk_score += 15
            matched_indicators.append(f"mimics_{mimic_target}_leetspeak_suffix")

    return {
        "risk_score": min(100, risk_score),
        "matched_indicators": matched_indicators,
        "patterns_checked": len(SCAM_NAME_INDICATORS) + len(SCAM_SYMBOL_PATTERNS),
    }


async def check_scam(
    token_address: str,
    chain: str,
    symbol: str = "",
    name: str = "",
    deployer: str | None = None,
) -> dict[str, Any]:
    """Check token for scam indicators using RAG + pattern matching.

    Args:
        token_address: The token contract/mint address.
        chain: Blockchain identifier (solana, ethereum, etc.).
        symbol: Token symbol for pattern matching.
        name: Token name for pattern matching.
        deployer: Deployer address, if available.

    Returns:
        Dict with result status, risk_score, matched patterns, and source.
    """
    result: dict[str, Any] = {
        "check": "rag",
        "result": "scanned",
        "risk_score": 0,
        "matched_indicators": [],
        "source": "pattern_fallback",
    }

    # ── Phase 1: Try RAG vector search via rmi-rag-api ──
    try:
        from app.config import settings

        # Build query only from non-empty fields — avoids prefixes leaking "token:" with no value
        query_parts = []
        if symbol:
            query_parts.append(f"token:{symbol}")
        if name:
            query_parts.append(f"name:{name}")
        query_parts.append(token_address)
        query = " ".join(query_parts)
        if query:
            # Fail-fast RAG probe: the vector search is best-effort enrichment.
            # A slow/hanging rmi-rag-api (e.g. its embedding backend is down)
            # must not burn seconds of the scan budget — degrade to the
            # deterministic pattern fallback below within 2.5s.
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.get(
                    "http://rmi-rag-api:8012/api/v1/search",
                    params={"q": query, "collection": "scam_intel", "limit": 5},
                    headers={"X-API-Key": settings.rmi_rag_api_key},
                )
                resp.raise_for_status()
                payload = resp.json()
            vector_results = payload.get("results", [])
            if vector_results:
                avg_score = sum(float(r.get("similarity", 0) or 0) for r in vector_results) / len(vector_results)
                has_scam_match = any(
                    "scam" in (r.get("content", "") or "").lower()
                    or "rug" in (r.get("content", "") or "").lower()
                    or "honeypot" in (r.get("content", "") or "").lower()
                    for r in vector_results
                )
                if has_scam_match and avg_score > 0.7:
                    result["risk_score"] = min(100, int(avg_score * 100))
                    result["matched_indicators"] = [r.get("content", "")[:120] for r in vector_results[:3]]
                    result["source"] = "chromadb_vector"
                    result["vector_matches"] = len(vector_results)
                    result["result"] = "scanned"
                    return result

                result["vector_matches"] = len(vector_results)
                result["vector_avg_relevance"] = round(avg_score, 4)
    except ImportError:
        logger.debug("rag_search_not_available")
    except (
        AttributeError,
        ValueError,
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as e:
        # Loud, not debug: a silent fallback here means scam checks silently
        # lose their strongest signal. Mark the result as degraded so callers
        # can surface the loss of RAG coverage.
        logger.warning("vector_search_failed", error=str(e))
        result["degraded"] = True

    # ── Phase 2: Pattern matching on name/symbol ──
    if symbol or name:
        pattern_result = await _score_by_pattern(name, symbol)
        if pattern_result["risk_score"] > 0:
            result["risk_score"] = pattern_result["risk_score"]
            result["matched_indicators"] = pattern_result["matched_indicators"]
            result["source"] = "pattern_match"
            return result

    # ── Phase 3: Check deployer against known scam deployers ──
    if deployer:
        try:
            from app.spam_registry import get_spam_registry

            registry = get_spam_registry()
            dep_check = registry.check_token(deployer, chain=chain)
            if dep_check and dep_check.get("is_known_scam"):
                result["risk_score"] = min(100, result.get("risk_score", 0) + 40)
                result["matched_indicators"].append("deployer_in_spam_registry")
                result["source"] = "deployer_check"
        except ImportError:
            logger.debug("spam_registry_not_available")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("deployer_spam_check_failed", error=str(e))

    return result
