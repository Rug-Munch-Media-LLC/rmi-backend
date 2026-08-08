"""GoPlus security helpers — extracted from ``modules/security.py``.

Degodding wave 6 (2026-08-09): the E-rated ``_check_goplus_security``
orchestrator in ``security.py`` was inflating the module's complexity. The
three cohesive units extracted here are:

- ``_goplus_api_scan`` — the Tier 2 GoPlus HTTP call + defensive parsing,
  returning the full GoPlus-compatible payload (scanned or error) so the
  distinct error messages are preserved.
- ``_compute_goplus_risk_score`` — the pure 0-100 risk-score accumulator over
  the GoPlus flag set (honeypot, ownership, taxes, proxy, mintable, ...).
- ``_map_own_security_data`` — the Tier 1 own-ClickHouse → GoPlus-compatible
  output mapping.

``security.py`` keeps the public ``_check_goplus_security`` name (tests patch
it at module level) and orchestrates: own data first, GoPlus API fallback.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import logger


async def _goplus_api_scan(chain_id: str, token_address: str, chain: str) -> dict[str, Any]:
    """Call the GoPlus ``token_security`` endpoint for one token.

    Returns the full GoPlus-compatible payload: a ``result: "scanned"`` dict
    with risk flags when the token is indexed, or a ``result: "error"`` dict
    with a human-readable message. Distinct failure modes (HTTP status, invalid
    JSON, missing result, missing token) each produce their own error payload.
    """
    url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={"contract_addresses": token_address})
            if resp.status_code != 200:
                logger.debug("goplus_http_error", status=resp.status_code, chain=chain)
                return {"check": "goplus", "result": "error", "error": f"HTTP {resp.status_code}"}

            # Defensive JSON parsing
            try:
                data = resp.json()
            except httpx.HTTPError:
                logger.debug("goplus_json_parse_failed", chain=chain)
                return {"check": "goplus", "result": "error", "error": "Invalid JSON response"}

            if not isinstance(data, dict):
                return {"check": "goplus", "result": "error", "error": "Unexpected response format"}

            result = data.get("result")
            if not isinstance(result, dict):
                # GoPlus returned data but no result for this token
                return {
                    "check": "goplus",
                    "result": "scanned",
                    "note": "Token not indexed by GoPlus",
                    "risk_score": 0,
                    "source": "goplus",
                }

            token_data = result.get(token_address.lower())
            if not isinstance(token_data, dict):
                return {"check": "goplus", "result": "error", "error": "Token not found in GoPlus"}

            return {
                "check": "goplus",
                "result": "scanned",
                "risk_score": min(100, max(0, _compute_goplus_risk_score(token_data))),
                "is_honeypot": token_data.get("is_honeypot") == "1",
                "is_open_source": token_data.get("is_open_source") == "1",
                "is_proxy": token_data.get("is_proxy") == "1",
                "is_mintable": token_data.get("is_mintable") == "1",
                "is_blacklisted": token_data.get("is_blacklisted") == "1",
                "is_whitelisted": token_data.get("is_whitelisted") == "1",
                "can_take_back_ownership": token_data.get("can_take_back_ownership") == "1",
                "hidden_owner": token_data.get("hidden_owner") == "1",
                "owner_address": token_data.get("owner_address", ""),
                "buy_tax": float(token_data.get("buy_tax", "0") or 0),
                "sell_tax": float(token_data.get("sell_tax", "0") or 0),
                "holder_count": int(token_data.get("holder_count", "0") or 0),
                "total_supply": token_data.get("total_supply", ""),
                "source": "goplus",
            }
    except httpx.TimeoutException:
        logger.debug("goplus_timeout", chain=chain)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as e:
        logger.warning("goplus_failed", token=token_address, error=str(e)[:100])
    return {"check": "goplus", "result": "error", "error": "GoPlus API unavailable"}


def _compute_goplus_risk_score(token_data: dict[str, Any]) -> int:
    """Accumulate the 0-100 risk score from GoPlus string flags.

    Each ``"1"`` flag adds its weight: honeypot (+50), blacklist (+40),
    selfdestruct (+30), take-back-ownership (+20), extreme tax (+20),
    closed-source (+15), high tax (+15), owner-change-balance (+15),
    hidden-owner (+15), proxy/mintable/pausable (+10), cooldown (+5);
    whitelist refunds (-10). Clamping to [0, 100] happens at the call site.
    """
    risk_score = 0
    is_honeypot = token_data.get("is_honeypot") == "1"
    if is_honeypot:
        risk_score += 50
    if token_data.get("can_take_back_ownership") == "1":
        risk_score += 20
    if token_data.get("is_open_source") == "0":
        risk_score += 15
    if token_data.get("is_blacklisted") == "1":
        risk_score += 40
    if token_data.get("is_whitelisted") == "1":
        risk_score -= 10
    buy_tax = float(token_data.get("buy_tax", "0") or 0)
    sell_tax = float(token_data.get("sell_tax", "0") or 0)
    if buy_tax > 10:
        risk_score += 15
    if sell_tax > 10:
        risk_score += 15
    if buy_tax > 50 or sell_tax > 50:
        risk_score += 20
    if token_data.get("is_proxy") == "1":
        risk_score += 10
    if token_data.get("is_mintable") == "1":
        risk_score += 10
    if token_data.get("owner_change_balance") == "1":
        risk_score += 15
    if token_data.get("hidden_owner") == "1":
        risk_score += 15
    if token_data.get("selfdestruct") == "1":
        risk_score += 30
    if token_data.get("transfer_pausable") == "1":
        risk_score += 10
    if token_data.get("trading_cooldown") == "1":
        risk_score += 5
    return risk_score


def _map_own_security_data(own_data: dict[str, Any]) -> dict[str, Any]:
    """Map an own-ClickHouse security payload to GoPlus-compatible output.

    The own engine returns ``risk_score`` / ``is_honeypot`` / tax percentages
    etc.; this renames them into the shape the rest of the scanner expects
    from GoPlus (``check: "goplus"``, ``source: "own_clickhouse"``).
    """
    return {
        "check": "goplus",
        "result": "scanned",
        "risk_score": own_data.get("risk_score", 0),
        "is_honeypot": own_data.get("is_honeypot", False),
        "is_mintable": own_data.get("is_mintable", False),
        "is_proxy": own_data.get("is_proxy", False),
        "is_blacklisted": False,
        "is_whitelisted": False,
        "can_take_back_ownership": False,
        "hidden_owner": False,
        "owner_address": own_data.get("owner_address", ""),
        "buy_tax": own_data.get("buy_tax_pct", 0),
        "sell_tax": own_data.get("sell_tax_pct", 0),
        "holder_count": own_data.get("holder_count", 0),
        "total_supply": own_data.get("total_supply", ""),
        "source": "own_clickhouse",
    }
