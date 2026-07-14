"""x402 shared primitives — constants, models, helpers used across tools.

Phase 3A of AUDIT-2026-Q3.md.

Extracted verbatim from the legacy app/routers/x402_tools.py (god-file
split, 2026-07-07). Anything imported by more than one tools/*.py file
lives here so the per-tool modules stay focused.

Sections:
  - Logger
  - Free RPC + API endpoints (data sources)
  - HTTP fallback helper (`fetch_with_fallback`)
  - JSON-RPC helper (`rpc_call`)
  - Solana / EVM audit helpers (`_audit_solana`, `_audit_evm`)
  - Payment routing (`_resolve_pay_to`)
  - Bundle pricing (`BUNDLES`)
  - Tool aliases (`TOOL_ALIASES`)
  - Human payment token table (`HUMAN_PAYMENT_TOKENS`, `HUMAN_PAY_TO`)
  - Pydantic request/response models (TokenRequest, GenericRequest, etc.)
  - `record_x402_payment` stub (pre-existing missing-import bug surfaced)

Note: `record_x402_payment` is intentionally a logging stub. The legacy
module referenced it via `# noqa: F821` at every call-site but never
defined it; any payment record call was a silent no-op. Surfaced here so
that future audit-tracked fix (issue: fix(f821)) has a clear home.
"""

from __future__ import annotations

from app.core.logging import get_logger
import os
from typing import Any, ClassVar

import aiohttp
from pydantic import BaseModel, Field

logger = get_logger("x402_tools")

# Caching shield - all data calls route through cache → rate limit → provider chain
from app.caching_shield.service_mcp import get_service_mcp
from app.caching_shield.tool_data import td

_svc_mcp = get_service_mcp()

# ── Data Sources (multi-layer fallback) ─────────────────────────

# Free public RPCs for blockchain queries
FREE_RPCS: dict[str, list[str]] = {
    "solana": [
        "https://api.mainnet-beta.solana.com",
        "https://solana.publicnode.com",
        "https://api.devnet.solana.com",
    ],
    "base": [
        "https://base.llamarpc.com",
        "https://base.publicnode.com",
        "https://developer-access-mainnet.base.org",
    ],
    "ethereum": [
        "https://eth.llamarpc.com",
        "https://ethereum.publicnode.com",
        "https://rpc.ankr.com/eth",
    ],
    "bsc": [
        "https://bsc-dataseed.binance.org",
        "https://bsc.publicnode.com",
    ],
}

# Free API endpoints (no key required)
FREE_APIS: dict[str, str] = {
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "coingecko": "https://api.coingecko.com/api/v3",
    "birdeye_public": "https://public-api.birdeye.so",
    "jupiter": "https://api.jup.ag",
    "defillama": "https://api.llama.fi",
    "pumpfun": "https://frontend-api.pump.fun",
}


# ── Fallback HTTP Request System ────────────────────────────────


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
                            data: dict[str, Any] = await resp.json()
                            return data, url
            except Exception as e:
                logger.debug(f"URL failed: {url} - {e}")
                continue
    return None, None


async def rpc_call(chain: str, method: str, params: list[Any]) -> Any:
    """Make JSON-RPC call to blockchain with fallback RPCs."""
    rpcs = FREE_RPCS.get(chain, FREE_RPCS.get("solana"))
    urls = [f"{rpc}" for rpc in rpcs]
    payloads = [{"jsonrpc": "2.0", "id": 1, "method": method, "params": params} for _ in urls]

    async with aiohttp.ClientSession() as session:
        for url, payload in zip(urls, payloads, strict=False):
            try:
                async with session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("result")
            except Exception:
                continue
    return None


# ── Request Models ──────────────────────────────────────────────


class TokenRequest(BaseModel):
    address: str
    chain: str = "solana"


class WalletRequest(BaseModel):
    address: str
    chain: str = "solana"


class SmartMoneyRequest(BaseModel):
    chain: str = "solana"
    threshold: float = 10000.0
    limit: int = 20


class URLRequest(BaseModel):
    url: str


class SentimentRequest(BaseModel):
    token: str
    chain: str = "solana"


class ClusterRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = 3


class InsiderRequest(BaseModel):
    creator_address: str
    chain: str = "solana"


# TwitterRequest and MarketRequest are defined in legacy code but never
# referenced by any @router endpoint. Surfaced here for traceability.
class TwitterRequest(BaseModel):  # pragma: no cover — legacy, unused
    query: str
    user: str | None = None
    handle: str | None = None
    tweet_id: str | None = None


class MultiTokenRequest(BaseModel):
    addresses: list[str]
    chain: str = "solana"


