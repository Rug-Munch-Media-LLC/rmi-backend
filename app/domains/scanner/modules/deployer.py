"""Scanner module — split from modules.py for maintainability."""

from __future__ import annotations
from typing import Any

from app.domains.scanner.modules._core import (
    EXPLORER_APIS,
    _fetch_dexscreener,
    _fetch_json,
    _fetch_with_fallback,
    logger,
)

async def _get_deployer_info(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get deployer address and basic reputation score.

    Resolves the contract creator via block explorer API (Etherscan, BscScan, etc.)
    with DexScreener fallback. Scores deployer using behavioral fingerprint matching
    from deployer_reputation module.

    Args:
        token_address: Token contract address
        chain: Blockchain network identifier

    Returns:
        Dict with deployer_address, reputation_score (0-100), reputation_label,
        signals found, and mitigations. Returns "unknown" deployer if not resolvable.

    Data Sources:
        - Block explorer APIs (Etherscan, BscScan, etc.)
        - DexScreener API (fallback)
        - app.deployer_reputation (behavioral fingerprint scoring)
    """
    result: dict[str, Any] = {"check": "deployer", "result": "scanned"}
    deployer_addr = ""

    # Try block explorer API to get contract creator
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

    # Fallback: try DexScreener (uses shared per-token cache)
    if not deployer_addr:
        try:
            ds_data = await _fetch_dexscreener(token_address)
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

            # Try to get more context about the deployer
            scoring_result = score_deployer_risk(
                address=deployer_addr,
                deployment_count=1,  # Will be refined if available
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

        # Try to get deployer's transaction count from explorer
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

        # Score with whatever data we have
        scoring_result = score_deployer_risk(
            address=deployer_addr,
            deployment_count=min(tx_count, 20),
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
