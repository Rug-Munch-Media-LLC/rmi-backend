"""Scanner module — split from modules.py for maintainability."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    EXPLORER_APIS,
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_json,
    _fetch_with_fallback,
    _solana_rpc_call,
    logger,
)
from app.domains.scanner.modules.holders import _get_holder_data


def _get_top_symbols() -> set[str]:
    """Get top 1000 symbols from Dune/Coingecko."""
    return {
        "ETH",
        "USDT",
        "USDC",
        "BTC",
        "SOL",
        "BNB",
        "XRP",
        "ADA",
        "DOGE",
        "MATIC",
        "DOT",
        "SHIB",
        "TRX",
        "AVAX",
        "UNI",
        "ATOM",
        "LINK",
        "BCH",
        "LTC",
        "NEAR",
        "AAVE",
        "APE",
        "AXS",
        "SAND",
        "MANA",
        "FIL",
        "ALGO",
        "FTM",
        "VET",
        "THETA",
        "XLM",
        "ICP",
        "HBAR",
        "EGLD",
        "STX",
        "KAS",
        "RUNE",
        "ARB",
        "OP",
        "INJ",
        "SEI",
        "SUI",
        "APT",
        "TIA",
        "PEPE",
        "FLOKI",
        "WIF",
        "BONK",
        "JUP",
        "RAY",
    }


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row: list[int] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = curr_row[j] + 1
            deletions = prev_row[j + 1] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


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


# ── 7. Bundle Detection ───────────────────────────────────────────────


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


# ── 8. Contract Verification ──────────────────────────────────────────


async def _check_contract_verification(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if contract source code is verified via block explorer or Sourcify."""
    result: dict[str, Any] = {
        "check": "verified",
        "result": "scanned",
        "is_verified": False,
        "source": "",
    }

    # Try block explorer first
    explorer_url = EXPLORER_APIS.get(chain)
    if explorer_url:
        try:
            resp_json = await _fetch_json(
                explorer_url,
                params={
                    "module": "contract",
                    "action": "getsourcecode",
                    "address": token_address,
                },
            )
            if resp_json and resp_json.get("status") == "1":
                source_data = resp_json["result"]
                if isinstance(source_data, list) and len(source_data) > 0:
                    source_info = source_data[0]
                    has_source = bool(source_info.get("SourceCode", ""))
                    result["is_verified"] = has_source
                    result["source"] = "explorer"
                    result["abi"] = bool(source_info.get("ABI", ""))
                    result["compiler_version"] = source_info.get("CompilerVersion", "")
                    result["optimization"] = source_info.get("OptimizationUsed", "0")
                    return result
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("explorer_verification_failed", error=str(e))

    # Fallback: Sourcify API
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, "")
        if chain_id:
            # Sourcify: check if contract is verified
            sourcify_url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{token_address}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(sourcify_url)
                    if resp.status_code == 200:
                        result["is_verified"] = True
                        result["source"] = "sourcify"
                        return result
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.debug("sourcify_check_failed", error=str(e))

            # Try Blockscout as another fallback
            if chain_id.isdigit():
                blockscout_url = f"https://eth.blockscout.com/api/v2/smart-contracts/{token_address}"
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(blockscout_url)
                        if resp.status_code == 200:
                            result["is_verified"] = True
                            result["source"] = "blockscout"
                            return result
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.debug("blockscout_check_failed", error=str(e))
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("sourcify_verification_failed", error=str(e))

    return result


# ── 9. Price Impact / Consensus ────────────────────────────────────────


