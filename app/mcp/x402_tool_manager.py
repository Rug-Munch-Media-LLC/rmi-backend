"""x402 tool manager - loads and exposes tool bundles across free/trial/premium tiers."""

from app.core.logging import get_logger
from dataclasses import dataclass

from .x402_bundles import X402Bundle

logger = get_logger("x402_tool_manager")


@dataclass
class X402ToolManager:
    """Manages all x402 tool bundles including free/trial/premium."""

    def __init__(self):
        self._bundles = self._load_all_bundles()
        self._index = {b.bundle_id: b for b in self._bundles}
        self._index.update({b.name.lower(): b for b in self._bundles})
        logger.info("X402ToolManager initialized with %d bundles", len(self._bundles))

    def _load_all_bundles(self) -> list[X402Bundle]:
        """Load complete catalog: free, trial, standard, premium."""

        return [
            # ========== FREE TIER ==========
            X402Bundle(
                bundle_id="free-health",
                name="Free Health Check",
                description="Free system health and status endpoints",
                category="free",
                endpoints=[
                    {"method": "GET", "path": "/health", "description": "System health"},
                    {"method": "GET", "path": "/status", "description": "System status"},
                    {"method": "GET", "path": "/version", "description": "API version"},
                ],
                price_usd=0.0,
                tier="free",
                is_premium=False,
                requires_payment=False,
            ),
            X402Bundle(
                bundle_id="free-discovery",
                name="Free Tool Discovery",
                description="Discover all available tools without payment",
                category="free",
                endpoints=[
                    {"method": "GET", "path": "/.well-known/x402", "description": "x402 discovery"},
                    {"method": "GET", "path": "/v1/bundles", "description": "List all bundles"},
                    {"method": "GET", "path": "/v1/bundles/{id}", "description": "Bundle details"},
                ],
                price_usd=0.0,
                tier="free",
                is_premium=False,
                requires_payment=False,
            ),
            # ========== TRIAL TIER ==========
            X402Bundle(
                bundle_id="trial-security",
                name="Trial Security Suite",
                description="7-day trial of premium security tools",
                category="trial",
                endpoints=[
                    {"method": "POST", "path": "/scan/contract", "description": "Smart contract scan"},
                    {"method": "POST", "path": "/scan/token", "description": "Token risk scan"},
                    {"method": "POST", "path": "/scan/wallet", "description": "Wallet risk scan"},
                ],
                price_usd=0.0,
                tier="trial",
                is_premium=False,
                requires_payment=False,
            ),
            X402Bundle(
                bundle_id="trial-intelligence",
                name="Trial Intelligence Suite",
                description="7-day trial of market intelligence tools",
                category="trial",
                endpoints=[
                    {"method": "GET", "path": "/market/pulse", "description": "Token pulse"},
                    {"method": "GET", "path": "/market/trends", "description": "Market trends"},
                    {"method": "GET", "path": "/market/whales", "description": "Whale tracking"},
                ],
                price_usd=0.0,
                tier="trial",
                is_premium=False,
                requires_payment=False,
            ),
            # ========== STANDARD TIER ==========
            X402Bundle(
                bundle_id="token-pulse",
                name="Token Pulse",
                description="Real-time market momentum, volume, whale alerts",
                category="market",
                endpoints=[
                    {"method": "GET", "path": "/tokens", "description": "Token listings"},
                    {"method": "GET", "path": "/tokens/{address}", "description": "Token details"},
                    {
                        "method": "GET",
                        "path": "/lp/{address}",
                        "description": "Liquidity pool analysis",
                    },
                ],
                price_usd=0.01,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="url-scam-detector",
                name="URL Scam Detector",
                description="Detect phishing sites, fake docs, malicious redirects",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/scan/url", "description": "URL scan"},
                    {"method": "POST", "path": "/scan/domain", "description": "Domain analysis"},
                ],
                price_usd=0.01,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="wallet-profiler",
                name="Wallet Profiler",
                description="Full wallet analysis with persona detection",
                category="wallet",
                endpoints=[
                    {"method": "POST", "path": "/wallet/profile", "description": "Profile wallet"},
                    {
                        "method": "POST",
                        "path": "/wallet/history",
                        "description": "Transaction history",
                    },
                ],
                price_usd=0.05,
                tier="standard",
            ),
            X402Bundle(
                bundle_id="social-sentiment",
                name="Social Sentiment",
                description="Cross-platform sentiment analysis",
                category="market",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/sentiment/analyze",
                        "description": "Analyze sentiment",
                    },
                    {
                        "method": "GET",
                        "path": "/sentiment/trends",
                        "description": "Trending topics",
                    },
                ],
                price_usd=0.03,
                tier="standard",
            ),
            # ========== PREMIUM TIER ==========
            X402Bundle(
                bundle_id="suspicious-transfers",
                name="Suspicious Transfers Scanner",
                description="Cross-chain anomaly detection with RugMunch intelligence",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/scan/transfers", "description": "Scan transfers"},
                    {"method": "GET", "path": "/scan/results", "description": "Get results"},
                    {"method": "POST", "path": "/scan/anomaly", "description": "Anomaly detection"},
                ],
                price_usd=0.15,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="wallet-labeler",
                name="Wallet Labeler",
                description="Behavioral reputation engine (Arkham+Nansen+Alchemy)",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/label/wallet", "description": "Label wallet"},
                    {"method": "GET", "path": "/label/history", "description": "Label history"},
                    {
                        "method": "POST",
                        "path": "/label/cluster",
                        "description": "Cluster detection",
                    },
                ],
                price_usd=0.20,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="memory-bank",
                name="Memory Bank",
                description="Unified agent knowledge store with GCS export",
                category="ai",
                endpoints=[
                    {"method": "POST", "path": "/memory/store", "description": "Store knowledge"},
                    {"method": "GET", "path": "/memory/query", "description": "Query knowledge"},
                    {"method": "POST", "path": "/memory/export", "description": "Export to GCS"},
                ],
                price_usd=0.12,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="deep-contract-audit",
                name="Deep Contract Audit",
                description="Smart contract audit, honeypot detection, hidden mint functions",
                category="security",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/audit/contract",
                        "description": "Full contract audit",
                    },
                    {"method": "POST", "path": "/audit/honeypot", "description": "Honeypot check"},
                    {
                        "method": "POST",
                        "path": "/audit/proxy",
                        "description": "Proxy pattern check",
                    },
                ],
                price_usd=0.05,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="token-forensics",
                name="Token Forensics",
                description="Deep forensics from DexScreener, GeckoTerminal, CoinGecko",
                category="forensics",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/forensics/token",
                        "description": "Token forensics",
                    },
                    {
                        "method": "POST",
                        "path": "/forensics/report",
                        "description": "Generate report",
                    },
                ],
                price_usd=0.10,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="whale-decoder",
                name="Whale Decoder",
                description="Advanced whale wallet analysis across chains",
                category="wallet",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/whale/decode",
                        "description": "Decode whale strategy",
                    },
                    {"method": "GET", "path": "/whale/positions", "description": "Track positions"},
                ],
                price_usd=0.15,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="darkroom-security",
                name="Darkroom Security Suite",
                description="Advanced security analysis, threat detection, forensics",
                category="security",
                endpoints=[
                    {"method": "POST", "path": "/security/scan", "description": "Security scan"},
                    {
                        "method": "POST",
                        "path": "/security/threats",
                        "description": "Threat database",
                    },
                    {
                        "method": "POST",
                        "path": "/security/forensics",
                        "description": "Forensic analysis",
                    },
                ],
                price_usd=0.20,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="solana-mcp",
                name="Solana MCP Server",
                description="Native Solana blockchain queries via MCP protocol",
                category="mcp",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/mcp/solana/balance",
                        "description": "Get SOL balance",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/transaction",
                        "description": "Get transaction",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/tokens",
                        "description": "Get token accounts",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/solana/simulate",
                        "description": "Simulate transaction",
                    },
                ],
                price_usd=0.08,
                tier="premium",
                is_premium=True,
            ),
            X402Bundle(
                bundle_id="ethereum-mcp",
                name="Ethereum MCP Server",
                description="Native Ethereum blockchain queries via MCP protocol",
                category="mcp",
                endpoints=[
                    {
                        "method": "POST",
                        "path": "/mcp/eth/balance",
                        "description": "Get ETH balance",
                    },
                    {
                        "method": "POST",
                        "path": "/mcp/eth/transaction",
                        "description": "Get transaction",
                    },
                    {"method": "POST", "path": "/mcp/eth/call", "description": "Call contract"},
                    {"method": "POST", "path": "/mcp/eth/logs", "description": "Get event logs"},
                    {"method": "POST", "path": "/mcp/eth/ens", "description": "Resolve ENS"},
                ],
                price_usd=0.08,
                tier="premium",
                is_premium=True,
            ),
        ]

    def list_bundles(self, tier: str | None = None) -> list[X402Bundle]:
        """List all or filtered bundles."""
        if tier:
            return [b for b in self._bundles if b.tier == tier]
        return self._bundles

    def get_bundle(self, identifier: str) -> X402Bundle | None:
        return self._index.get(identifier.lower())

    @property
    def free_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "free"]

    @property
    def trial_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.tier == "trial"]

    @property
    def premium_bundles(self) -> list[X402Bundle]:
        return [b for b in self._bundles if b.is_premium]

    @property
    def total_endpoints(self) -> int:
        return sum(len(b.endpoints) for b in self._bundles)
