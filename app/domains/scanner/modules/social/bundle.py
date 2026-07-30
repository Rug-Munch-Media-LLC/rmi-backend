"""Bundle detection and copycat checks.

Split from social.py for maintainability.
Functions:
    _check_copycat: Check if token is a copycat (ClickHouse + Levenshtein).
    _check_bundle: Check if token launch was bundled (ClickHouse + fallback).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    PREFER_OWN_DATA,
    _fetch_json,
    _solana_rpc_call,
    logger,
)
from app.domains.scanner.modules.holders import _get_holder_data
from app.domains.scanner.modules.social.community import (
    _get_top_symbols,
    _levenshtein_distance,
)


# ── Copycat Detection ──────────────────────────────


async def _check_copycat(
    symbol: str, name: str, token_address: str = "", chain: str = "", deployer: str = ""
) -> dict[str, Any] | None:
    """Check if token is a copycat — own ClickHouse data first, then Levenshtein.

    Tier 1: own_copycat_detect — queries ClickHouse deployer_events + RAG
    Tier 2: Static Levenshtein distance against top symbols (original behavior)

    Args:
        symbol: Token symbol (e.g., "PEPE")
        name: Token name (e.g., "Pepe the Frog")
        token_address: Token contract address
        chain: Blockchain network identifier
        deployer: Deployer wallet address

    Returns:
        Dict with result, match_type, similar_to, and risk assessment.

    Data Sources:
        - Own ClickHouse deployer_events (primary, zero cost)
        - RAG vector search (semantic similarity)
        - Static top-symbol list + Levenshtein (fallback)
    """
    # ── Tier 1: Own copycat detection from ClickHouse + RAG ──
    if PREFER_OWN_DATA and token_address and chain:
        try:
            from app.domains.scanner.engines.own_copycat_detect import get_own_copycat_analysis

            own_result = await get_own_copycat_analysis(
                token_address=token_address,
                chain=chain,
                token_symbol=symbol,
                token_name=name,
                deployer_address=deployer,
            )
            if own_result.get("source") == "own" and own_result.get("similar_tokens"):
                # Convert to the format expected by caller
                top_match = own_result["similar_tokens"][0] if own_result["similar_tokens"] else {}
                match_type = own_result.get("match_type", "similarity")

                if own_result["is_copycat"]:
                    copycat_of = own_result.get("copycat_of") or top_match.get("symbol", top_match.get("name", ""))
                    return {
                        "check": "copycat",
                        "result": "high_risk",
                        "similar_to": copycat_of,
                        "distance": top_match.get("distance", 0),
                        "score": top_match.get("score", 0),
                        "match_type": match_type,
                        "confidence": own_result["confidence"],
                        "similar_tokens": own_result["similar_tokens"][:5],
                        "source": "own_clickhouse",
                    }

                return {
                    "check": "copycat",
                    "result": "clean",
                    "match_type": "unique",
                    "confidence": 100,
                    "source": "own_clickhouse",
                }
        except ImportError:
            logger.debug("own_copycat_detect not available")
        except Exception as exc:
            logger.debug("own_copycat_detect_failed", error=str(exc)[:60])

    # ── Tier 2: Static Levenshtein distance (original behavior) ──
    top_symbols = _get_top_symbols()
    symbol_upper = symbol.upper() if symbol else ""

    if symbol_upper in top_symbols:
        return {"check": "copycat", "result": "clean", "match_type": "exact_symbol"}

    for top in top_symbols:
        if symbol_upper and _levenshtein_distance(symbol_upper, top) <= 2:
            return {
                "check": "copycat",
                "result": "high_risk",
                "similar_to": top,
                "distance": _levenshtein_distance(symbol_upper, top),
                "match_type": "symbol_similarity",
                "source": "static_top_symbols",
            }

    if name:
        name_upper = name.upper()
        for top in top_symbols:
            if top in name_upper or _levenshtein_distance(name_upper, top) <= 3:
                return {
                    "check": "copycat",
                    "result": "medium_risk",
                    "similar_to": top,
                    "match_type": "name_similarity",
                    "source": "static_top_symbols",
                }

    return {"check": "copycat", "result": "clean", "match_type": "unique", "source": "static_top_symbols"}


# ── Bundle Detection ────────────────────────────────


async def _check_bundle(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if token launch was bundled — own ClickHouse data first.

    Tier 1: own_bundle_detect — analyzes evm_transfers from ClickHouse
    Tier 2: bundle_detector (existing) — external API fallback

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with is_bundled flag, confidence, and all sub-scores.

    Data Sources:
        - Own ClickHouse evm_transfers (primary, zero cost)
        - bundle_detector module (fallback)
    """
    # ── Tier 1: Own ClickHouse bundle detection (zero cost) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_bundle_detect import get_own_bundle_analysis

            own_result = await get_own_bundle_analysis(token_address, chain)
            if own_result.get("source") == "own" and own_result.get("confidence", 0) > 0:
                logger.info(
                    "Using own bundle data for %s on %s (bundled: %s, confidence: %s%%)",
                    token_address[:16],
                    chain,
                    own_result["is_bundled"],
                    own_result["confidence"],
                )
                return {
                    "check": "bundle",
                    "result": "scanned",
                    **own_result,
                    "source": "own_clickhouse",
                }
            logger.debug(
                "Own bundle data insufficient for %s, falling back",
                token_address[:16],
            )
        except ImportError:
            logger.debug("own_bundle_detect not available")
        except Exception as exc:
            logger.debug("own_bundle_detect_failed", error=str(exc)[:60])

    # ── Tier 2: bundle_detector (existing fallback) ──
    try:
        from app.bundle_detector import get_bundle_detector

        detector = get_bundle_detector()

        holder_data = await _get_holder_data(token_address, chain)
        holders = []
        if holder_data and holder_data.get("top_holders"):
            for h in holder_data["top_holders"]:
                holders.append(
                    {
                        "address": h.get("address", ""),
                        "amount": h.get("percentage", 0) * 100,
                    }
                )

        transactions = []
        try:
            if chain == "solana":
                sig_data = await _solana_rpc_call("getSignaturesForAddress", [token_address, {"limit": 50}])
                if sig_data:
                    sigs = sig_data.get("result", [])
                    for sig in sigs[:20]:
                        transactions.append(
                            {
                                "signature": sig.get("signature", ""),
                                "slot": sig.get("slot", 0),
                                "timestamp": sig.get("blockTime", 0),
                            }
                        )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("bundle_tx_fetch_failed", error=str(e))

        bundle_result = await detector.detect(
            token_address=token_address,
            chain=chain,
            holders=holders if len(holders) >= 5 else None,
            transactions=transactions if len(transactions) >= 5 else None,
        )

        return {
            "check": "bundle",
            "result": "scanned",
            "is_bundled": bundle_result.is_bundled,
            "confidence": bundle_result.confidence,
            "risk_label": bundle_result.risk_label,
            "atomic_block_score": bundle_result.atomic_block_score,
            "common_funder_score": bundle_result.common_funder_score,
            "temporal_score": bundle_result.temporal_score,
            "distribution_anomaly_score": bundle_result.distribution_anomaly_score,
            "concentration_score": bundle_result.concentration_score,
            "top3_holder_pct": bundle_result.top3_holder_pct,
            "top10_holder_pct": bundle_result.top10_holder_pct,
            "holder_count": bundle_result.holder_count,
            "source": "bundle_detector",
        }
    except ImportError:
        logger.warning("bundle_detector_not_available")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("bundle_check_failed", error=str(e))

    return {"check": "bundle", "result": "error", "error": "Bundle detection unavailable"}
