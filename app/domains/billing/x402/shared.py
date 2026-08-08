"""x402 shared primitives — constants, models, helpers used across tools.

Barrel file — re-exports from shared_data and shared_models for backward compat.
Also contains HTTP helpers, audit helpers, and payment recording stub.
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

import aiohttp
import httpx

from app.core.logging import get_logger

logger = get_logger("x402_tools")

# Caching shield
from app.caching_shield.service_mcp import (  # noqa: E402 — imported after logger init; kept below module header for readability
    get_service_mcp,
)
from app.caching_shield.tool_data import (  # noqa: E402 — imported after logger init; kept below module header for readability
    td,
)

_svc_mcp = get_service_mcp()

# Re-export data constants
from app.domains.billing.x402.shared_data import (  # type: ignore[attr-defined]  # noqa: E402 — module docstring precedes import block
    BUNDLES,
    FREE_APIS,
    FREE_RPCS,
    HUMAN_PAY_TO,
    HUMAN_PAYMENT_TOKENS,
    TOOL_ALIASES,
    _resolve_pay_to,
)

# Re-export models
from app.domains.billing.x402.shared_models import (  # noqa: E402 — module docstring precedes import block
    AuditRequest,
    BundleRequest,
    ClusterRequest,
    GenericRequest,
    HumanPaymentMethodsResponse,
    HumanPaymentRequest,
    InsiderRequest,
    MCPProxyRequest,
    MemeVibeRequest,
    MultiTokenRequest,
    SentimentRequest,
    SentinelModuleRequest,
    SentinelScanRequest,
    SmartMoneyAlphaRequest,
    SmartMoneyRequest,
    TokenRequest,
    URLRequest,
    WalletListRequest,
    WalletRequest,
)


async def fetch_with_fallback(
    urls: list[str],
    method: str = "GET",
    json_data: dict[str, Any] | None = None,
    timeout: int = 10,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Try multiple URLs in sequence. Returns (data, source_url) on first success.
    Falls back from primary to secondary to tertiary sources.
    """
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                if method == "GET":
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            data: dict[str, Any] = await resp.json()
                            return data, url
                elif method == "POST" and json_data:
                    async with session.post(
                        url,
                        json=json_data,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            data: dict[str, Any] = await resp.json()  # type: ignore[no-redef]
                            return data, url
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.debug(f"URL failed: {url} - {e}")
                continue
    return None, None


async def rpc_call(chain: str, method: str, params: list[Any]) -> Any:
    """Make JSON-RPC call to blockchain with fallback RPCs."""
    rpcs = FREE_RPCS.get(chain, FREE_RPCS.get("solana"))
    urls = [f"{rpc}" for rpc in rpcs]  # type: ignore[union-attr]
    payloads = [{"jsonrpc": "2.0", "id": 1, "method": method, "params": params} for _ in urls]

    async with aiohttp.ClientSession() as session:
        for url, payload in zip(urls, payloads, strict=False):
            try:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("result")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
                continue
    return None


async def _audit_solana(address: str) -> dict[str, Any]:
    """Full audit for Solana tokens with 5-layer fallback."""
    result = {"chain": "solana", "address": address, "sources_used": []}

    # Layer 1: DexScreener (free, no key)
    try:
        data, _src = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["dexscreener"] = {  # type: ignore[assignment]
                "price_usd": pair.get("priceUsd", 0),
                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                "volume_24h": pair.get("volume", {}).get("h24", 0),
                "price_change_24h": pair.get("priceChange", {}).get("h24", 0),
                "buyers_24h": pair.get("txns", {}).get("h24", {}).get("buys", 0),
                "sellers_24h": pair.get("txns", {}).get("h24", {}).get("sells", 0),
            }
            result["sources_used"].append("dexscreener")  # type: ignore[attr-defined]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 2: Solana RPC - get account info
    try:
        account = await rpc_call("solana", "getAccountInfo", [address, {"encoding": "jsonParsed"}])
        if account and account.get("value"):
            result["account_exists"] = True  # type: ignore[assignment]
            result["lamports"] = account["value"].get("lamports", 0)
            result["sources_used"].append("solana_rpc")  # type: ignore[attr-defined]
        else:
            result["account_exists"] = False  # type: ignore[assignment]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 3: Birdeye public API
    try:
        data, _ = await fetch_with_fallback(
            [f"https://public-api.birdeye.so/defi/token_meta?address={address}"],
            headers={"X-API-KEY": os.getenv("BIRDEYE_KEY", "")},
        )
        if data and data.get("data"):
            result["birdeye"] = data["data"]
            result["sources_used"].append("birdeye")  # type: ignore[attr-defined]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 4: Jupiter token list
    try:
        data, _ = await fetch_with_fallback(["https://token.jup.ag/all"])
        if data:
            token = next((t for t in data if t.get("address") == address), None)  # type: ignore[attr-defined]
            if token:
                result["jupiter"] = {  # type: ignore[assignment]
                    "name": token.get("name"),  # type: ignore[attr-defined]
                    "symbol": token.get("symbol"),  # type: ignore[attr-defined]
                    "decimals": token.get("decimals"),  # type: ignore[attr-defined]
                    "logo": token.get("logoURI"),  # type: ignore[attr-defined]
                }
                result["sources_used"].append("jupiter")  # type: ignore[attr-defined]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 5: Coingecko
    try:
        data, _ = await fetch_with_fallback([f"https://api.coingecko.com/api/v3/coins/{address}"])
        if data:
            result["coingecko"] = {"name": data.get("name"), "symbol": data.get("symbol")}  # type: ignore[assignment]
            result["sources_used"].append("coingecko")  # type: ignore[attr-defined]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Compute risk score from available data
    risk_score = 0
    findings = []

    if result.get("dexscreener"):
        liq = result["dexscreener"].get("liquidity_usd", 0)  # type: ignore[attr-defined]
        if liq < 1000:
            risk_score += 25
            findings.append(f"Very low liquidity: ${liq:,.0f}")
        elif liq < 10000:
            risk_score += 15
            findings.append(f"Low liquidity: ${liq:,.0f}")

        vol = result["dexscreener"].get("volume_24h", 0)  # type: ignore[attr-defined]
        if liq > 0 and vol > liq * 10:
            risk_score += 10
            findings.append("Volume/liquidity ratio unusually high")

        buyers = result["dexscreener"].get("buyers_24h", 0)  # type: ignore[attr-defined]
        sellers = result["dexscreener"].get("sellers_24h", 0)  # type: ignore[attr-defined]
        if sellers > 0 and buyers > 0:
            ratio = sellers / buyers
            if ratio > 3:
                risk_score += 20
                findings.append(f"Sell pressure {ratio:.1f}x - heavy dumping")
            elif ratio > 1.5:
                risk_score += 10
                findings.append(f"Sell pressure {ratio:.1f}x")

    if not result.get("account_exists") and not result.get("jupiter"):
        risk_score += 30
        findings.append("Token not found on Solana - possible scam address")

    if len(result["sources_used"]) == 0:
        risk_score += 20
        findings.append("Unable to fetch data from any source - proceed with extreme caution")

    risk_score = min(100, risk_score)

    if risk_score >= 80:
        level = "CRITICAL"
    elif risk_score >= 60:
        level = "HIGH"
    elif risk_score >= 40:
        level = "MEDIUM"
    elif risk_score >= 20:
        level = "LOW"
    else:
        level = "SAFE"

    result["risk_score"] = risk_score  # type: ignore[assignment]
    result["risk_level"] = level
    result["findings"] = findings
    result["source_count"] = len(result["sources_used"])  # type: ignore[assignment]
    return result


async def _audit_evm(address: str, chain: str) -> dict[str, Any]:
    """Full audit for EVM tokens (Base, ETH, BSC)."""
    result = {"chain": chain, "address": address, "sources_used": []}

    # Layer 1: DexScreener
    try:
        data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["dexscreener"] = {  # type: ignore[assignment]
                "price_usd": pair.get("priceUsd", 0),
                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                "volume_24h": pair.get("volume", {}).get("h24", 0),
                "price_change_24h": pair.get("priceChange", {}).get("h24", 0),
            }
            result["sources_used"].append("dexscreener")  # type: ignore[attr-defined]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 2: Basescan / Etherscan
    explorer_key = "BASESCAN_KEY" if chain == "base" else "ETHERSCAN_KEY"
    explorer_api = "https://api.basescan.org/api" if chain == "base" else "https://api.etherscan.io/api"
    api_key = os.getenv(explorer_key, "")

    if api_key:
        try:
            data, _ = await fetch_with_fallback(
                [f"{explorer_api}?module=contract&action=getsourcecode&address={address}&apikey={api_key}"]
            )
            if data and data.get("result") and data["result"][0].get("SourceCode"):
                result["verified"] = True  # type: ignore[assignment]
                result["sources_used"].append("explorer")  # type: ignore[attr-defined]
            else:
                result["verified"] = False  # type: ignore[assignment]
                result["findings"].append("Contract not verified on explorer")  # type: ignore[attr-defined]
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

    # Layer 3: Chain RPC
    try:
        account = await rpc_call(chain, "eth_getCode", [address, "latest"])
        if account and account != "0x":
            result["is_contract"] = True  # type: ignore[assignment]
            result["sources_used"].append(f"{chain}_rpc")  # type: ignore[attr-defined]
        else:
            result["is_contract"] = False  # type: ignore[assignment]
            result["findings"].append("Address is not a contract")  # type: ignore[attr-defined]
    except httpx.HTTPError:
        pass

    # Risk computation
    risk_score = 0
    findings = result.get("findings", [])

    if result.get("dexscreener"):
        liq = result["dexscreener"].get("liquidity_usd", 0)  # type: ignore[attr-defined]
        if liq < 5000:
            risk_score += 20
            findings.append(f"Low liquidity: ${liq:,.0f}")  # type: ignore[attr-defined]

    if not result.get("verified"):
        risk_score += 15
        findings.append("Contract source not verified")  # type: ignore[attr-defined]

    if not result.get("is_contract"):
        risk_score += 40
        findings.append("Not a contract - likely EOA or invalid")  # type: ignore[attr-defined]

    if len(result["sources_used"]) == 0:
        risk_score += 25
        findings.append("No data from any source")  # type: ignore[attr-defined]

    risk_score = min(100, risk_score)
    level = (
        "CRITICAL"
        if risk_score >= 80
        else "HIGH"
        if risk_score >= 60
        else "MEDIUM"
        if risk_score >= 40
        else "LOW"
        if risk_score >= 20
        else "SAFE"
    )

    result["risk_score"] = risk_score  # type: ignore[assignment]
    result["risk_level"] = level
    result["findings"] = findings
    return result


# Pre-existing bug surface: this name was referenced (via noqa)
# in 77+ places across x402_tools.py but never defined. The legacy module
# silently failed at every endpoint call. We surface a no-op stub here
# so the audit-tracked fix (issue: fix(f821)) has a clear home.
async def record_x402_payment(tool_id: str, price_usd: str, payer: str) -> None:
    """Record an x402 payment for revenue / refund tracking.

    Historical note: pre-P3A this was a NameError-bait. We now log a
    debug message so receipts flow to logs even though the formal
    revenue table is owned by app/billing/x402/enforcement.py.

    Replace with `from app.domains.billing.x402.enforcement import record_x402_payment`
    once the canonical recording path is wired.
    """
    logger.debug(
        "x402_payment_record tool=%s usd=%s payer=%s (P3A stub - route to enforcement)",
        tool_id,
        price_usd,
        payer,
    )


# Re-exported for tools that mirror legacy symbols
__all__ = [
    # pricing + aliases
    "BUNDLES",
    "FREE_APIS",
    # sources
    "FREE_RPCS",
    # payment routing
    "HUMAN_PAYMENT_TOKENS",
    "HUMAN_PAY_TO",
    "TOOL_ALIASES",
    "AuditRequest",
    "BundleRequest",
    "ClassVar",
    "ClusterRequest",
    "GenericRequest",
    "HumanPaymentMethodsResponse",
    "HumanPaymentRequest",
    "InsiderRequest",
    "MCPProxyRequest",
    "MemeVibeRequest",
    "MultiTokenRequest",
    "SentimentRequest",
    "SentinelModuleRequest",
    "SentinelScanRequest",
    "SmartMoneyAlphaRequest",
    "SmartMoneyRequest",
    # request models
    "TokenRequest",
    "URLRequest",
    "WalletListRequest",
    "WalletRequest",
    "_audit_evm",
    "_audit_solana",
    "_resolve_pay_to",
    # helpers
    "fetch_with_fallback",
    # logger
    "logger",
    # payment stub (P3A surface)
    "record_x402_payment",
    "rpc_call",
    # upstream aliases from other libs (used by _check_rate_limit etc.)
    "td",
]
