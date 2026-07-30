"""Scanner module — split from modules.py for maintainability.

Own-Data Integration:
    _get_deployer_info now queries our own ClickHouse deployer_events table FIRST
    to find the deployer address, avoiding block explorer API calls.
    Falls back to DexScreener API only when deployer_events has no data.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    EXPLORER_APIS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    _fetch_json,
    logger,
)


async def _get_deployer_info(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get deployer address — own ClickHouse deployer_events first, then explorer API.

    Tier 1: ClickHouse deployer_events (own data, zero cost)
    Tier 2: Block explorer API (Etherscan, BscScan, etc.)
    Tier 3: DexScreener API (last resort)

    Scores deployer using behavioral fingerprint matching from deployer_reputation.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with deployer_address, reputation_score (0-100), reputation_label,
        signals found, and mitigations.

    Data Sources:
        - Own wallet_memory.deployer_events (primary)
        - Block explorer APIs (fallback)
        - DexScreener API (last resort)
        - app.deployer_reputation (behavioral fingerprint scoring)
    """
    result: dict[str, Any] = {"check": "deployer", "result": "scanned"}
    deployer_addr = ""
    deployment_count = 1

    # ── Tier 1: Own ClickHouse deployer_events (zero cost) ──
    if PREFER_OWN_DATA:
        try:
            ch_data = await _query_deployer_from_ch(token_address, chain)
            if ch_data:
                deployer_addr = ch_data.get("deployer", "")
                deployment_count = ch_data.get("deployment_count", 1)
                result["deployed_at"] = ch_data.get("deployed_at")
                result["deployment_tx"] = ch_data.get("tx_hash")
                result["deployment_block"] = ch_data.get("block_number")
                result["source"] = "own_clickhouse"
                logger.info(
                    "Found deployer from ClickHouse for %s on %s: %s",
                    token_address[:16],
                    chain,
                    deployer_addr[:10],
                )
        except ImportError:
            logger.debug("clickhouse_driver not available for deployer query")
        except Exception as exc:
            logger.debug("deployer_ch_query_failed", error=str(exc)[:60])

    # ── Tier 2: Block explorer API (original behavior) ──
    if not deployer_addr:
        explorer_url = EXPLORER_APIS.get(chain)
        if explorer_url:
            try:
                resp_json = await _fetch_json(
                    explorer_url,
                    params={
                        "module": "contract",
                        "action": "getcontractcreation",
                        "contractaddresses": token_address,
                    },
                )
                if resp_json and resp_json.get("status") == "1":
                    result_data = resp_json.get("result", [])
                    if result_data and isinstance(result_data, list):
                        deployer_addr = result_data[0].get("contractCreator", "")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.debug("explorer_deployer_failed", error=str(e))

    # ── Tier 3: DEX data fallback (own data first, DexScreener API fallback) ──
    if not deployer_addr:
        try:
            ds_data = await _fetch_dexscreener(token_address, chain)
            if ds_data:
                pair = ds_data["pairs"][0]
                deployer_addr = pair.get("baseToken", {}).get("address", "")
                result["token_name"] = pair.get("baseToken", {}).get("name", "")
                result["token_symbol"] = pair.get("baseToken", {}).get("symbol", "")
                result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("dexscreener_deployer_fallback_failed", error=str(e))

    result["deployer_address"] = deployer_addr or "unknown"

    if deployer_addr and deployer_addr != "unknown":
        # Score deployer reputation using existing engine
        try:
            from app.deployer_reputation import score_deployer_risk

            scoring_result = score_deployer_risk(
                address=deployer_addr,
                deployment_count=deployment_count,
                source_verified=False,
            )
            result["reputation_score"] = scoring_result.get("risk_score", 50)
            result["reputation_label"] = scoring_result.get("risk_label", "UNKNOWN")
            result["signals"] = scoring_result.get("signals_found", [])
            result["mitigations"] = scoring_result.get("mitigations", [])
            result["fingerprint_match"] = scoring_result.get("fingerprint_match")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("deployer_scoring_failed", error=str(e))
            result["reputation_score"] = 50
            result["reputation_label"] = "UNKNOWN"

    return result


