"""
Chain Pattern Detector — extracts scam patterns from chain-indexer ClickHouse data
and feeds them into the RAG pipeline as "known scam patterns" for vector matching.

Architecture:
  1. Query chain-indexer ClickHouse tables (evm_transfers, dex_events, deployer_events)
  2. Detect behavioral scam patterns:
     a. Deployer who created multiple scam tokens (deployer_events cross-referenced)
     b. Tokens with extreme holder concentration (evm_transfers holder analysis)
     c. Pump-and-dump: many small buys → one large sell (dex_events pattern)
     d. Addresses interacting with known mixer/tumbler contracts
  3. Convert each finding to structured text description
  4. Ingest into RAG via IngestionPipeline for future vector matching
  5. Called from scanner DEEP phase to enrich per-token analysis

Data Sources:
  - wallet_memory.evm_transfers — 73M+ ERC-20 transfers (chain-indexer)
  - wallet_memory.dex_events — 47M+ DEX swaps/mints/burns (chain-indexer)
  - wallet_memory.deployer_events — deployer → token mappings (chain-indexer)
"""

from __future__ import annotations

import os
from typing import Any

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)


# ── Known mixer / tumbler contract addresses ────────────────────────────
KNOWN_MIXERS: dict[str, list[str]] = {
    "ethereum": [
        "0x0000000000000000000000000000000000000001".lower(),  # placeholder
    ],
}


# ── Lazy ClickHouse client (same pattern as lite_chain_indexer.py) ───────────
_ch_client: Any = None

# ── ClickHouse error types (narrowed broad excepts) ───────────────────────
try:
    import clickhouse_driver.errors as ch_errors

    CH_ERRORS: tuple[type[Exception], ...] = (
        ch_errors.Error,
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
    )
except ImportError:
    CH_ERRORS = (ConnectionError, OSError, TimeoutError, ValueError)