async def _check_social_sentiment(token_address: str, chain: str, symbol: str = "") -> dict[str, Any] | None:
    """Check social sentiment — LunarCrush volume, dominance, sentiment score.

    Flags suspicious patterns:
      - High social volume but negative sentiment (paid shills)
      - Very low social volume for a "popular" token (bot-like)
      - Sudden spike in mentions without organic follow-through
    """
    result: dict[str, Any] = {
        "check": "social_sentiment",
        "result": "scanned",
        "social_volume": 0,
        "social_dominance": 0,
        "sentiment_score": 0,
        "mentions_24h": 0,
        "is_suspicious": False,
        "suspicion_reasons": [],
    }

    # Try LunarCrush via the existing connector
    try:
        from app.lunarcrush_connector import get_lunarcrush_connector

        lc = get_lunarcrush_connector()
        sentiment = await lc.get_sentiment(token_address)
        if sentiment:
            result["social_volume"] = sentiment.get("social_volume", 0)
            result["social_dominance"] = sentiment.get("social_dominance", 0)
            result["sentiment_score"] = sentiment.get("sentiment_score", 0)
            result["mentions_24h"] = sentiment.get("mentions_24h", 0)
            result["mentions_change_24h"] = sentiment.get("mentions_change_24h", 0)
            result["source"] = "lunarcrush"

            # Suspicion detection
            volume = result["social_volume"]
            mentions = result["mentions_24h"]
            sentiment_score = result["sentiment_score"]

            # High volume + negative sentiment = paid shills
            if volume > 100 and sentiment_score < -0.3:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("high_volume_negative_sentiment")

            # Very low mentions for a token with volume
            if mentions < 5 and volume > 10:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("low_mentions_relative_to_volume")

            # Extreme positive sentiment with no volume (bot-like)
            if sentiment_score > 0.8 and volume < 5:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("extreme_sentiment_no_volume")

    except ImportError:
        logger.debug("lunarcrush_connector_not_available")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("social_sentiment_failed", error=str(e)[:80])

    # Fallback: try RMI news sentiment endpoint for signal
    if not result.get("source"):
        try:
            from app.domains.x402.tools.news_tools import get_news_sentiment

            news_data = await get_news_sentiment(token_address, chain=chain)
            if news_data and news_data.get("articles", 0) > 0:
                result["news_articles"] = news_data.get("articles", 0)
                result["news_sentiment"] = news_data.get("sentiment", "neutral")
                result["source"] = "news_fallback"
        except ImportError:
            pass
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("news_sentiment_fallback_failed", error=str(e)[:60])

    # Score: suspicious activity adds risk
    risk_score = 0
    if result["is_suspicious"]:
        risk_score += 25 * len(result["suspicion_reasons"])
    result["risk_score"] = min(100, risk_score)

    return result


# ── 12. Token Age Check ───────────────────────────────────────────────