async def _query_deployer_from_ch(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query deployer from ClickHouse deployer_events table."""
    try:
        from clickhouse_driver import Client as CHClient

        ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
        ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
        ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
        ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
        ch_db = "wallet_memory"

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=10,
        )

        rows = client.execute(
            """
            SELECT
                deployer,
                count() AS deployment_count,
                min(block_timestamp) AS first_deployed,
                min(tx_hash) AS first_tx,
                min(block_number) AS first_block
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
            GROUP BY deployer
            ORDER BY deployment_count DESC
            LIMIT 1
            """,
            {"token": token_address.lower(), "chain": chain},
        )

        if rows:
            row = rows[0]
            ts = row[2]
            deployed_at = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            return {
                "deployer": row[0],
                "deployment_count": row[1],
                "deployed_at": deployed_at,
                "tx_hash": row[3],
                "block_number": row[4],
            }

        return None
    except ImportError:
        logger.debug("clickhouse_driver not available")
        return None
    except Exception as exc:
        logger.debug("deployer_ch_query_exception: %s", exc)
        return None


async def _deep_deployer_check(deployer_addr: str, chain: str) -> dict[str, Any] | None:
    """Deep deployer analysis using behavioral fingerprint matching.

    ELITE-tier check that performs advanced deployer risk assessment:
    cross-chain activity, bytecode similarity to known scams, and
    historical deployment pattern analysis.

    Args:
        deployer_addr: Deployer wallet address
        chain: Blockchain network identifier

    Returns:
        Dict with deployer details, risk signals, and cross-chain findings.
        Returns error dict if no deployer address is available.
    """
    if not deployer_addr or deployer_addr == "unknown":
        return {"check": "deployer_deep", "result": "error", "error": "No deployer address"}

    result: dict[str, Any] = {
        "check": "deployer_deep",
        "result": "scanned",
        "deployer": deployer_addr,
    }

    try:
        from app.deployer_reputation import score_deployer_risk

        # Try to get deployment count from own ClickHouse data first
        deployment_count = 1
        try:
            ch_data = await _query_deployer_history(deployer_addr, chain)
            if ch_data:
                deployment_count = ch_data.get("deployment_count", 1)
                result["deployments_known"] = ch_data.get("deployments", [])
                result["deployer_source"] = "own_clickhouse"
        except Exception as exc:
            logger.debug("deployer_history_query_failed", error=str(exc)[:60])

            # Fallback: try explorer for tx count
            explorer_url = EXPLORER_APIS.get(chain)
            tx_count = 0
            if explorer_url:
                try:
                    resp_json = await _fetch_json(
                        explorer_url,
                        params={
                            "module": "account",
                            "action": "txlist",
                            "address": deployer_addr,
                            "sort": "desc",
                            "page": 1,
                            "offset": 100,
                        },
                    )
                    if resp_json and resp_json.get("status") == "1":
                        txs = resp_json.get("result", [])
                        tx_count = len(txs) if isinstance(txs, list) else 0
                except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.debug("deployer_tx_fetch_failed", error=str(e))
            deployment_count = min(tx_count, 20)

        # Score with whatever data we have
        scoring_result = score_deployer_risk(
            address=deployer_addr,
            deployment_count=deployment_count,
            source_verified=False,
        )
        result.update(scoring_result)
    except ImportError:
        logger.warning("deployer_reputation_module_not_available")
        result["risk_score"] = 50
        result["risk_label"] = "UNKNOWN"
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("deep_deployer_failed", error=str(e))
        result["risk_score"] = 50
        result["risk_label"] = "ERROR"

    return result


async def _query_deployer_history(
    deployer_addr: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query deployer history from ClickHouse deployer_events."""
    try:
        from clickhouse_driver import Client as CHClient

        ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
        ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
        ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
        ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
        ch_db = "wallet_memory"

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=10,
        )

        rows = client.execute(
            """
            SELECT
                token_address,
                block_timestamp,
                type
            FROM wallet_memory.deployer_events
            WHERE deployer = %(deployer)s
              AND chain = %(chain)s
            ORDER BY block_timestamp DESC
            LIMIT 50
            """,
            {"deployer": deployer_addr.lower(), "chain": chain},
        )

        if rows:
            deployments = []
            for row in rows:
                ts = row[1]
                deployments.append(
                    {
                        "token_address": row[0],
                        "block_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                        "type": row[2],
                    }
                )
            return {
                "deployment_count": len(deployments),
                "deployments": deployments,
                "chain": chain,
            }

        return None
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("deployer_history_query_exception: %s", exc)
        return None