class WalletListRequest(BaseModel):
    addresses: list[str]
    chain: str = "solana"


class MarketRequest(BaseModel):  # pragma: no cover — legacy, unused
    chain: str = "all"
    hours: int = 24


class GenericRequest(BaseModel):
    address: str | None = None
    chain: str = "solana"
    token: str | None = None
    query: str | None = None
    hours: int = 24
    threshold: float = 10000.0
    limit: int = 20


# ── Helpers ─────────────────────────────────────────────────────


async def _audit_solana(address: str) -> dict[str, Any]:
    """Full audit for Solana tokens with 5-layer fallback."""
    result = {"chain": "solana", "address": address, "sources_used": []}

    # Layer 1: DexScreener (free, no key)
    try:
        data, _src = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["dexscreener"] = {
                "price_usd": pair.get("priceUsd", 0),
                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                "volume_24h": pair.get("volume", {}).get("h24", 0),
                "price_change_24h": pair.get("priceChange", {}).get("h24", 0),
                "buyers_24h": pair.get("txns", {}).get("h24", {}).get("buys", 0),
                "sellers_24h": pair.get("txns", {}).get("h24", {}).get("sells", 0),
            }
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Layer 2: Solana RPC - get account info
    try:
        account = await rpc_call("solana", "getAccountInfo", [address, {"encoding": "jsonParsed"}])
        if account and account.get("value"):
            result["account_exists"] = True
            result["lamports"] = account["value"].get("lamports", 0)
            result["sources_used"].append("solana_rpc")
        else:
            result["account_exists"] = False
    except Exception:
        pass

    # Layer 3: Birdeye public API
    try:
        data, _ = await fetch_with_fallback(
            [f"https://public-api.birdeye.so/defi/token_meta?address={address}"],
            headers={"X-API-KEY": os.getenv("BIRDEYE_KEY", "")},
        )
        if data and data.get("data"):
            result["birdeye"] = data["data"]
            result["sources_used"].append("birdeye")
    except Exception:
        pass

    # Layer 4: Jupiter token list
    try:
        data, _ = await fetch_with_fallback(["https://token.jup.ag/all"])
        if data:
            token = next((t for t in data if t.get("address") == address), None)
            if token:
                result["jupiter"] = {
                    "name": token.get("name"),
                    "symbol": token.get("symbol"),
                    "decimals": token.get("decimals"),
                    "logo": token.get("logoURI"),
                }
                result["sources_used"].append("jupiter")
    except Exception:
        pass

    # Layer 5: Coingecko
    try:
        data, _ = await fetch_with_fallback([f"https://api.coingecko.com/api/v3/coins/{address}"])
        if data:
            result["coingecko"] = {"name": data.get("name"), "symbol": data.get("symbol")}
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # Compute risk score from available data
    risk_score = 0
    findings = []

    if result.get("dexscreener"):
        liq = result["dexscreener"].get("liquidity_usd", 0)
        if liq < 1000:
            risk_score += 25
            findings.append(f"Very low liquidity: ${liq:,.0f}")
        elif liq < 10000:
            risk_score += 15
            findings.append(f"Low liquidity: ${liq:,.0f}")

        vol = result["dexscreener"].get("volume_24h", 0)
        if liq > 0 and vol > liq * 10:
            risk_score += 10
            findings.append("Volume/liquidity ratio unusually high")

        buyers = result["dexscreener"].get("buyers_24h", 0)
        sellers = result["dexscreener"].get("sellers_24h", 0)
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

    result["risk_score"] = risk_score
    result["risk_level"] = level
    result["findings"] = findings
    result["source_count"] = len(result["sources_used"])
    return result


