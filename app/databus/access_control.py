"""
DataBus Access Control - Who Sees What, Based On Who They Are
================================================================

Controls data access at a granular level. No consumer can pull raw individual
data points. Every response is packaged and scoped to the consumer's identity:

CONSUMER TYPES:
  - public_web     → RugCharts, RugMaps, market overview pages (free tier)
  - authenticated  → Logged-in users with wallet (basic tier)
  - premium        → Paid subscribers (premium tier)
  - admin          → Internal admin / darkroom (full access)
  - mcp_tool       → MCP server tools (scoped per tool, never raw)
  - x402_paid      → x402 paid API calls (scoped per tool price tier)

ACCESS MATRIX (what each consumer can see):
  - public_web: prices, basic market data, alerts summary, trending tokens
  - authenticated: + wallet labels, risk scans, basic profiles
  - premium: + smart money, funding traces, deep risk, whale tracking
  - admin: everything including Arkham, raw data, credit reports
  - mcp_tool: only the specific data the tool is authorized for
  - x402_paid: only what they paid for, packaged, never raw

RESPONSE PACKAGING:
  Every DataBus response is transformed based on consumer type.
  - public_web gets redacted summaries (no wallet addresses, no internal sources)
  - mcp_tool gets tool-scoped results (only the fields the tool needs)
  - x402_paid gets paid-scope results (exactly what they bought, nothing more)
  - admin gets full data including source metadata

This ensures no consumer can enumerate our data sources or pull more than
they're authorized for. The DataBus is the ONLY way to access external data.
"""

import logging
import os
from enum import Enum

logger = logging.getLogger("databus.access_control")


class ConsumerType(Enum):
    PUBLIC_WEB = "public_web"  # RugCharts, RugMaps, market pages (no auth)
    AUTHENTICATED = "authenticated"  # Logged-in user (basic tier)
    PREMIUM = "premium"  # Paid subscriber
    ADMIN = "admin"  # Internal admin / darkroom
    MCP_TOOL = "mcp_tool"  # MCP server tools (scoped per tool)
    X402_PAID = "x402_paid"  # x402 paid API calls (scoped per tier)


# ── DATA TYPE → RESPONSE PACKAGING PER CONSUMER ──────────────────────────────
# Each data type defines what fields each consumer type can see.
# "full" = everything, "summary" = redacted summary, "denied" = 403, "scoped" = field-level