# Client init additionally raises ImportError (missing package) and RuntimeError
CH_GET_CLIENT_ERRORS: tuple[type[Exception], ...] = (*CH_ERRORS, ImportError, RuntimeError)


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Chain pattern detector ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except CH_GET_CLIENT_ERRORS as e:
        logger.warning("Chain pattern detector ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ── Async wrapper ────────────────────────────────────────────────────────


async def _run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Chain name normalizer ────────────────────────────────────────────────

_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ═══════════════════════════════════════════════════════════════════════════
# PATTERN DETECTORS
# ═══════════════════════════════════════════════════════════════════════════


async def detect_deployer_clusters(
    deployer: str,
    chain: str = "",
    min_tokens: int = 3,
) -> dict[str, Any]:
    """Detect if a deployer has created multiple tokens (potential serial deployer).

    Queries deployer_events to find all tokens deployed by this address.
    If the deployer created >= min_tokens tokens across one or more chains,
    flags as a multi-deploy pattern.

    Returns dict with findings suitable for RAG ingestion.
    """
    result: dict[str, Any] = {
        "pattern": "deployer_cluster",
        "detected": False,
        "risk_score": 0,
        "tokens": [],
        "description": "",
        "chain": chain or "any",
    }

    deployer_lower = deployer.lower()
    if not deployer_lower or deployer_lower == "unknown":
        return result

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {"deployer": deployer_lower, "min_tokens": min_tokens}
        where_parts = ["deployer = %(deployer)s"]
        if chain:
            where_parts.append("chain = %(chain)s")
            params["chain"] = _normalize_chain(chain)

        try:
            where_clause = " AND ".join(where_parts)
            sql_parts = [
                "SELECT token_address, chain, block_number, block_timestamp, type",
                "FROM wallet_memory.deployer_events",
                "WHERE " + where_clause,
                "ORDER BY block_number DESC LIMIT 100",
            ]
            sql = " ".join(sql_parts)
            return list(client.execute(sql, params))
        except CH_ERRORS as e:
            logger.debug("deployer_cluster_query failed: %s", e)
            return []

    rows = await _run_sync(_query)
    if not rows or len(rows) < min_tokens:
        # Only flag if this deployer created a meaningful number of tokens
        if rows and len(rows) >= 2:
            # Even 2 tokens is notable — flag as low risk
            pass
        else:
            return result

    tokens = []
    for row in rows:
        tokens.append(
            {
                "token_address": row[0],
                "chain": row[1],
                "block_number": row[2],
                "block_timestamp": str(row[3]) if row[3] else "",
                "type": row[4],
            }
        )

    total_tokens = len(tokens)
    chains_used = list({t["chain"] for t in tokens})
    risk_score = min(70, total_tokens * 5)  # 5 pts per token, max 70

    description = (
        f"Deployer {deployer[:12]}... deployed {total_tokens} tokens "
        f"on {len(chains_used)} chain(s): {', '.join(chains_used)}. "
        f"High deployment count suggests possible serial deployer pattern. "
        f"Risk score: {risk_score}/100."
    )

    result["detected"] = total_tokens >= min_tokens
    result["risk_score"] = risk_score
    result["tokens"] = tokens[:10]  # Keep top 10
    result["total_tokens"] = total_tokens
    result["chains_used"] = chains_used
    result["description"] = description
    return result


async def detect_holder_concentration(
    token_address: str,
    chain: str,
    top_n: int = 10,
    concentration_threshold: float = 70.0,
) -> dict[str, Any]:
    """Detect extreme holder concentration via evm_transfers analysis.

    Queries the top N receiving addresses for the token. If the top N
    addresses account for > concentration_threshold % of all transfers,
    flags as concentrated holder base — possible price manipulation.

    Returns dict with findings.
    """
    result: dict[str, Any] = {
        "pattern": "holder_concentration",
        "detected": False,
        "risk_score": 0,
        "top_holders": [],
        "concentration_pct": 0.0,
        "description": "",
    }

    token_lower = token_address.lower()
    chain_norm = _normalize_chain(chain)
    if not token_lower or not chain_norm:
        return result

    def _query() -> dict[str, Any]:
        client = _get_ch_client()
        if client is None:
            return {"holders": [], "total": 0}

        params = {"addr": token_lower, "chain": chain_norm, "limit": top_n}

        try:
            # Get top holders by transfer count
            holder_rows = client.execute(
                """
                SELECT to_address, count() AS tx_count
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0000000000000000000000000000000000000000'
                  AND to_address != ''
                GROUP BY to_address
                ORDER BY tx_count DESC
                LIMIT %(limit)s
                """,
                params,
            )

            # Get total unique holders
            count_rows = client.execute(
                """
                SELECT uniq(to_address) AS total_holders
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                  AND to_address != '0000000000000000000000000000000000000000'
                  AND to_address != ''
                """,
                {"addr": token_lower, "chain": chain_norm},
            )

            total_holders = count_rows[0][0] if count_rows else 0

            # Get total transfer count
            total_rows = client.execute(
                """
                SELECT count() AS total
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(addr)s
                  AND chain = %(chain)s
                """,
                {"addr": token_lower, "chain": chain_norm},
            )
            total_tx = total_rows[0][0] if total_rows else 1

            return {
                "holders": holder_rows or [],
                "total_holders": total_holders,
                "total_tx": total_tx,
            }
        except CH_ERRORS as e:
            logger.debug("holder_concentration_query failed: %s", e)
            return {"holders": [], "total": 0}

    data = await _run_sync(_query)
    holders = data.get("holders", [])
    total_tx = data.get("total_tx", 1)
    total_holders = data.get("total_holders", 0)

    if not holders or total_holders < 5:
        return result

    # Calculate concentration: what % of transfers go to top N addresses
    top_count = sum(row[1] for row in holders[:top_n])
    concentration_pct = (top_count / total_tx) * 100

    is_concentrated = concentration_pct > concentration_threshold
    risk_score = 0
    if concentration_pct > 90:
        risk_score = 60
    elif concentration_pct > 80:
        risk_score = 45
    elif concentration_pct > 70:
        risk_score = 30
    elif concentration_pct > 50:
        risk_score = 15

    top_holders_list = [{"address": row[0], "tx_count": row[1]} for row in holders[:10]]

    description = (
        f"Token {token_address[:12]}... on {chain_norm}: top {top_n} addresses "
        f"account for {concentration_pct:.1f}% of {total_tx} transfers "
        f"({total_holders} unique holders). "
        f"{'HIGH concentration — possible price manipulation.' if is_concentrated else 'Moderate holder distribution.'}"
    )

    result["detected"] = is_concentrated
    result["risk_score"] = risk_score
    result["top_holders"] = top_holders_list
    result["concentration_pct"] = round(concentration_pct, 1)
    result["total_holders"] = total_holders
    result["description"] = description
    return result


async def detect_pump_and_dump(
    token_address: str,
    chain: str,
    time_window_hours: int = 24,
) -> dict[str, Any]:
    """Detect pump-and-dump pattern via dex_events analysis.

    Look for: many small buys → one large sell within time_window.
    Pattern: >50 small swaps (buy side) followed by a single whale sell.

    Returns dict with findings.
    """
    result: dict[str, Any] = {
        "pattern": "pump_and_dump",
        "detected": False,
        "risk_score": 0,
        "small_buys": 0,
        "large_sells": 0,
        "description": "",
    }

    token_lower = token_address.lower()
    chain_norm = _normalize_chain(chain)
    if not token_lower or not chain_norm:
        return result

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params = {
            "addr0": token_lower,
            "addr1": token_lower,
            "chain": chain_norm,
            "limit": 200,
        }

        try:
            return list(
                client.execute(
                    """
                    SELECT type, block_timestamp, tx_hash, dex, version,
                           token0, token1, sender, recipient,
                           amount0_in, amount1_in, amount0_out, amount1_out,
                           amount0, amount1, whale
                    FROM wallet_memory.dex_events
                    WHERE chain = %(chain)s
                      AND (token0 = %(addr0)s OR token1 = %(addr1)s)
                      AND type = 'swap'
                    ORDER BY block_number DESC
                    LIMIT %(limit)s
                    """,
                    params,
                )
            )
        except CH_ERRORS as e:
            logger.debug("pump_dump_query failed: %s", e)
            return []

    rows = await _run_sync(_query)
    if not rows or len(rows) < 10:
        return result

    # Classify: small buys = non-whale, large sells = whale-flagged
    small_buys = 0
    large_sells = 0
    for row in rows:
        is_whale = bool(row[15]) if len(row) > 15 else False
        # The event involves this token
        token_is_token0 = (row[9] or "").lower() == token_lower  # token0

        if is_whale:
            # Whale flagged — check if it's selling (outflow of our token)
            if token_is_token0:
                # token0 is being sold: amount0_out (v2) or negative amount0 (v3)
                amt_out = row[12]  # amount0_out
                if amt_out and int(amt_out) > 0:
                    large_sells += 1
            else:
                # token1 is being sold: amount1_out (v2) or negative amount1 (v3)
                amt_out = row[13]  # amount1_out
                if amt_out and int(amt_out) > 0:
                    large_sells += 1
        else:
            # Non-whale = small buy
            small_buys += 1

    is_pump_dump = large_sells >= 1 and small_buys > large_sells * 10
    risk_score = 0
    if is_pump_dump:
        ratio = small_buys / max(large_sells, 1)
        if ratio > 100:
            risk_score = 60
        elif ratio > 50:
            risk_score = 45
        elif ratio > 20:
            risk_score = 30
        else:
            risk_score = 15

    description = (
        f"Token {token_address[:12]}... on {chain_norm}: "
        f"{small_buys} small buys vs {large_sells} large sells in recent swaps. "
        f"{'PUMP & DUMP pattern detected!' if is_pump_dump else 'No significant pump-and-dump pattern.'}"
    )

    result["detected"] = is_pump_dump
    result["risk_score"] = risk_score
    result["small_buys"] = small_buys
    result["large_sells"] = large_sells
    result["buy_sell_ratio"] = round(small_buys / max(large_sells, 1), 1)
    result["description"] = description
    return result


async def detect_mixer_interactions(address: str, chain: str) -> dict[str, Any]:
    """Check if address has interacted with known mixer/tumbler contracts.

    Queries evm_transfers for transfers to/from known mixer addresses.

    Returns dict with findings.
    """
    result: dict[str, Any] = {
        "pattern": "mixer_interaction",
        "detected": False,
        "risk_score": 0,
        "mixer_txns": [],
        "description": "",
    }

    addr_lower = address.lower()
    chain_norm = _normalize_chain(chain)
    if not addr_lower or not chain_norm:
        return result

    mixers = KNOWN_MIXERS.get(chain_norm, [])
    if not mixers:
        return result

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params = {"addr": addr_lower, "chain": chain_norm}

        try:
            return list(
                client.execute(
                    """
                    SELECT tx_hash, block_number, block_timestamp,
                           from_address, to_address, value
                    FROM wallet_memory.evm_transfers
                    WHERE chain = %(chain)s
                      AND (from_address = %(addr)s OR to_address = %(addr)s)
                      AND (from_address IN %(mixers)s OR to_address IN %(mixers)s)
                    ORDER BY block_number DESC
                    LIMIT 20
                    """,
                    {**params, "mixers": tuple(mixers)},
                )
            )
        except CH_ERRORS as e:
            logger.debug("mixer_query failed: %s", e)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return result

    mixer_txns = []
    for row in rows:
        mixer_txns.append(
            {
                "tx_hash": row[0],
                "block_number": row[1],
                "block_timestamp": str(row[2]) if row[2] else "",
                "from": row[3],
                "to": row[4],
                "value": row[5],
            }
        )

    description = (
        f"Address {address[:12]}... interacted with known mixer contracts "
        f"on {chain_norm}: {len(mixer_txns)} transaction(s) detected. "
        f"This is a strong privacy/anonymity signal."
    )

    result["detected"] = True
    result["risk_score"] = 40  # Mixer interaction = inherently risky
    result["mixer_txns"] = mixer_txns[:5]
    result["description"] = description
    return result


async def detect_all_patterns(
    token_address: str,
    chain: str,
    deployer: str | None = None,
) -> list[dict[str, Any]]:
    """Run all pattern detectors and return findings.

    Args:
        token_address: Token contract/mint address.
        chain: Blockchain identifier.
        deployer: Optional deployer address for deployer clustering.

    Returns:
        List of pattern detection results (each with pattern, detected, risk_score, description).
    """
    findings: list[dict[str, Any]] = []

    # 1. Holder concentration (always run)
    concentration = await detect_holder_concentration(token_address, chain)
    if concentration["detected"]:
        findings.append(concentration)

    # 2. Pump and dump (always run)
    pump_dump = await detect_pump_and_dump(token_address, chain)
    if pump_dump["detected"]:
        findings.append(pump_dump)

    # 3. Deployer clusters (if deployer available)
    if deployer and deployer != "unknown":
        cluster = await detect_deployer_clusters(deployer, chain)
        if cluster["detected"]:
            findings.append(cluster)

    # 4. Mixer interactions (if deployer available)
    if deployer and deployer != "unknown":
        mixer = await detect_mixer_interactions(deployer, chain)
        if mixer["detected"]:
            findings.append(mixer)

    return findings


# ═══════════════════════════════════════════════════════════════════════════
# RAG INGESTION
# ═══════════════════════════════════════════════════════════════════════════


async def ingest_patterns_into_rag(findings: list[dict[str, Any]]) -> int:
    """Publish detected patterns into the RAG threat-intel pipeline via Redis.

    DEPRECATED (v3): the legacy RAG HTTP facade (``RAG_API =
    http://localhost:8000/api/v1/rag``) was removed — ``/ingest`` and friends
    no longer exist. Scanner engines now publish high-risk findings to the
    ``rmi:rag:ingest`` pub/sub channel and the ``rmi:rag:findings_queue``
    LPUSH list — the same channel used by
    ``app.domains.scanner.unified.persistence.publish_rag_finding`` and
    ``app.domains.scanner.service.scoring``. rmi-rag's subscriber embeds
    these into the Qdrant ``threat_intel`` collection.

    Args:
        findings: List of pattern detection results from detect_all_patterns.

    Returns:
        Number of findings published (0 on Redis failure).
    """
    if not findings:
        return 0

    try:
        import json as _json

        from redis.exceptions import RedisError

        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            logger.warning("RAG Redis unavailable — skipping chain pattern publish")
            return 0

        published = 0
        for finding in findings:
            risk_score = int(finding.get("risk_score", 0) or 0)
            risk_level = "critical" if risk_score >= 70 else ("high" if risk_score >= 40 else "medium")
            payload = {
                "token_address": finding.get("token_address", "") or finding.get("address", ""),
                "chain": finding.get("chain", "any"),
                "name": "",
                "symbol": "",
                "safety_score": max(0.0, 100.0 - float(risk_score)),
                "confidence": 0.0,
                "risk_level": risk_level,
                "risk_flags": [str(finding.get("description", "") or finding.get("pattern", "unknown"))],
                "green_flags": [],
                "price_usd": None,
                "liquidity_usd": None,
                "age_hours": None,
                "scanned_at": "",
                "modules_run": ["chain_pattern_detector"],
                "pattern_type": finding.get("pattern", "unknown"),
                "category": "onchain_pattern",
                "severity": "high" if risk_score >= 40 else "medium",
            }
            payload_json = _json.dumps(payload)
            await r.publish("rmi:rag:ingest", payload_json)
            await r.lpush("rmi:rag:findings_queue", payload_json)
            published += 1

        logger.info(
            "chain_patterns_ingested",
            published=published,
            total=len(findings),
        )
        return published

    except (RedisError, ConnectionError, OSError, TimeoutError, ImportError) as e:
        logger.warning("RAG ingestion unavailable for chain patterns: %s", e)
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL API
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_and_ingest(
    token_address: str,
    chain: str,
    deployer: str | None = None,
) -> dict[str, Any]:
    """Full analysis: detect patterns → ingest into RAG → return findings.

    This is the primary entry point for the scanner pipeline.

    Args:
        token_address: Token contract/mint address.
        chain: Blockchain identifier.
        deployer: Optional deployer address.

    Returns:
        Dict with findings, total_risk_score, and ingestion stats.
    """
    # 1. Detect all patterns
    findings = await detect_all_patterns(token_address, chain, deployer)

    # 2. Compute aggregate risk score
    total_risk = sum(f.get("risk_score", 0) for f in findings)
    match_count = len(findings)

    # 3. Ingest high-confidence findings into RAG
    high_risk = [f for f in findings if f.get("risk_score", 0) >= 30]
    ingested = 0
    if high_risk:
        ingested = await ingest_patterns_into_rag(high_risk)

    # 4. Return results
    return {
        "check": "chain_pattern_detector",
        "result": "scanned" if findings else "clean",
        "match_count": match_count,
        "total_risk_score": min(100, total_risk),
        "findings": findings,
        "ingested_into_rag": ingested,
        "source": "lite_chain_indexer (ClickHouse)",
    }


# ── Backward-compatible alias ───────────────────────────────────────────
detect_and_ingest = analyze_and_ingest