async def _audit_evm(address: str, chain: str) -> dict[str, Any]:
    """Full audit for EVM tokens (Base, ETH, BSC)."""
    result = {"chain": chain, "address": address, "sources_used": []}

    # Layer 1: DexScreener
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["dexscreener"] = {
                "price_usd": pair.get("priceUsd", 0),
                "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
                "volume_24h": pair.get("volume", {}).get("h24", 0),
                "price_change_24h": pair.get("priceChange", {}).get("h24", 0),
            }
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Layer 2: Basescan / Etherscan
    explorer_key = "BASESCAN_KEY" if chain == "base" else "ETHERSCAN_KEY"
    explorer_api = (
        "https://api.basescan.org/api" if chain == "base" else "https://api.etherscan.io/api"
    )
    api_key = os.getenv(explorer_key, "")

    if api_key:
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"{explorer_api}?module=contract&action=getsourcecode&address={address}&apikey={api_key}"
                ]
            )
            if data and data.get("result") and data["result"][0].get("SourceCode"):
                result["verified"] = True
                result["sources_used"].append("explorer")
            else:
                result["verified"] = False
                result["findings"].append("Contract not verified on explorer")
        except Exception:
            pass

    # Layer 3: Chain RPC
    try:
        account = await rpc_call(chain, "eth_getCode", [address, "latest"])
        if account and account != "0x":
            result["is_contract"] = True
            result["sources_used"].append(f"{chain}_rpc")
        else:
            result["is_contract"] = False
            result["findings"].append("Address is not a contract")
    except Exception:
        pass

    # Risk computation
    risk_score = 0
    findings = result.get("findings", [])

    if result.get("dexscreener"):
        liq = result["dexscreener"].get("liquidity_usd", 0)
        if liq < 5000:
            risk_score += 20
            findings.append(f"Low liquidity: ${liq:,.0f}")

    if not result.get("verified"):
        risk_score += 15
        findings.append("Contract source not verified")

    if not result.get("is_contract"):
        risk_score += 40
        findings.append("Not a contract - likely EOA or invalid")

    if len(result["sources_used"]) == 0:
        risk_score += 25
        findings.append("No data from any source")

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

    result["risk_score"] = risk_score
    result["risk_level"] = level
    result["findings"] = findings
    return result


# ── Helper: Record x402 Payment ────────────────────────────────
# Pre-existing bug surface: this name was referenced (via # noqa: F821)
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


# ── Bundle Pricing (Phase 3A surface) ──────────────────────────


BUNDLES: dict[str, dict[str, Any]] = {
    "security_pack": {
        "name": "Security Pack",
        "description": "Complete pre-trade security check - rug scan, contract audit, URL safety, and honeypot detection.",
        "tools": ["rugshield", "audit", "urlcheck", "honeypot_check"],
        "individual_total": 0.13,  # 0.02 + 0.05 + 0.01 + 0.05
        "bundle_price_usd": 0.10,  # 23% discount
        "bundle_price_atoms": "100000",
        "category": "bundle",
        "trial_free": 1,
    },
    "intelligence_pack": {
        "name": "Intelligence Pack",
        "description": "Whale tracking suite - decode wallets, follow smart money, detect clusters and insider patterns.",
        "tools": ["whale", "smartmoney", "cluster", "insider"],
        "individual_total": 0.35,  # 0.15 + 0.05 + 0.05 + 0.10
        "bundle_price_usd": 0.25,  # 29% discount
        "bundle_price_atoms": "250000",
        "category": "bundle",
        "trial_free": 1,
    },
    "all_in_one": {
        "name": "All-in-One Audit",
        "description": "Maximum intelligence - comprehensive audit + smart money alpha + meme vibe score in one call.",
        "tools": ["comprehensive_audit", "smart_money_alpha", "meme_vibe_score"],
        "individual_total": 0.50,  # 0.15 + 0.25 + 0.10
        "bundle_price_usd": 0.35,  # 30% discount
        "bundle_price_atoms": "350000",
        "category": "bundle",
        "trial_free": 1,
    },
    "forensic_pack": {
        "name": "Forensic Investigation Pack",
        "description": "Complete forensic analysis - valuation, OSINT identity hunt, and investigation report at 33% discount.",
        "tools": ["forensic_valuation", "osint_identity_hunt", "investigation_report"],
        "individual_total": 0.60,  # 0.25 + 0.15 + 0.20
        "bundle_price_usd": 0.40,  # 33% discount
        "bundle_price_atoms": "400000",
        "category": "bundle",
        "trial_free": 1,
    },
}


# ── Tool aliases (catch-all dispatcher source) ─────────────────


TOOL_ALIASES: dict[str, str] = {
    # Security
    "airdrop_check": "airdrop_finder",
    "bundler_detect": "mev_protection",
    "clone_detect": "contract_diff",
    "deployer_history": "insider",
    "fresh_pair": "launch",
    "liquidity_migration": "rugshield",
    "mev_alert": "mev_protection",
    "profile_flip": "social_signal",
    "protocol_risk": "chain_health",
    "scam_database": "urlcheck",
    "token_age": "audit",
    "wash_trading": "nft_wash_detector",
    # Intelligence
    "alpha_digest": "smart_money_alpha",
    "insider_network": "insider",
    "kol_performance": "social_signal",
    "listing_predictor": "market_overview",
    "sniper_detect": "sniper_alert",
    "syndicate_scan": "cluster",
    "syndicate_track": "cluster",
    "whale_accumulation": "whale",
    "whale_profile": "whale",
    "whale_scan": "whale",
    "wallet_graph": "cluster",
    # Market
    "arbitrage_scan": "market_overview",
    "liquidity_depth": "market_overview",
    "unlock_calendar": "market_overview",
    # Social
    "sentiment_spike": "sentiment",
    # Analysis
    "portfolio_aggregate": "portfolio_tracker",
    "wallet_pnl": "wallet",
    # Premium (mapped to closest real tool)
    "forensic_valuation": "forensics",
    "investigation_report": "forensics",
    "osint_identity_hunt": "social_signal",
    # Launchpad
    # (fresh_pair already mapped to launch above)
}