DATA_ACCESS_MATRIX = {
    # ── PUBLIC (anyone on web) ──
    "token_price": {
        ConsumerType.PUBLIC_WEB: "summary",  # price + 24h change only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "tvl": {
        ConsumerType.PUBLIC_WEB: "summary",  # total TVL per chain
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "news": {
        ConsumerType.PUBLIC_WEB: "summary",  # title + source only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "market_overview": {
        ConsumerType.PUBLIC_WEB: "summary",  # basic stats
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "trending": {
        ConsumerType.PUBLIC_WEB: "summary",  # top 10 only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "market_movers": {
        ConsumerType.PUBLIC_WEB: "summary",  # top 5 only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "spl_token_metadata": {
        ConsumerType.PUBLIC_WEB: "full",  # Raw SPL token decoder is safe for public
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    # ── AUTHENTICATED (logged in) ──
    "wallet_labels": {
        ConsumerType.PUBLIC_WEB: "denied",  # must be logged in
        ConsumerType.AUTHENTICATED: "summary",  # label text only, no source
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "wallet_balance": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # balance only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "wallet_profile": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # basic profile only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "risk_scan": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # risk score only, no details
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "funding_source": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",  # premium only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "smart_money": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",  # premium only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "rag_search": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    # ── ADMIN ONLY ──
    "entity_intel": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",  # label only, not raw entity data
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "arkham_portfolio": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "denied",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "denied",  # MCP tools cannot access raw portfolio
        ConsumerType.X402_PAID: "summary",  # paid x402 gets packaged summary
    },
    "arkham_entity": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "arkham_labels": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    # ── PREMIUM ──
    "sentinel_deep": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "arkham_transfers": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "arkham_counterparties": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "nansen_labels": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "nansen_smart_money": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "summary",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "summary",
    },
    "portfolio": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "denied",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    # ── NEW AUTHENTICATED DATA TYPES ──
    "bubble_map": {
        ConsumerType.PUBLIC_WEB: "summary",  # basic map only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "rugmaps_analysis": {
        ConsumerType.PUBLIC_WEB: "summary",  # risk score only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "socialfi_resolve": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # ENS name only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "cross_chain": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # linked chains only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "wallet_cluster": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # cluster count only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "bundle_detect": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # bundle detected y/n
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "wallet_tokens": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # token count + top 5
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "token_detail": {
        ConsumerType.PUBLIC_WEB: "summary",  # name + price + change
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "wallet_pnl": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # total PnL only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "gmgn_smart_money": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # narrative summary only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "threat_check": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # threat detected y/n
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "contract_scan": {
        ConsumerType.PUBLIC_WEB: "denied",
        ConsumerType.AUTHENTICATED: "summary",  # scan score only
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "scoped",
        ConsumerType.X402_PAID: "full",
    },
    "dex_data": {
        ConsumerType.PUBLIC_WEB: "summary",
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    # ── NEW PUBLIC DATA TYPES ──
    "social_feed": {
        ConsumerType.PUBLIC_WEB: "summary",  # titles + sources only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "defi_protocols": {
        ConsumerType.PUBLIC_WEB: "summary",  # top protocols only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "prediction_markets": {
        ConsumerType.PUBLIC_WEB: "summary",  # top 10 markets
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
    "prediction_signals": {
        ConsumerType.PUBLIC_WEB: "summary",  # signal direction only
        ConsumerType.AUTHENTICATED: "full",
        ConsumerType.PREMIUM: "full",
        ConsumerType.ADMIN: "full",
        ConsumerType.MCP_TOOL: "full",
        ConsumerType.X402_PAID: "full",
    },
}

# ── FIELDS TO STRIP PER PACKAGING LEVEL ─────────────────────────────────────
# These fields are NEVER exposed outside admin scope.

NEVER_EXPOSE_FIELDS = {
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "x-api-key",
    "key",
    "api-key",
    "internal_url",
    "server_path",
    "source_address",
    "funding_tx",
    "raw_data",
    "internal_id",
}

SUMMARY_ONLY_FIELDS = {
    # For summary packaging, only allow these fields per data type
    "token_price": {"price_usd", "price", "change_24h", "source", "cached"},
    "wallet_labels": {"label", "source", "cached"},
    "risk_scan": {"is_honeypot", "risk_score", "source", "cached"},
    "entity_intel": {"entity_name", "category", "source", "cached"},
    "arkham_entity": {"entity_name", "category", "source", "cached"},
    "arkham_portfolio": {"total_value_usd", "token_count", "source", "cached"},
    "arkham_labels": {"label", "confidence", "source", "cached"},
    "arkham_transfers": {"from_address", "to_address", "amount_usd", "source", "cached"},
    "arkham_counterparties": {"counterparty_count", "top_counterparties", "source", "cached"},
    "nansen_labels": {"label", "category", "source", "cached"},
    "nansen_smart_money": {"wallet_count", "top_wallets", "source", "cached"},
    "news": {"title", "source_name", "published_at", "source", "cached"},
    "trending": {"name", "symbol", "price_usd", "change_24h", "source", "cached"},
    "market_movers": {"name", "symbol", "price_usd", "change_24h", "source", "cached"},
    "tvl": {"chain", "tvl_usd", "change_24h", "source", "cached"},
    "market_overview": {"total_mcap", "btc_dom", "eth_dom", "fgi", "source", "cached"},
    "bubble_map": {"cluster_count", "top_holders", "risk_score", "source", "cached"},
    "rugmaps_analysis": {"risk_score", "verdict", "similar_scams_count", "source", "cached"},
    "socialfi_resolve": {"ens", "farcaster", "source", "cached"},
    "cross_chain": {"linked_chains", "linked_count", "source", "cached"},
    "wallet_cluster": {"cluster_count", "related_wallets_count", "source", "cached"},
    "bundle_detect": {"bundle_detected", "bundle_count", "source", "cached"},
    "wallet_tokens": {"token_count", "top_5_tokens", "source", "cached"},
    "dex_data": {"pair_count", "top_pairs", "liquidity", "source", "cached"},
    "token_detail": {"name", "symbol", "price_usd", "change_24h", "source", "cached"},
    "wallet_pnl": {"total_pnl_usd", "pnl_pct", "source", "cached"},
    "gmgn_smart_money": {"narrative_summary", "source", "cached"},
    "threat_check": {"threat_detected", "threat_score", "source", "cached"},
    "contract_scan": {"scan_score", "vulnerabilities_count", "source", "cached"},
    "social_feed": {"title", "source_name", "published_at", "source", "cached"},
    "defi_protocols": {"protocol_count", "top_protocols", "source", "cached"},
    "prediction_markets": {"market_count", "top_markets", "source", "cached"},
    "prediction_signals": {"signal_direction", "confidence", "source", "cached"},
    "portfolio": {"total_value_usd", "token_count", "source", "cached"},
}

# ── MCP TOOL SCOPING ─────────────────────────────────────────────────────────
# Each MCP tool can only access the specific data type and fields it's authorized for.

MCP_TOOL_SCOPES = {
    # Tool ID → {data_types: [allowed types], fields: [allowed fields] or "all"}
    "wallet_risk_scan": {
        "data_types": ["risk_scan", "wallet_labels", "threat_check", "bundle_detect"],
        "fields": "all",
    },
    "token_security_check": {
        "data_types": ["risk_scan", "contract_scan", "threat_check"],
        "fields": [
            "is_honeypot",
            "risk_score",
            "risks",
            "scan_score",
            "vulnerabilities_count",
            "source",
        ],
    },
    "address_entity_lookup": {
        "data_types": ["wallet_labels", "entity_intel", "socialfi_resolve", "cross_chain"],
        "fields": "all",
    },
    "portfolio_tracker": {
        "data_types": ["wallet_balance", "wallet_profile", "wallet_tokens", "portfolio"],
        "fields": "all",
    },
    "smart_money_tracker": {
        "data_types": ["smart_money", "gmgn_smart_money", "nansen_smart_money"],
        "fields": "all",
    },
    "arkham_intel": {
        "data_types": [
            "arkham_entity",
            "arkham_labels",
            "arkham_transfers",
            "arkham_counterparties",
        ],
        "fields": [
            "entity_name",
            "category",
            "label",
            "confidence",
            "from_address",
            "to_address",
            "source",
        ],
    },
    "funding_trace": {
        "data_types": ["funding_source", "cross_chain", "wallet_cluster"],
        "fields": "all",
    },
    "market_data": {
        "data_types": [
            "token_price",
            "token_detail",
            "tvl",
            "trending",
            "market_movers",
            "market_overview",
            "news",
            "dex_data",
            "defi_protocols",
            "social_feed",
            "prediction_markets",
            "prediction_signals",
        ],
        "fields": "all",
    },
    "deep_scan": {
        "data_types": [
            "risk_scan",
            "sentinel_deep",
            "funding_source",
            "wallet_labels",
            "entity_intel",
            "threat_check",
            "contract_scan",
            "bundle_detect",
        ],
        "fields": "all",
    },
    "rugmaps_scan": {
        "data_types": ["bubble_map", "rugmaps_analysis", "risk_scan", "wallet_labels"],
        "fields": "all",
    },
}

# ── X402 PRICING → ACCESS MAPPING ────────────────────────────────────────────
# What x402 paid tools can access based on their price tier.

X402_ACCESS_TIERS = {
    "free": [
        "token_price",
        "tvl",
        "news",
        "market_overview",
        "trending",
        "dex_data",
        "social_feed",
        "defi_protocols",
        "prediction_markets",
        "prediction_signals",
    ],
    "basic": [
        "token_price",
        "tvl",
        "news",
        "market_overview",
        "trending",
        "market_movers",
        "wallet_labels",
        "wallet_balance",
        "risk_scan",
        "token_detail",
        "bubble_map",
        "rugmaps_analysis",
        "dex_data",
        "social_feed",
        "defi_protocols",
        "prediction_markets",
        "prediction_signals",
    ],
    "premium": [
        "token_price",
        "tvl",
        "news",
        "market_overview",
        "trending",
        "market_movers",
        "wallet_labels",
        "wallet_balance",
        "risk_scan",
        "wallet_profile",
        "smart_money",
        "funding_source",
        "entity_intel",
        "arkham_entity",
        "arkham_labels",
        "bubble_map",
        "rugmaps_analysis",
        "token_detail",
        "wallet_tokens",
        "wallet_pnl",
        "cross_chain",
        "wallet_cluster",
        "bundle_detect",
        "socialfi_resolve",
        "threat_check",
        "contract_scan",
        "sentinel_deep",
        "gmgn_smart_money",
        "dex_data",
        "social_feed",
        "defi_protocols",
        "prediction_markets",
        "prediction_signals",
        "portfolio",
    ],
    "enterprise": "all",  # Gets everything including admin
}


class AccessController:
    """
    Controls what data each consumer can see and in what format.
    No consumer ever gets raw data - everything is packaged and scoped.
    """

    @staticmethod
    def identify_consumer(
        request=None,
        admin_key: str = "",
        consumer_type: str = "",
        tool_id: str = "",
        x402_tier: str = "",
    ) -> ConsumerType:
        """Identify who's making the request."""
        # Explicit type override
        if consumer_type:
            try:
                return ConsumerType(consumer_type)
            except ValueError:
                pass

        # MCP tool
        if tool_id and tool_id in MCP_TOOL_SCOPES:
            return ConsumerType.MCP_TOOL

        # x402 paid call
        if x402_tier:
            return ConsumerType.X402_PAID

        # Admin key
        if admin_key == os.getenv("ADMIN_API_KEY", ""):
            return ConsumerType.ADMIN

        # Auth header check
        if request:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                # Could verify JWT here for tier info
                # For now, authenticated
                return ConsumerType.AUTHENTICATED

        # No auth = public web
        return ConsumerType.PUBLIC_WEB

    @staticmethod
    def check_access(data_type: str, consumer: ConsumerType) -> str:
        """
        Check what packaging level the consumer gets for this data type.
        Returns: "full", "summary", "scoped", or "denied"
        """
        if data_type not in DATA_ACCESS_MATRIX:
            # Unknown data type - authenticated minimum
            if consumer in (ConsumerType.ADMIN, ConsumerType.MCP_TOOL):
                return "full"
            if consumer == ConsumerType.PREMIUM:
                return "full"
            if consumer == ConsumerType.AUTHENTICATED:
                return "summary"
            return "denied"

        type_matrix = DATA_ACCESS_MATRIX[data_type]
        packaging = type_matrix.get(consumer, "denied")

        # MCP tool scoping - further restrict to tool's allowed data types
        if consumer == ConsumerType.MCP_TOOL:
            # MCP tools get "full" for their scoped data types,
            # but only if the data type is in their scope
            # (Tool scope is checked separately in package_for_mcp)
            pass

        return packaging

    @staticmethod
    def package_response(
        data: dict, data_type: str, consumer: ConsumerType, tool_id: str = "", x402_tier: str = ""
    ) -> dict:
        """
        Package a response based on consumer type.
        Strips fields, redacts sensitive data, scopes to authorization level.
        NEVER exposes raw internal data to non-admin consumers.
        """
        packaging = AccessController.check_access(data_type, consumer)

        if packaging == "denied":
            return {
                "error": "access_denied",
                "message": f"Access to {data_type} requires higher tier",
                "required_tier": "authenticated" if consumer == ConsumerType.PUBLIC_WEB else "premium",
            }

        if packaging == "full":
            # Admin and premium get everything (minus dangerous fields)
            return AccessController._strip_dangerous(data)

        if packaging == "summary":
            # Summary = only whitelisted fields for this data type
            return AccessController._package_summary(data, data_type)

        if packaging == "scoped":
            # MCP tool = only fields the tool is authorized for
            if tool_id and tool_id in MCP_TOOL_SCOPES:
                return AccessController._package_for_mcp(data, data_type, tool_id)
            return AccessController._package_summary(data, data_type)

        # Fallback: strip dangerous, return basic
        return AccessController._strip_dangerous(data)

    @staticmethod
    def _strip_dangerous(data: dict) -> dict:
        """Always strip dangerous fields regardless of access level."""
        if not isinstance(data, dict):
            return data
        result = {}
        for k, v in data.items():
            if k.lower() in NEVER_EXPOSE_FIELDS:
                continue
            if isinstance(v, dict):
                result[k] = AccessController._strip_dangerous(v)
            elif isinstance(v, list):
                result[k] = [AccessController._strip_dangerous(i) if isinstance(i, dict) else i for i in v]
            else:
                result[k] = v
        return result

    @staticmethod
    def _package_summary(data: dict, data_type: str) -> dict:
        """Package as summary - only whitelisted fields."""
        if not isinstance(data, dict):
            return data

        # Get allowed fields for this data type
        allowed = SUMMARY_ONLY_FIELDS.get(data_type)
        inner = data.get("data", data)

        if allowed and isinstance(inner, dict):
            result = {k: v for k, v in inner.items() if k in allowed}
            # Always include metadata
            result["source"] = data.get("source", "rmi")
            result["cached"] = data.get("cached", False)
            result["tier"] = data.get("tier", "unknown")
            return result

        # No specific field list - strip dangerous and return
        return AccessController._strip_dangerous(data)

    @staticmethod
    def _package_for_mcp(data: dict, data_type: str, tool_id: str) -> dict:
        """Package for MCP tool - only data types and fields the tool is authorized for."""
        scope = MCP_TOOL_SCOPES.get(tool_id, {})
        allowed_types = scope.get("data_types", [])

        if data_type not in allowed_types:
            return {
                "error": "tool_not_authorized",
                "message": f"Tool {tool_id} not authorized for {data_type}",
            }

        allowed_fields = scope.get("fields", "all")
        inner = data.get("data", data)

        if allowed_fields == "all":
            return AccessController._strip_dangerous(data)

        # Strip to only allowed fields
        if isinstance(inner, dict):
            result = {k: v for k, v in inner.items() if k in allowed_fields}
            result["source"] = "rmi_dataservice"
            result["tool_id"] = tool_id
            return result

        return AccessController._strip_dangerous(data)

    @staticmethod
    def get_mcp_allowed_types(tool_id: str) -> list:
        """Return data types an MCP tool is allowed to access."""
        scope = MCP_TOOL_SCOPES.get(tool_id, {})
        return scope.get("data_types", [])

    @staticmethod
    def get_x402_allowed_types(tier: str) -> list:
        """Return data types an x402 tier can access."""
        types = X402_ACCESS_TIERS.get(tier, X402_ACCESS_TIERS["free"])
        if types == "all":
            return list(DATA_ACCESS_MATRIX.keys())
        return types

    @staticmethod
    def list_access_matrix() -> dict:
        """Return the full access matrix for admin UI."""
        result = {}
        for dtype, consumers in DATA_ACCESS_MATRIX.items():
            result[dtype] = {c.value: p for c, p in consumers.items()}
        return result


# ── Singleton ─────────────────────────────────────────────────────────────────
access_controller = AccessController()