async def _check_token_age(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check how old the token/pool is.

    New tokens (< 24h) are higher risk.
    Very new (< 1h) are extremely high risk.
    Mature (> 7d) are lower risk.
    """
    result: dict[str, Any] = {
        "check": "token_age",
        "result": "scanned",
        "age_hours": None,
        "age_days": None,
        "created_at": None,
        "risk_level": "unknown",
        "is_new_token": False,
        "risk_score": 0,
    }

    now = time.time()

    # Try DexScreener for pool creation time
    try:
        data, _ = await _fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"])
        if data and data.get("pairs"):
            # Use the earliest pair creation time
            creation_times = []
            for pair in data["pairs"]:
                created = pair.get("pairCreatedAt", 0)
                if created and created > 0:
                    creation_times.append(created / 1000)  # ms → s

            if creation_times:
                first_created = min(creation_times)
                age_seconds = max(0, now - first_created)
                age_hours = age_seconds / 3600

                result["age_hours"] = round(age_hours, 1)
                result["age_days"] = round(age_hours / 24, 1)
                result["created_at"] = first_created
                result["source"] = "dexscreener"

                # Risk classification
                if age_hours < 1:
                    result["risk_level"] = "critical"
                    result["is_new_token"] = True
                    result["risk_score"] = 40
                elif age_hours < 24:
                    result["risk_level"] = "high"
                    result["is_new_token"] = True
                    result["risk_score"] = 25
                elif age_hours < 168:  # 7 days
                    result["risk_level"] = "medium"
                    result["risk_score"] = 10
                else:
                    result["risk_level"] = "low"
                    result["risk_score"] = 0
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("token_age_dexscreener_failed", error=str(e)[:60])

    # Fallback: GoPlus has creation info for some tokens
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data and goplus_data.get("result"):
            token_data = goplus_data["result"].get(token_address.lower(), {})
            if token_data:
                creator_time = token_data.get("creator_time", 0)
                if creator_time:
                    age_seconds = max(0, now - int(creator_time))
                    age_hours = age_seconds / 3600
                    result["age_hours"] = round(age_hours, 1)
                    result["age_days"] = round(age_hours / 24, 1)
                    result["created_at"] = int(creator_time)
                    result["source"] = "goplus"

                    if age_hours < 24:
                        result["risk_level"] = "high"
                        result["is_new_token"] = True
                        result["risk_score"] = 25
                    elif age_hours < 168:
                        result["risk_level"] = "medium"
                        result["risk_score"] = 10
                    else:
                        result["risk_level"] = "low"
                        result["risk_score"] = 0
                    return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("token_age_goplus_failed", error=str(e)[:60])

    # No data available
    result["result"] = "unavailable"
    return result


# ── 13. LP Concentration Check ─────────────────────────────────────────


async def _check_ownership_renounced(token_address: str, chain: str) -> dict[str, Any] | None:
    """Check if contract ownership has been renounced.

    Ownership renounced = good (can't be rugged by owner).
    Active ownership = higher risk (owner can mint, pause, blacklist).
    """
    result: dict[str, Any] = {
        "check": "ownership_renounced",
        "result": "scanned",
        "ownership_renounced": False,
        "owner_address": "",
        "is_proxy": False,
        "risk_score": 0,
        "risk_level": "unknown",
    }

    # ── Source 1: GoPlus data (best for EVM) ──
    try:
        chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
        goplus_data = await _fetch_json(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": token_address},
        )
        if goplus_data and goplus_data.get("result"):
            token_data = goplus_data["result"].get(token_address.lower(), {})
            if token_data:
                owner = token_data.get("owner_address", "")
                is_renounced = not owner or owner == "0x0000000000000000000000000000000000000000"
                result["ownership_renounced"] = is_renounced
                result["owner_address"] = owner or ""
                result["is_proxy"] = token_data.get("is_proxy") == "1"
                result["source"] = "goplus"

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("ownership_goplus_failed", error=str(e)[:60])

    # ── Source 2: Solana — check mint authority ──
    if chain == "solana" and not result.get("source"):
        try:
            data = await _solana_rpc_call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed"}],
            )
            if data:
                val = (data.get("result") or {}).get("value")
                if val:
                    parsed = (val.get("data") or {}).get("parsed", {})
                    if parsed.get("type") == "mint":
                        info = parsed.get("info", {})
                        mint_auth = info.get("mintAuthority")
                        is_renounced = mint_auth is None
                        result["ownership_renounced"] = is_renounced
                        result["owner_address"] = str(mint_auth) if mint_auth else ""
                        result["source"] = "solana_rpc"
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("ownership_solana_failed", error=str(e)[:60])

    # ── Source 3: HoneypotDetector fallback ──
    if not result.get("source"):
        try:
            from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector

            detector = HoneypotDetector()
            hp_result = await detector.analyze_token(token_address, chain=chain)
            if hp_result:
                is_renounced = hp_result.token.ownership_renounced
                result["ownership_renounced"] = is_renounced
                result["owner_address"] = hp_result.token.owner_address or ""
                result["is_proxy"] = hp_result.token.is_proxy
                result["source"] = "honeypot_detector"
        except ImportError:
            pass
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("ownership_honeypot_failed", error=str(e)[:60])

    # ── Risk scoring ──
    if result.get("ownership_renounced") is True:
        result["risk_level"] = "safe"
        result["risk_score"] = 0
    elif result.get("owner_address"):
        owner = result["owner_address"]
        if owner and owner != "0x0000000000000000000000000000000000000000":
            result["risk_level"] = "active_owner"
            result["risk_score"] = 30  # Owner can still manipulate
            if result.get("is_proxy"):
                result["risk_score"] = 50  # Proxy + active owner = very risky
                result["risk_level"] = "active_proxy_owner"
        else:
            result["ownership_renounced"] = True
            result["risk_level"] = "safe"
            result["risk_score"] = 0

    return result


# ── 15. Volume/Liquidity Ratio Check ──────────────────────────────────