# ── Expanded tool aliases (44 new specialized tools → real handlers) ──
try:
    from app.routers._expanded_aliases import EXPANDED_ALIASES

    TOOL_ALIASES.update(EXPANDED_ALIASES)
except Exception:
    pass


# ── Human Payment - Multi-Chain Wallet Pay-Per-Call ────────────

# Supported payment tokens across all chains
HUMAN_PAYMENT_TOKENS: dict[str, dict[str, Any]] = {
    # Base chain
    "USDC-BASE": {
        "chain": "base",
        "network": "eip155:8453",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Base",
        "icon": "💵",
    },
    # Solana chain
    "USDC-SOL": {
        "chain": "solana",
        "network": "solana:mainnet",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Solana",
        "icon": "💵",
    },
    "SOL": {
        "chain": "solana",
        "network": "solana:mainnet",
        "asset": "SOL",
        "decimals": 9,
        "label": "SOL native",
        "icon": "◎",
    },
    # Ethereum chain
    "USDC-ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Ethereum",
        "icon": "💵",
    },
    "USDT-ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "USDT",
        "decimals": 6,
        "label": "USDT on Ethereum",
        "icon": "💲",
    },
    "ETH": {
        "chain": "ethereum",
        "network": "eip155:1",
        "asset": "ETH",
        "decimals": 18,
        "label": "ETH native",
        "icon": "⟠",
    },
    # BNB Chain
    "USDC-BSC": {
        "chain": "bsc",
        "network": "eip155:56",
        "asset": "USDC",
        "decimals": 18,
        "label": "USDC on BSC",
        "icon": "💵",
    },
    "USDT-BSC": {
        "chain": "bsc",
        "network": "eip155:56",
        "asset": "USDT",
        "decimals": 18,
        "label": "USDT on BSC",
        "icon": "💲",
    },
    # Polygon
    "USDC-POLY": {
        "chain": "polygon",
        "network": "eip155:137",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Polygon",
        "icon": "💵",
    },
    "POL": {
        "chain": "polygon",
        "network": "eip155:137",
        "asset": "POL",
        "decimals": 18,
        "label": "POL native",
        "icon": "🟣",
    },
    # Arbitrum
    "USDC-ARB": {
        "chain": "arbitrum",
        "network": "eip155:42161",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on Arbitrum",
        "icon": "💵",
    },
    # TRON
    "USDT-TRON": {
        "chain": "tron",
        "network": "tron:mainnet",
        "asset": "USDT",
        "decimals": 6,
        "label": "USDT on TRON",
        "icon": "💲",
    },
    "USDC-TRON": {
        "chain": "tron",
        "network": "tron:mainnet",
        "asset": "USDC",
        "decimals": 6,
        "label": "USDC on TRON",
        "icon": "💵",
    },
    # Bitcoin
    "BTC": {
        "chain": "bitcoin",
        "network": "bitcoin:mainnet",
        "asset": "BTC",
        "decimals": 8,
        "label": "Bitcoin",
        "icon": "₿",
    },
    # Fiat
    "EUR-SEPA": {
        "chain": "sepa",
        "network": "sepa:eur",
        "asset": "EUR",
        "decimals": 2,
        "label": "EUR via SEPA",
        "icon": "€",
    },
}


