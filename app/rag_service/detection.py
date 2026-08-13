"""Scam pattern detection and external security analysis."""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.crypto_embeddings import (
    KNOWN_SCAM_PATTERNS,
    extract_contract_features,
    get_embedder,
)
from app.rag_service.cache import _get_redis
from app.rag_service.indexing import _preembed_scam_patterns
from app.rag_service.state import _pattern_cache, _pattern_cache_loaded

logger = get_logger(__name__)


async def detect_scam_patterns(token_data: dict[str, Any], threshold: float = 0.65) -> dict[str, Any]:
    """Compare a token against all known scam patterns."""
    embedder = await get_embedder()

    if not _pattern_cache_loaded:
        await _preembed_scam_patterns()

    code = token_data.get("contract_code", "")
    desc = token_data.get("description", "")
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "???")

    code_lower = code.lower() if code else ""
    quick_matches = []
    for pattern in KNOWN_SCAM_PATTERNS:
        if not pattern["code_snippets"]:
            continue
        hits = sum(1 for s in pattern["code_snippets"] if s.lower() in code_lower)
        if hits > 0:
            quick_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "quick_match_score": hits / len(pattern["code_snippets"]),
                    "matched_snippets": hits,
                }
            )

    token_result = await embedder.embed_token_scam(
        name=name,
        symbol=symbol,
        description=desc,
        contract_code=code,
        chain=token_data.get("chain", "solana"),
        metadata=token_data,
    )

    token_total = len(token_result.vector)
    if token_total > 128 + 64 + 64:
        token_sem_end = token_total - 128 - 64 - 64
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    elif token_total > 128:
        token_sem_end = token_total - 128
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    else:
        token_sem = token_result.vector
        token_code = [0.0] * 128

    deep_matches: list[dict[str, Any]] = []
    for pattern in KNOWN_SCAM_PATTERNS:
        pattern_name = pattern["name"]
        if pattern_name in _pattern_cache:
            pattern_result = _pattern_cache[pattern_name]
        else:
            pattern_result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            _pattern_cache[pattern_name] = pattern_result
            try:
                r = await _get_redis()
                await r.setex(
                    f"rag:pattern_cache:{pattern_name}",
                    86400 * 30,
                    json.dumps(
                        {
                            "vector": pattern_result.vector,
                            "dims": pattern_result.dims,
                            "model": pattern_result.model,
                            "head": pattern_result.head,
                        }
                    ),
                )
            except json.JSONDecodeError:
                pass

        pat_total = len(pattern_result.vector)
        if pat_total > 128:
            pat_sem_end = pat_total - 128
            pattern_sem = pattern_result.vector[:pat_sem_end]
        else:
            pattern_sem = pattern_result.vector

        min_sem = min(len(token_sem), len(pattern_sem))
        if min_sem == 0:
            continue
        sem_sim = embedder.cosine_similarity(token_sem[:min_sem], pattern_sem[:min_sem])

        pat_code_features = (
            extract_contract_features("\n".join(pattern.get("code_snippets", []))).tolist()
            if pattern.get("code_snippets")
            else [0.0] * 128
        )
        code_sim = embedder.cosine_similarity(token_code, pat_code_features) if any(pat_code_features) else 0.0

        combined = 0.7 * sem_sim + 0.3 * code_sim
        if combined >= threshold:
            deep_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "description": pattern["description"],
                    "similarity": round(combined, 4),
                    "semantic_sim": round(sem_sim, 4),
                    "code_sim": round(code_sim, 4),
                }
            )

    deep_matches.sort(key=lambda x: x["similarity"], reverse=True)
    return {
        "quick_matches": quick_matches,
        "deep_matches": deep_matches,
        "highest_threat": deep_matches[0]["pattern"] if deep_matches else "none",
        "threat_severity": deep_matches[0]["severity"] if deep_matches else "low",
    }


async def get_goplus_analysis(token_address: str, chain: str = "1") -> dict[str, Any]:
    """Run GoPlus Security API token risk check."""
    from app.scam_sources import GoPlusConnector

    result = await GoPlusConnector.check_token(chain, token_address)
    if not result:
        return {"error": "GoPlus check returned no data", "address": token_address, "chain": chain}
    try:
        from app.rag_service.indexing import ingest_document

        content = (
            f"GoPlus Security Scan [{chain}]: {token_address} - "
            f"honeypot={result.get('is_honeypot')}, buy_tax={result.get('buy_tax')}, "
            f"sell_tax={result.get('sell_tax')}, proxy={result.get('is_proxy')}, "
            f"blacklisted={result.get('is_blacklisted')}, "
            f"hidden_owner={result.get('hidden_owner')}, "
            f"risk_score={result.get('risk_score')}"
        )
        await ingest_document(
            collection="token_analysis",
            content=content,
            metadata={
                "source": "goplus",
                "address": token_address,
                "chain": chain,
                **result,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to ingest GoPlus result for {token_address}: {e}")
    return result
