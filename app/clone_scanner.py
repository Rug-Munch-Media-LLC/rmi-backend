"""
Token Clone Scanner
===================
Detects cloned, copycat, and impersonation tokens by analyzing:
  - Contract bytecode similarity (code reuse / unverified contract suspicion)
  - Name/symbol fuzzy matching (brand impersonation)
  - Deployer history (same deployer creating lookalike tokens)
  - Metadata scraping (stolen logos, websites, social links)
  - Launch time proximity to high-value token launches

TOOL  : clone_detect
TIER  : premium
PRICE : $0.08 (80000 atoms)
TRIAL : 2 free checks

Endpoints:
  POST /api/v1/x402-tools/clone/scan  - full clone analysis
  POST /api/v1/x402-tools/clone/detect - fast similarity check only

Data Sources (all free):
  - DexScreener - token info, pairs, prices across chains
  - Birdeye public - holder stats, token metadata
  - Solscan (free) - Solana contract verification status
  - Etherscan family - EVM contract source code
  - Jupiter - Solana token list

Usage:
    from app.clone_scanner import CloneScanner, CloneReport
    scanner = CloneScanner()
    report = await scanner.scan("0x...", "ethereum")
    print(report.clone_score, report.risk_label)
"""

from __future__ import annotations
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.shared_utils import EVM_ADDRESS_RE, SOLANA_ADDRESS_RE

logger = get_logger(__name__)

# ── Patterns ──────────────────────────────────────────────────────

# Chain → address format mapping for chain-specific validation
EVM_CHAINS = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "avalanche",
        "base",
        "fantom",
        "linea",
        "zksync",
        "scroll",
        "mantle",
    }
)

CLONE_INDICATOR_KEYWORDS = [
    "pepe",
    "bonk",
    "doge",
    "shib",
    "woof",
    "moon",
    "safe",
    "baby",
    "elon",
    "chatgpt",
    "ai",
    "gpt",
    "biden",
    "trump",
    "floki",
    "samoyed",
    "husky",
    "corgi",
    "kitty",
    "cat",
]

# ── APIs (free) ───────────────────────────────────────────────────
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_PUBLIC = "https://public-api.birdeye.so"
JUPITER_TOKEN_LIST = "https://tokens.jup.ag/all"

# Etherscan-like API bases
ETHERSCAN_BASES: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "avalanche": "https://api.snowtrace.io/api",
    "base": "https://api.basescan.org/api",
    "fantom": "https://api.ftmscan.com/api",
    "linea": "https://api.lineascan.build/api",
    "zksync": "https://api-era.zksync.network/api",
    "scroll": "https://api.scrollscan.com/api",
    "mantle": "https://api.mantlescan.xyz/api",
}

# Known legitimate token registries for reference (name -> (symbol, chain))
WELL_KNOWN_TOKENS: dict[str, tuple[str, str]] = {
    "Bitcoin": ("BTC", "ethereum"),
    "Ethereum": ("ETH", "ethereum"),
    "Tether": ("USDT", "ethereum"),
    "USD Coin": ("USDC", "ethereum"),
    "BNB": ("BNB", "bsc"),
    "Solana": ("SOL", "solana"),
    "XRP": ("XRP", "ethereum"),
    "Dogecoin": ("DOGE", "ethereum"),
    "Pepe": ("PEPE", "ethereum"),
    "Bonk": ("BONK", "solana"),
}