# Pay-to addresses - pulled dynamically from WalletManagerV2
# Falls back to env vars if wallet manager unavailable
def _resolve_pay_to(chain: str) -> str:
    """Get active x402 payment address from WalletManagerV2."""
    try:
        from app.wallet_manager_v2 import get_wallet_manager_v2

        mgr = get_wallet_manager_v2(os.getenv("WALLET_VAULT_PASSWORD", ""))
        # Map chain key to wallet manager chain key
        chain_map = {
            "base": "eth",
            "ethereum": "eth",
            "bsc": "eth",
            "polygon": "eth",
            "arbitrum": "eth",
            "optimism": "eth",
            "avalanche": "eth",
            "fantom": "eth",
            "gnosis": "eth",
            "solana": "sol",
            "tron": "trx",
            "bitcoin": "btc",
        }
        wm_chain = chain_map.get(chain, chain)
        for w in mgr._wallets.values():
            if w.chain == wm_chain and w.x402_enabled and w.status == "active":
                return w.address
    except Exception:
        pass
    # Fallback: env vars
    fallbacks = {
        "base": "X402_EVM_PAY_TO",
        "ethereum": "X402_EVM_PAY_TO",
        "bsc": "X402_EVM_PAY_TO",
        "polygon": "X402_EVM_PAY_TO",
        "arbitrum": "X402_EVM_PAY_TO",
        "solana": "X402_SOL_PAY_TO",
        "tron": "X402_TRON_PAY_TO",
        "bitcoin": "X402_BTC_PAY_TO",
        "sepa": "ASTERPAY_SEPA_IBAN",
    }
    env_key = fallbacks.get(chain)
    if env_key:
        return os.getenv(env_key, "")
    return ""


HUMAN_PAY_TO: dict[str, str] = {
    "base": _resolve_pay_to("base"),
    "ethereum": _resolve_pay_to("ethereum"),
    "bsc": _resolve_pay_to("bsc"),
    "polygon": _resolve_pay_to("polygon"),
    "arbitrum": _resolve_pay_to("arbitrum"),
    "solana": _resolve_pay_to("solana"),
    "tron": _resolve_pay_to("tron"),
    "bitcoin": _resolve_pay_to("bitcoin"),
    "sepa": os.getenv("ASTERPAY_SEPA_IBAN", ""),
}


# ── Models specific to integration endpoints ──────────────────────


class BundleRequest(BaseModel):
    address: str = ""
    token: str = ""
    wallet: str = ""
    chain: str = "base"
    url: str = ""


class MCPProxyRequest(BaseModel):
    service: str
    tool: str
    arguments: dict[str, Any] = {}


class HumanPaymentRequest(BaseModel):
    tool: str = Field(..., description="Tool ID to execute")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool parameters")
    payment_token: str = Field(
        ..., description=f"Payment token key: {', '.join(HUMAN_PAYMENT_TOKENS.keys())}"
    )
    tx_hash: str = Field(..., description="Transaction hash on-chain")
    wallet: str = Field(..., description="Payer wallet address")
    chain: str | None = Field(
        default=None, description="Blockchain (auto-detected from payment_token if not set)"
    )


class HumanPaymentMethodsResponse(BaseModel):
    """Response listing all payment methods available to humans."""

    tokens: list[dict[str, Any]]
    pay_to_addresses: dict[str, str]
    chain_count: int
    token_count: int


class AuditRequest(BaseModel):
    address: str
    chain: str = "solana"


class SmartMoneyAlphaRequest(BaseModel):
    wallet: str
    chain: str = "solana"


class MemeVibeRequest(BaseModel):
    token: str
    chain: str = "solana"


class SentinelScanRequest(BaseModel):
    """Full 9-module SENTINEL deep scan."""

    address: str
    chain: str = "solana"
    dev_address: str | None = None


class SentinelModuleRequest(BaseModel):
    """Single SENTINEL module request."""

    address: str
    chain: str = "solana"
    dev_address: str | None = None


# Re-exported for tools that mirror legacy symbols
__all__ = [
    # logger
    "logger",
    # sources
    "FREE_RPCS",
    "FREE_APIS",
    # helpers
    "fetch_with_fallback",
    "rpc_call",
    "_audit_solana",
    "_audit_evm",
    # pricing + aliases
    "BUNDLES",
    "TOOL_ALIASES",
    # payment routing
    "HUMAN_PAYMENT_TOKENS",
    "HUMAN_PAY_TO",
    "_resolve_pay_to",
    # payment stub (P3A surface)
    "record_x402_payment",
    # request models
    "TokenRequest",
    "WalletRequest",
    "SmartMoneyRequest",
    "URLRequest",
    "SentimentRequest",
    "ClusterRequest",
    "InsiderRequest",
    "MultiTokenRequest",
    "WalletListRequest",
    "GenericRequest",
    "BundleRequest",
    "MCPProxyRequest",
    "HumanPaymentRequest",
    "HumanPaymentMethodsResponse",
    "AuditRequest",
    "SmartMoneyAlphaRequest",
    "MemeVibeRequest",
    "SentinelScanRequest",
    "SentinelModuleRequest",
    # upstream aliases from other libs (used by _check_rate_limit etc.)
    "td",
    "ClassVar",
]
