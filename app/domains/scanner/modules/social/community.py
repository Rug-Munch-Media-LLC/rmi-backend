"""Community-related token checks — verification, ownership, age, helpers.

Split from social.py for maintainability.
Functions:
    _get_top_symbols: Return set of top 1000 crypto symbols.
    _levenshtein_distance: Compute Levenshtein distance between strings.
    _check_contract_verification: Check if contract source is verified.
    _check_ownership_renounced: Check if ownership is renounced.
    _check_token_age: Check token/pool age.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.domains.scanner.modules._core import (
    EXPLORER_APIS,
    GOPLUS_CHAIN_IDS,
    PREFER_OWN_DATA,
    _fetch_dexscreener,
    _fetch_json,
    _solana_rpc_call,
    logger,
)

# ── Helpers ─────────────────────────────────────────


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


# ── Contract Verification ──────────────────────────


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


# ── Token Age Check ─────────────────────────────────


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

    # ── Tier 1: Own DEX data (pool creation time) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_dex_data import get_own_dex_data

            own_dex = await get_own_dex_data(token_address, chain)
            if own_dex.get("source") == "own" and own_dex.get("pairs"):
                creation_times = []
                for pair in own_dex["pairs"]:
                    created = pair.get("pairCreatedAt", 0)
                    if created and created > 0:
                        creation_times.append(created / 1000 if created > 1e12 else created)

                if creation_times:
                    first_created = min(creation_times)
                    age_seconds = max(0, now - first_created)
                    age_hours = age_seconds / 3600
                    result["age_hours"] = round(age_hours, 1)
                    result["age_days"] = round(age_hours / 24, 1)
                    result["created_at"] = first_created
                    result["source"] = "own_dex_data"
                    _apply_age_risk(result, age_hours)
                    return result
        except ImportError:
            logger.debug("own_dex_data not available for token age")
        except Exception as exc:
            logger.debug("own_dex_age_failed", error=str(exc)[:60])

    # ── Tier 2: Own deployer_events (creation timestamp) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_security_analysis import get_own_security

            own_sec = await get_own_security(token_address, chain)
            if own_sec.get("source") == "own" and own_sec.get("age_hours") is not None:
                age_hours = own_sec["age_hours"]
                result["age_hours"] = round(age_hours, 1)
                result["age_days"] = round(age_hours / 24, 1)
                result["created_at"] = own_sec.get("deployment_block")
                result["source"] = "own_security"
                _apply_age_risk(result, age_hours)
                return result
        except ImportError:
            logger.debug("own_security_analysis not available for token age")
        except Exception as exc:
            logger.debug("own_security_age_failed", error=str(exc)[:60])

    # ── Tier 3: DexScreener API (via _fetch_dexscreener which tries own data first) ──
    try:
        ds_data = await _fetch_dexscreener(token_address, chain)
        if ds_data and ds_data.get("pairs"):
            creation_times = []
            for pair in ds_data["pairs"]:
                created = pair.get("pairCreatedAt", 0)
                if created and created > 0:
                    creation_times.append(created / 1000 if created > 1e12 else created)

            if creation_times:
                first_created = min(creation_times)
                age_seconds = max(0, now - first_created)
                age_hours = age_seconds / 3600
                result["age_hours"] = round(age_hours, 1)
                result["age_days"] = round(age_hours / 24, 1)
                result["created_at"] = first_created
                result["source"] = "dexscreener"
                _apply_age_risk(result, age_hours)
                return result
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("token_age_dexscreener_failed", error=str(e)[:60])

    # ── No data available ──
    result["result"] = "unavailable"
    return result


def _apply_age_risk(result: dict[str, Any], age_hours: float) -> None:
    """Apply risk classification based on token age in hours."""
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


# ── Ownership Renounced Check ──────────────────────


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

    # ── Source 1: Own security analysis (ClickHouse, zero cost) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_security_analysis import get_own_security

            own_sec = await get_own_security(token_address, chain)
            if own_sec.get("source") == "own" and own_sec.get("owner_address"):
                owner = own_sec.get("owner_address", "")
                is_renounced = not owner or owner == "0x0000000000000000000000000000000000000000"
                result["ownership_renounced"] = is_renounced
                result["owner_address"] = owner
                result["source"] = "own_security"
        except ImportError:
            logger.debug("own_security_analysis not available")
        except Exception as exc:
            logger.debug("own_security_ownership_failed", error=str(exc)[:60])

    # ── Source 2: GoPlus data (free API) ──
    if not result.get("source"):
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

    # ── Source 3: Solana — check mint authority ──
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

    # ── Source 4: HoneypotDetector fallback ──
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
        except ImportError as e:
            logger.debug(f"_check_ownership_renounced failed: {e}")
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