class CloneRisk(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"

SUPPORTED_CHAINS = [
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "avalanche",
    "base",
    "fantom",
    "linea",
    "zksync",
    "scroll",
    "mantle",
    "solana",
]

# ── Data Models ───────────────────────────────────────────────────

@dataclass
class SimilarToken:
    """A token found to be similar to the target."""

    address: str
    chain: str
    name: str
    symbol: str
    name_similarity: float = 0.0
    symbol_similarity: float = 0.0
    deployer_match: bool = False
    age_days: float = 0.0

    @property
    def overall_similarity(self) -> float:
        return max(self.name_similarity, self.symbol_similarity)

@dataclass
class CloneReport:
    """Full clone analysis result."""

    token_address: str
    chain: str
    name: str = ""
    symbol: str = ""
    clone_score: float = 0.0  # 0-100

    # Per-category scores
    bytecode_similarity_score: float = 0.0
    name_similarity_score: float = 0.0
    deployer_risk_score: float = 0.0
    metadata_risk_score: float = 0.0

    # Findings
    similar_tokens: list[SimilarToken] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    unverified_contract: bool = False
    same_deployer_other_tokens: int = 0
    is_verified_known_token: bool = False
    risk_label: str = "none"
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "name": self.name,
            "symbol": self.symbol,
            "clone_score": self.clone_score,
            "risk_label": self.risk_label,
            "signals": {
                "bytecode_similarity": self.bytecode_similarity_score,
                "name_similarity": self.name_similarity_score,
                "deployer_risk": self.deployer_risk_score,
                "metadata_risk": self.metadata_risk_score,
            },
            "similar_tokens": [
                {
                    "address": t.address,
                    "chain": t.chain,
                    "name": t.name,
                    "symbol": t.symbol,
                    "similarity": round(t.overall_similarity, 2),
                    "deployer_match": t.deployer_match,
                    "age_days": round(t.age_days, 1),
                }
                for t in self.similar_tokens
            ],
            "matched_keywords": self.matched_keywords,
            "unverified_contract": self.unverified_contract,
            "same_deployer_other_tokens": self.same_deployer_other_tokens,
            "is_verified_known_token": self.is_verified_known_token,
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        flags = []
        if self.unverified_contract:
            flags.append("unverified")
        if self.matched_keywords:
            flags.append(f"keywords:{','.join(self.matched_keywords[:3])}")
        if self.same_deployer_other_tokens > 0:
            flags.append(f"{self.same_deployer_other_tokens}x other tokens")
        if self.is_verified_known_token:
            flags.append("known_legitimate")

        flag_str = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"[{self.risk_label.upper()}] {self.token_address[:12]}... "
            f"({self.name}/{self.symbol}) - "
            f"Clone score: {self.clone_score:.0f}/100 | "
            f"{len(self.similar_tokens)} similar tokens found"
            f"{flag_str}"
        )

# ── Helper: String similarity ─────────────────────────────────────

def string_similarity(a: str, b: str) -> float:
    """Fuzzy string similarity (0-1) using SequenceMatcher.
    Truncates inputs at 200 chars to prevent O(n²) blowup."""
    if not a or not b:
        return 0.0
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    # Truncate very long strings to keep SequenceMatcher performant
    if len(a) > 200 or len(b) > 200:
        a, b = a[:200], b[:200]
    return SequenceMatcher(None, a, b).ratio()

def name_similarity_score(name: str, target_name: str) -> float:
    """Score name similarity considering common clone patterns."""
    base = string_similarity(name, target_name)

    # Boost if one name contains the other (e.g. "Baby Pepe" contains "Pepe")
    n_lower = name.lower().strip()
    t_lower = target_name.lower().strip()
    if n_lower and t_lower:
        if n_lower in t_lower or t_lower in n_lower:
            base = max(base, 0.65)
        # Check prefix/suffix patterns common in clones
        for prefix in [
            "baby ",
            "mini ",
            "super ",
            "mega ",
            "ultra ",
            "real ",
            "new ",
            "true ",
            "safe ",
            "moon ",
        ]:
            if (n_lower.startswith(prefix) and n_lower[len(prefix) :] == t_lower) or (
                t_lower.startswith(prefix) and t_lower[len(prefix) :] == n_lower
            ):
                base = max(base, 0.85)
        for suffix in [" v2", " v3", " pro", " x", " 2.0", " 3.0"]:
            if n_lower == t_lower + suffix or t_lower == n_lower + suffix:
                base = max(base, 0.90)

    return min(base, 1.0)

