"""x402 pricing & chain configuration — single source of truth.

Extracted from enforcement.py (P6 - complexity refactor).

Provides:
- CHAIN_USDC: chain config for all 13 supported payment chains
- TOOL_PRICES: tool pricing dict (seeded at import, enriched lazily)
- Pay-to address resolution and helpers
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger("x402_enforcement")

EVM_PAY_TO: str | None = None
SOL_PAY_TO: str | None = None

CHAIN_USDC: dict[str, dict[str, Any]] = {
    "base": {
        "network": "eip155:8453",
        "chain_id": 8453,
        "usdc": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["coinbase_cdp", "payai"],
    },
    "solana": {
        "network": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        "chain_id": None,
        "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "name": "USD Coin",
        "version": "2",
        "method": "payai",
        "verify": "facilitator",
        "facilitators": ["payai"],
    },
    "bsc": {
        "network": "eip155:56",
        "chain_id": 56,
        "usdc": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["eip7702"],
    },
    "ethereum": {
        "network": "eip155:1",
        "chain_id": 1,
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "facilitator",
        "facilitators": ["primev", "payai", "cloudflare_x402", "eip7702"],
    },
    "tron": {
        "network": "tron:mainnet",
        "chain_id": None,
        "usdc": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",
        "name": "USD Coin (TRC20)",
        "version": "2",
        "method": "tron_selfverify",
        "verify": "facilitator",
        "facilitators": ["tron_selfverify"],
        "tokens": {
            "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "USDD": "TPYmHEhy5n8TCEfZGqW2rPbmgh1fGqNBPa",
        },
    },
    "bitcoin": {
        "network": "bitcoin:mainnet",
        "chain_id": None,
        "usdc": "",
        "name": "Bitcoin",
        "version": "2",
        "method": "bitcoin_selfverify",
        "verify": "facilitator",
        "facilitators": ["bitcoin_selfverify"],
        "tokens": {"BTC": "native"},
    },
    "arbitrum": {
        "network": "eip155:42161",
        "chain_id": 42161,
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "optimism": {
        "network": "eip155:10",
        "chain_id": 10,
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "polygon": {
        "network": "eip155:137",
        "chain_id": 137,
        "usdc": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "avalanche": {
        "network": "eip155:43114",
        "chain_id": 43114,
        "usdc": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "fantom": {
        "network": "eip155:250",
        "chain_id": 250,
        "usdc": "0x04068DA6C83AFCFA0e13ba15A6696662335D5B75",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "gnosis": {
        "network": "eip155:100",
        "chain_id": 100,
        "usdc": "0xDDAfbb505ad214D7b80b1f830fcCc89B60fb7A83",
        "name": "USD Coin",
        "version": "2",
        "method": "local_eip712",
        "verify": "self",
        "facilitators": ["eip7702"],
    },
    "sepa": {
        "network": "sepa:eur",
        "chain_id": None,
        "usdc": "",
        "name": "Euro",
        "version": "1",
        "method": "asterpay",
        "verify": "facilitator",
        "facilitators": ["asterpay"],
        "tokens": {"EUR": "fiat"},
    },
}

CHAIN_NAMES: dict[str, str] = {
    "base": "Base",
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BNB Chain",
    "tron": "TRON",
    "bitcoin": "Bitcoin",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "polygon": "Polygon",
    "avalanche": "Avalanche",
    "fantom": "Fantom",
    "gnosis": "Gnosis",
    "sepa": "SEPA (EUR)",
}

TOOL_PRICES: dict[str, dict[str, Any]] = {}

# ─── 1. Seed from canonical base at import time ─────────────────────────
try:
    from app.canonical_tools import CANONICAL_TOOL_PRICES

    TOOL_PRICES.update(CANONICAL_TOOL_PRICES)
except ImportError:
    logger.warning("Cannot import CANONICAL_TOOL_PRICES")

# ─── 2. Add hard-coded tools not in gateway configs yet ─────────────────
_NEW_TOOL_PRICES: dict[str, dict[str, Any]] = {
    "forensic_valuation": {
        "price_usd": 0.25,
        "price_atoms": "250000",
        "category": "premium",
        "trial_free": 1,
        "description": "Institutional-grade token valuation - DCF intrinsic value, comparable analysis with outlier detection, scam probability scoring",
    },
    "osint_identity_hunt": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "premium",
        "trial_free": 2,
        "description": "Cross-platform OSINT investigation - hunt usernames across 400+ networks, domain intelligence, stealth page capture",
    },
    "investigation_report": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "premium",
        "trial_free": 1,
        "description": "Full investigation report - on-chain forensics, financial valuation, OSINT findings, scam scoring in one deliverable",
    },
    "whale_accumulation": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "intelligence",
        "trial_free": 2,
        "description": "Accumulation Pattern Detector - detect stealth accumulation by large holders before price impact. Identifies quiet buying patterns and wallet funding sequences.",
    },
    "social_engineering": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "premium",
        "trial_free": 1,
        "description": "Social Engineering & Identity Fraud Detector - detect fake teams, AI-generated profiles, phishing domains, copied whitepapers, and social engineering campaigns.",
    },
    "forensic_pack": {
        "price_usd": 0.35,
        "price_atoms": "350000",
        "category": "bundle",
        "trial_free": 1,
        "description": "Forensic Investigation Pack - valuation + OSINT + report at 33% discount",
    },
    "token_watch_create": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "monitoring",
        "trial_free": 3,
        "description": "Set token monitoring watch - alerts when LP drops, price changes, or rug indicators detected",
    },
    "token_watch_check": {
        "price_usd": 0.03,
        "price_atoms": "30000",
        "category": "monitoring",
        "trial_free": 5,
        "description": "One-shot token status check - current LP, price, volume, and rug risk warnings",
    },
    "catalog": {
        "price_usd": 0.00,
        "price_atoms": "0",
        "category": "api",
        "trial_free": 999,
        "description": "Browse available tools, pricing, and chain support",
    },
    "smart_money_alpha": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "intelligence",
        "trial_free": 3,
        "description": "Smart money alpha signals - track wallets that consistently outperform the market",
    },
    "meme_vibe_score": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "social",
        "trial_free": 3,
        "description": "Meme token vibe scoring - sentiment, community strength, and virality analysis",
    },
    "mcp-proxy": {
        "price_usd": 0.01,
        "price_atoms": "10000",
        "category": "api",
        "trial_free": 5,
        "description": "MCP protocol proxy - route tool calls through the x402 payment layer",
    },
    "human-execute": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "api",
        "trial_free": 2,
        "description": "Human-in-the-loop execution - wallet-based payment for manual crypto investigation tasks",
    },
    "reputation_score": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "premium",
        "trial_free": 1,
        "description": "Comprehensive 0-100 trust score combining wallet labels, scam databases, deployer history, and RAG similarity matching",
    },
    "webhook_register": {
        "price_usd": 0.02,
        "price_atoms": "20000",
        "category": "monitoring",
        "trial_free": 2,
        "description": "Register webhook URL for real-time monitoring alerts - rug pulls, whale moves, price crashes",
    },
    "webhook_list": {
        "price_usd": 0.00,
        "price_atoms": "0",
        "category": "monitoring",
        "trial_free": 999,
        "description": "List registered webhooks for an address",
    },
    "rug_probability": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "premium",
        "trial_free": 1,
        "description": "Predictive rug pull probability 0-100 - honeypot + liquidity + deployer + social signals",
    },
    "history": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "analysis",
        "trial_free": 2,
        "description": "Historical scanner time-series - risk/liquidity/volume/price trends over hours",
    },
    "narrative": {
        "price_usd": 0.05,
        "price_atoms": "50000",
        "category": "social",
        "trial_free": 3,
        "description": "Market narrative engine - what is the market saying about this token RIGHT NOW",
    },
    "portfolio_risk": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "premium",
        "trial_free": 1,
        "description": "Cross-chain portfolio risk dashboard - unified risk across multiple wallets and chains",
    },
    "defi_position": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "defi",
        "trial_free": 1,
        "description": "DeFi position analyzer - LP holdings, impermanent loss estimation, yield sustainability, protocol risk",
    },
    "liquidation_cascade": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "defi",
        "trial_free": 1,
        "description": "Liquidation Cascade Risk Analyzer - cross-chain DeFi position monitoring, health factor computation, and cascade scenario simulation for Aave/Compound positions",
    },
    "mev_detect": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "security",
        "trial_free": 2,
        "description": "MEV/Sandwich attack detection - sandwich attacks, frontrunning, arbitrage extraction, MEV bot identification",
    },
    "composite_score": {
        "price_usd": 0.25,
        "price_atoms": "250000",
        "category": "premium",
        "trial_free": 1,
        "description": "RMI Composite Score - one number combining ALL signals for instant buy/sell/avoid decisions",
    },
    "smart_money": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "intelligence",
        "trial_free": 1,
        "description": "Smart Money P&L Tracker - real profitability-based wallet tracking, find the actual profitable traders",
    },
    "clone_detect": {
        "price_usd": 0.10,
        "price_atoms": "100000",
        "category": "security",
        "trial_free": 2,
        "description": "Token Clone Detector - find tokens cloned from known rug pulls by name, symbol, and deployer patterns",
    },
    "wash_trade_detect": {
        "price_usd": 0.15,
        "price_atoms": "150000",
        "category": "security",
        "trial_free": 2,
        "description": "Wash Trading & Insider Detection - artificial volume, coordinated buying, insider accumulation patterns",
    },
    "rug_imminence": {
        "price_usd": 0.20,
        "price_atoms": "200000",
        "category": "security",
        "trial_free": 1,
        "description": "Rug Pull Imminence Predictor - AI-powered early warning system fusing 7 signals to predict imminent rug pulls before they happen",
    },
    "cross_chain_whale": {
        "price_usd": 0.08,
        "price_atoms": "80000",
        "category": "intelligence",
        "trial_free": 2,
        "description": "Track whale wallets across multiple blockchains simultaneously - maps cross-chain capital flows, bridge migrations, and multi-network positioning of top holders",
    },
}
TOOL_PRICES.update(_NEW_TOOL_PRICES)

_tool_prices_extras_loaded = False

def _load_tool_prices() -> None:
    """Load tool prices from canonical dict (single source of truth).

    1. Load from CANONICAL_TOOL_PRICES (127 tools, always present)
    2. Enrich from gateway configs if available (adds chain variants)
    3. NO circular catalog API fallback - that was the bug
    """
    global TOOL_PRICES

    try:
        from app.canonical_tools import CANONICAL_TOOL_PRICES

        TOOL_PRICES.update(CANONICAL_TOOL_PRICES)
    except ImportError:
        pass

    gateway_base = None
    for candidate in [
        os.environ.get("X402_GATEWAY_DIR"),
        "/srv/work/repos/rmi-mcp-x402",
        "/srv/rmi/rmi-mcp-x402",
    ]:
        if os.path.isdir(candidate):
            gateway_base = candidate
            break

    if gateway_base and os.path.exists(gateway_base):
        logger.info(f"Loading tool prices from gateway configs at {gateway_base}")
        for chain_dir in os.listdir(gateway_base):
            index_path = os.path.join(gateway_base, chain_dir, "index.ts")
            if not os.path.exists(index_path):
                continue
            with open(index_path) as f:
                content = f.read()

            tool_pattern = r"(\w+):\s*\{([^}]+)\}"
            for m in re.finditer(tool_pattern, content):
                tool_id = m.group(1)
                body = m.group(2)
                if "name:" not in body or "price:" not in body:
                    continue

                name_match = re.search(r'name:\s*"([^"]+)"', body)
                price_match = re.search(r'price:\s*"\$([^"]+)"', body)
                atoms_match = re.search(r'priceAtomic:\s*"([^"]+)"', body)
                cat_match = re.search(r'category:\s*"([^"]+)"', body)
                trial_match = re.search(r"trialFree:\s*(\d+)", body)

                if all([name_match, price_match, atoms_match, cat_match, trial_match]):
                    TOOL_PRICES[tool_id] = {
                        "price_usd": float(price_match.group(1)),
                        "price_atoms": atoms_match.group(1),
                        "category": (cat_match.group(1) or "").lower(),
                        "trial_free": int(trial_match.group(1)),
                        "description": name_match.group(1) or "",
                    }

def _ensure_tool_prices() -> None:
    """Load expanded tools, bundles, and DataBus pricing on first use."""
    global _tool_prices_extras_loaded
    if _tool_prices_extras_loaded:
        return
    _tool_prices_extras_loaded = True

    try:
        from app.routers._expanded_tools import ADDITIONAL_TOOLS as _EXPANDED_TOOLS

        TOOL_PRICES.update(_EXPANDED_TOOLS)
        logger.info(f"x402 enforcement: loaded {len(_EXPANDED_TOOLS)} expanded tools")
    except (ValueError, TypeError, KeyError, OSError) as e :
        logger.debug("_ensure_tool_prices_error", exc_info=True)
        logger.warning(f"x402 enforcement: could not load expanded tools: {e}")

    try:
        from app.routers.x402_tools import BUNDLES as _TOOLS_BUNDLES

        for bid, b in _TOOLS_BUNDLES.items():
            if bid not in TOOL_PRICES and b.get("category") == "bundle":
                TOOL_PRICES[bid] = {
                    "price_usd": b.get("bundle_price_usd", 0.01),
                    "price_atoms": b.get("bundle_price_atoms", "10000"),
                    "category": "bundle",
                    "trial_free": b.get("trial_free", 1),
                    "description": b.get("name", bid),
                }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"x402 enforcement: could not load x402_tools bundles: {e}")

    try:
        from app.routers.x402_databus_tools import X402_TOOL_PRICING as _DATABUS_PRICING

        TOOL_PRICES.update(_DATABUS_PRICING)
        logger.info(f"x402 enforcement: loaded {len(_DATABUS_PRICING)} DataBus-powered tools")
    except (ValueError, TypeError, KeyError, OSError) as e :
        logger.debug("_ensure_tool_prices_error", exc_info=True)
        logger.warning(f"x402 enforcement: could not load DataBus tools: {e}")

    _bad_keys = [k for k in TOOL_PRICES if "{" in k or "/" in k or " " in k]
    if _bad_keys:
        logger.warning(f"Removing corrupted tool IDs: {_bad_keys}")
    for _k in _bad_keys:
        del TOOL_PRICES[_k]

def _ensure_pay_addresses() -> None:
    """Ensure EVM_PAY_TO and SOL_PAY_TO are populated from env vars."""
    global EVM_PAY_TO, SOL_PAY_TO
    if not EVM_PAY_TO:
        EVM_PAY_TO = os.getenv("X402_PAYMENT_ADDRESS_EVM", os.getenv("X402_PAYMENT_ADDRESS", ""))
    if not SOL_PAY_TO:
        SOL_PAY_TO = os.getenv("X402_PAYMENT_ADDRESS_SOL", os.getenv("X402_PAYMENT_ADDRESS", ""))

def _get_pay_to_address(chain: str) -> str:
    """Get active x402 payment address from WalletManagerV2, with fallbacks."""
    try:
        from app.wallet_manager_v2 import get_wallet_manager_v2

        mgr = get_wallet_manager_v2(os.getenv("WALLET_VAULT_PASSWORD", ""))
        for w in mgr._wallets.values():
            if w.chain == chain and w.x402_enabled and w.status == "active":
                return w.address
    except (AttributeError, ImportError):
        pass
    if chain == "eth":
        return os.getenv("X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
    if chain == "sol":
        return os.getenv("X402_SOL_PAY_TO", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv")
    if chain in ("base", "polygon", "arbitrum", "optimism", "avalanche", "bsc", "fantom", "gnosis"):
        return _get_pay_to_address("eth")
    return ""

def _resolve_pay_to(method: str) -> str:
    _ensure_pay_addresses()
    if method == "payai":
        return SOL_PAY_TO or ""
    elif method == "bitcoin_selfverify":
        return os.getenv("X402_BTC_PAY_TO", "")
    elif method == "tron_selfverify":
        return os.getenv("X402_TRON_PAY_TO", "")
    elif method == "asterpay":
        return os.getenv("ASTERPAY_SEPA_IBAN", "")
    return EVM_PAY_TO or ""

def _resolve_asset(cfg: dict[str, Any]) -> str:
    asset: str = cfg.get("usdc", "") or ""
    if not asset and "tokens" in cfg:
        first: str = next(iter(cfg["tokens"].values()), "") or ""
        return first if first != "native" else "BTC"
    return asset

def _build_extra(
    cfg: dict[str, Any], tool_id: str, chain_key: str, pay_to: str, method: str
) -> dict[str, Any]:
    extra: dict[str, Any] = {
        "name": cfg["name"],
        "version": cfg["version"],
        "tool": tool_id,
        "chain": chain_key,
    }
    if method == "local_eip712" and cfg.get("chain_id"):
        extra["domain"] = {
            "name": cfg["name"],
            "version": cfg["version"],
            "chainId": cfg["chain_id"],
            "verifyingContract": pay_to,
        }
    elif method == "payai":
        extra["feePayer"] = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"
    elif method == "tron_selfverify":
        extra["tronNetwork"] = "mainnet"
        extra["trc20Tokens"] = cfg.get("tokens", {})
    elif method == "bitcoin_selfverify":
        extra["paymentNetwork"] = "bitcoin"
        extra["settlementChains"] = ["base", "solana"]
    elif method == "asterpay":
        extra["currency"] = "EUR"
        extra["sepa"] = True
    return extra