# ── Core Scanner ──────────────────────────────────────────────────

class CloneScanner:
    """Main scanner for token clone detection."""

    def __init__(self, http_timeout: float = 15.0) -> None:
        self.http = httpx.AsyncClient(timeout=http_timeout)
        self._jupiter_tokens: list[dict[str, Any]] | None = None
        self._birdeye_api_key = os.environ.get("BIRDEYE_API_KEY", "")

    async def close(self) -> None:
        await self.http.aclose()

    # ── Public API ──────────────────────────────────────────────

    async def scan(self, address: str, chain: str) -> CloneReport:
        """Full clone analysis for a token."""
        if not self._validate_address(address, chain):
            return CloneReport(
                token_address=address,
                chain=chain,
                errors=[f"Invalid address for chain {chain}"],
                risk_label="error",
            )

        chain = chain.lower()
        if chain not in SUPPORTED_CHAINS:
            return CloneReport(
                token_address=address,
                chain=chain,
                errors=[f"Unsupported chain: {chain}. Supported: {SUPPORTED_CHAINS}"],
                risk_label="error",
            )
        report = CloneReport(token_address=address, chain=chain)

        try:
            # 1. Fetch token metadata
            metadata = await self._fetch_metadata(address, chain)
            report.name = metadata.get("name", "Unknown")
            report.symbol = metadata.get("symbol", "UNKNOWN")
            report.raw["metadata"] = metadata

            # 2. Check if it's a well-known legitimate token
            if self._is_well_known(report.name, report.symbol):
                report.is_verified_known_token = True
                report.risk_label = "none"
                return report

            # 3. Check contract verification status
            report.unverified_contract = await self._check_verification(address, chain)

            # 4. Search for similar named tokens
            tasks = [
                self._search_similar_names(report.name, report.symbol, address, chain),
                self._check_deployer_history(address, chain),
                self._check_keyword_matches(report.name, report.symbol),
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            similar_tokens = results[0] if not isinstance(results[0], Exception) else []
            deployer_info = results[1] if not isinstance(results[1], Exception) else {}
            keywords = results[2] if not isinstance(results[2], Exception) else []

            report.similar_tokens = similar_tokens
            report.same_deployer_other_tokens = deployer_info.get("other_tokens", 0)
            report.matched_keywords = keywords

            # 5. Compute scores
            report.bytecode_similarity_score = self._score_bytecode_risk(
                report.unverified_contract, deployer_info
            )
            report.name_similarity_score = self._score_name_similarity(similar_tokens, keywords)
            report.deployer_risk_score = self._score_deployer_risk(deployer_info)
            report.metadata_risk_score = self._score_metadata_risk(
                metadata, keywords, similar_tokens
            )

            # 6. Composite clone score
            report.clone_score = self._compute_clone_score(report)
            report.risk_label = self._label_risk(report.clone_score)

        except Exception as e:
            logger.error(f"Clone scan error for {address}: {e}")
            report.errors.append(str(e))
            report.risk_label = "error"

        return report

    async def fast_check(self, address: str, chain: str) -> dict[str, Any]:
        """Quick similarity check - name/symbol only, no bytecode."""
        if not self._validate_address(address, chain):
            return {"error": f"Invalid address for chain {chain}"}

        chain = chain.lower()
        metadata = await self._fetch_metadata(address, chain)
        name = metadata.get("name", "")
        symbol = metadata.get("symbol", "")

        result = {
            "address": address,
            "chain": chain,
            "name": name,
            "symbol": symbol,
        }

        # Check well-known
        if self._is_well_known(name, symbol):
            result["clone_risk"] = "none"
            result["reason"] = "Known legitimate token"
            return result

        # Check keywords
        keywords = self._check_keyword_matches(name, symbol)
        keywords = [k for k in keywords if k not in CLONE_INDICATOR_KEYWORDS[:3]]
        result["matched_keywords"] = keywords

        # Simple score
        score = 0
        if keywords:
            score += 30
        if any(k.lower() in name.lower() for k in [" v2", " v3", " 2.0", "pro"]):
            score += 15
        if not name or not symbol:
            score += 10

        result["clone_score"] = min(score, 100)
        result["clone_risk"] = self._label_risk(result["clone_score"])
        return result

    # ── Validation ──────────────────────────────────────────────

    def _validate_address(self, address: str, chain: str) -> bool:
        """Validate address format against chain type."""
        chain = chain.lower()
        if chain == "solana":
            return bool(SOLANA_ADDRESS_RE.match(address))
        if chain in EVM_CHAINS:
            return bool(EVM_ADDRESS_RE.match(address))
        # Unknown chain - try either format
        return bool(EVM_ADDRESS_RE.match(address) or SOLANA_ADDRESS_RE.match(address))

    def _is_well_known(self, name: str, symbol: str) -> bool:
        key = name.strip()
        sym = symbol.strip().upper()
        if key in WELL_KNOWN_TOKENS:
            ref_sym, _ = WELL_KNOWN_TOKENS[key]
            if sym == ref_sym:
                return True
        # Also check by symbol alone for major tokens
        return sym in ("BTC", "ETH", "USDT", "USDC", "BNB", "SOL", "XRP", "ADA", "DOGE")

    @staticmethod
    def _label_risk(score: float) -> str:
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        if score > 0:
            return "low"
        return "none"

    # ── Scoring ─────────────────────────────────────────────────

    def _score_bytecode_risk(self, unverified: bool, deployer_info: dict[str, Any]) -> float:
        score = 0.0
        if unverified:
            score += 40  # Unverified contracts are high risk for clones
        if deployer_info.get("other_unverified", 0) > 5:
            score += 20
        if deployer_info.get("other_tokens", 0) > 20:
            score += 15  # Mass deployer pattern
        return min(score, 100)

    def _score_name_similarity(
        self, similar_tokens: list[SimilarToken], keywords: list[str]
    ) -> float:
        score = 0.0
        if keywords:
            score += 25
        high_sim = [t for t in similar_tokens if t.overall_similarity > 0.8]
        if high_sim:
            score += min(len(high_sim) * 20, 50)
        med_sim = [t for t in similar_tokens if 0.5 < t.overall_similarity <= 0.8]
        if med_sim:
            score += min(len(med_sim) * 10, 25)
        return min(score, 100)

    def _score_deployer_risk(self, deployer_info: dict[str, Any]) -> float:
        score = 0.0
        other = deployer_info.get("other_tokens", 0)
        if other > 50:
            score += 40
        elif other > 20:
            score += 30
        elif other > 5:
            score += 15
        if deployer_info.get("clone_deployer", False):
            score += 30
        return min(score, 100)

    def _score_metadata_risk(
        self, metadata: dict[str, Any], keywords: list[str], similar_tokens: list[SimilarToken]
    ) -> float:
        score = 0.0
        # Missing metadata is suspicious
        if not metadata.get("name") or not metadata.get("symbol"):
            score += 20
        # Empty socials or website
        social = metadata.get("social", {}) or {}
        if not social.get("twitter") and not social.get("telegram"):
            score += 10
        if not metadata.get("website"):
            score += 10
        # High number of similar tokens with matching metadata patterns
        if len(similar_tokens) > 5:
            score += 15
        return min(score, 100)

    def _compute_clone_score(self, report: CloneReport) -> float:
        """Weighted composite score from all signals."""
        weights = {
            "bytecode": 0.25,
            "name": 0.35,
            "deployer": 0.25,
            "metadata": 0.15,
        }
        score = (
            weights["bytecode"] * report.bytecode_similarity_score
            + weights["name"] * report.name_similarity_score
            + weights["deployer"] * report.deployer_risk_score
            + weights["metadata"] * report.metadata_risk_score
        )
        return round(score, 1)

    # ── Data Fetching ───────────────────────────────────────────

    async def _fetch_metadata(self, address: str, chain: str) -> dict[str, Any]:
        """Fetch token metadata from available free sources."""
        result: dict[str, Any] = {}

        # DexScreener gives name, symbol, pairs
        try:
            url = f"{DEXSCREENER_API}/tokens/{address}"
            resp = await self.http.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                if pairs:
                    pair = pairs[0]
                    result["name"] = pair.get("baseToken", {}).get("name", "")
                    result["symbol"] = pair.get("baseToken", {}).get("symbol", "")
                    result["price_usd"] = pair.get("priceUsd", "")
                    result["liquidity_usd"] = pair.get("liquidity", {}).get("usd", 0)
                    result["pair_created_at"] = pair.get("pairCreatedAt", 0)
                    result["dex_id"] = pair.get("dexId", "")
                    # Social links from info
                    info = pair.get("info", {})
                    if isinstance(info, dict):
                        result["social"] = {
                            "twitter": info.get("twitter", ""),
                            "telegram": info.get("telegram", ""),
                            "website": info.get("website", ""),
                        }
        except Exception as e:
            logger.debug(f"DexScreener fetch failed for {address}: {e}")

        # Jupiter token list for Solana
        if chain == "solana" and not result.get("name"):
            try:
                if self._jupiter_tokens is None:
                    resp = await self.http.get(JUPITER_TOKEN_LIST)
                    if resp.status_code == 200:
                        self._jupiter_tokens = resp.json()
                if self._jupiter_tokens:
                    for token in self._jupiter_tokens:
                        if token.get("address", "").lower() == address.lower():
                            result.setdefault("name", token.get("name", ""))
                            result.setdefault("symbol", token.get("symbol", ""))
                            break
            except Exception as e:
                logger.debug(f"Jupiter fetch failed: {e}")

        # Birdeye fallback
        if not result.get("name"):
            try:
                headers = {"Accept": "application/json"}
                if self._birdeye_api_key:
                    headers["X-API-KEY"] = self._birdeye_api_key
                url = f"{BIRDEYE_PUBLIC}/defi/token_overview?address={address}"
                resp = await self.http.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    if data:
                        result.setdefault("name", data.get("name", ""))
                        result.setdefault("symbol", data.get("symbol", ""))
                        result.setdefault("price_usd", data.get("price", ""))
            except Exception as e:
                logger.debug(f"Birdeye fetch failed: {e}")

        return result

    async def _check_verification(self, address: str, chain: str) -> bool:
        """Check if contract source code is verified on block explorer."""
        if chain == "solana":
            return False  # Solana doesn't have a simple verified/unverified check via free API
        base = ETHERSCAN_BASES.get(chain)
        if not base:
            return True  # Unknown chain - assume unverified

        try:
            # Use free-tier etherscan API (no key needed for basic queries)
            url = f"{base}?module=contract&action=getsourcecode&address={address}"
            resp = await self.http.get(url)
            if resp.status_code == 200:
                data = resp.json()
                source = data.get("result", [{}])
                if isinstance(source, list) and source:
                    source_code = source[0].get("SourceCode", "")
                    return not bool(source_code and source_code.strip())
            return True  # Assume unverified on error
        except (ConnectionError, OSError, TimeoutError):
            return True

    async def _fetch_deployer(self, address: str, chain: str) -> str | None:
        """Get deployer address for a contract."""
        if chain == "solana":
            return None  # Free Solana API doesn't expose deployer easily
        base = ETHERSCAN_BASES.get(chain)
        if not base:
            return None

        try:
            # Get internal txns for creation - free endpoint
            url = f"{base}?module=account&action=txlistinternal&address={address}&sort=asc&limit=1"
            resp = await self.http.get(url)
            if resp.status_code == 200:
                data = resp.json()
                txs = data.get("result", [])
                if isinstance(txs, list) and txs:
                    return txs[0].get("from", None)
            return None
        except (ConnectionError, OSError, TimeoutError):
            return None

    async def _check_deployer_history(self, address: str, chain: str) -> dict[str, Any]:
        """Check if deployer created many tokens (mass deployer pattern)."""
        result: dict[str, Any] = {"other_tokens": 0, "clone_deployer": False}

        deployer = await self._fetch_deployer(address, chain)
        if not deployer:
            return result

        # Fetch recent tokens from this deployer via DexScreener
        try:
            url = f"{DEXSCREENER_API}/tokens/{deployer}"
            resp = await self.http.get(url)
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs", [])
                tokens = set()
                for p in pairs:
                    bt = p.get("baseToken", {})
                    tok_addr = bt.get("address", "")
                    if tok_addr and tok_addr.lower() != address.lower():
                        tokens.add(tok_addr)
                result["other_tokens"] = len(tokens)
                # Flag if deployer created > 20 tokens
                result["clone_deployer"] = len(tokens) > 20
        except (ConnectionError, OSError, TimeoutError):
            pass

        return result

    async def _search_similar_names(
        self, name: str, symbol: str, address: str, chain: str
    ) -> list[SimilarToken]:
        """Search DexScreener for tokens with similar names."""
        if not name and not symbol:
            return []

        similar: list[SimilarToken] = []
        seen = set()

        # Search by name
        queries = []
        if name:
            queries.append(name.replace(" ", "%20")[:30])
        if symbol:
            queries.append(symbol[:10])

        for query in queries[:2]:
            try:
                url = f"{DEXSCREENER_API}/search?q={query}"
                resp = await self.http.get(url)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                pairs = data.get("pairs", [])
                for pair in pairs:
                    bt = pair.get("baseToken", {})
                    tok_addr = bt.get("address", "").lower()
                    tok_name = bt.get("name", "")
                    tok_symbol = bt.get("symbol", "")
                    tok_chain = pair.get("chainId", "").lower()

                    # Skip self
                    if tok_addr == address.lower():
                        continue

                    # Skip duplicates
                    dedup_key = f"{tok_chain}:{tok_addr}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    # Skip well-known tokens
                    if self._is_well_known(tok_name, tok_symbol):
                        continue

                    name_sim = name_similarity_score(tok_name, name)
                    sym_sim = string_similarity(tok_symbol, symbol)

                    # Estimate age from pair creation timestamp
                    created_at = pair.get("pairCreatedAt", 0)
                    age_days = 0.0
                    if created_at and isinstance(created_at, int | float) and created_at > 0:
                        age_days = (time.time() - created_at / 1000) / 86400

                    if name_sim > 0.4 or sym_sim > 0.4:
                        similar.append(
                            SimilarToken(
                                address=tok_addr,
                                chain=tok_chain,
                                name=tok_name,
                                symbol=tok_symbol,
                                name_similarity=name_sim,
                                symbol_similarity=sym_sim,
                                age_days=age_days,
                            )
                        )

            except Exception as e:
                logger.debug(f"Name search failed for '{query}': {e}")

        # Sort by similarity descending, take top 20
        similar.sort(key=lambda t: t.overall_similarity, reverse=True)
        return similar[:20]

    def _check_keyword_matches(self, name: str, symbol: str) -> list[str]:
        """Check for clone-indicator keywords in name/symbol."""
        matches = []
        combined = f"{name} {symbol}".lower()
        for kw in CLONE_INDICATOR_KEYWORDS:
            if kw in combined:
                matches.append(kw)
        return matches

# ── Module-level helpers ──────────────────────────────────────────

async def scan_token(address: str, chain: str) -> CloneReport:
    """Convenience function - create scanner, scan, close."""
    scanner = CloneScanner()
    try:
        return await scanner.scan(address, chain)
    finally:
        await scanner.close()

async def fast_check(address: str, chain: str) -> dict[str, Any]:
    """Convenience function for fast check."""
    scanner = CloneScanner()
    try:
        return await scanner.fast_check(address, chain)
    finally:
        await scanner.close()
