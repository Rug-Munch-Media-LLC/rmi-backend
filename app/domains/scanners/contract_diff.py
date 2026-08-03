"""
SENTINEL Contract Diff - bytecode hash comparison against known rug patterns
==============================================================================
Compares a token's contract bytecode/ABI against a database of known rug contracts.
Detects clones, forks, and near-identical contracts by hashing function selectors,
bytecode sections, and metadata patterns.

Key features:
- EVM bytecode hashing (creation code + runtime code)
- Function selector fingerprinting (4byte directory style)
- Known-rug bytecode database via RAG
- Similarity scoring against known malicious contracts
- Contract clone detection (same creator, same code, different name)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.core.logging import get_logger

logger = get_logger("contract_diff")


@dataclass
class ContractDiffReport:
    """Result of contract diff analysis."""

    token_address: str
    chain: str
    risk_score: float = 0.0
    risk_level: str = "LOW"

    # Contract identification
    is_verified: bool = False
    contract_name: str | None = None
    bytecode_hash: str | None = None
    runtime_bytecode_hash: str | None = None

    # Function selectors
    function_selectors: list[str] = field(default_factory=list)
    selector_count: int = 0

    # Dangerous function signatures found
    dangerous_functions: list[str] = field(default_factory=list)

    # Clone detection
    is_clone: bool = False
    clone_of: str | None = None  # Address of matching contract
    clone_similarity: float = 0.0

    # Known rug matches
    known_rug_matches: list[dict[str, Any]] = field(default_factory=list)

    # RAG citations
    citations: list[dict[str, Any]] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)


# Dangerous function selectors (4-byte signatures of known rug functions)
DANGEROUS_SELECTORS = {
    "0x3ccfd60b": "withdraw()",
    "0x2e1a7d4d": "withdraw(uint256)",
    "0x51cff8d9": "withdrawAll()",
    "0x1e83409a": "emergencyWithdraw()",
    "0x715018a6": "renounceOwnership()",
    "0xf2fde38b": "transferOwnership(address)",
    "0x8da5cb5b": "owner()",
    "0x7f4e2f42": "setOwner(address)",
    "0x42966c68": "burn(uint256)",
    "0xa9059cbb": "transfer(address,uint256)",
    "0x095ea7b3": "approve(address,uint256)",
    "0x23b872dd": "transferFrom(address,address,uint256)",
    "0x40c10f19": "mint(address,uint256)",
    "0xa0712d68": "mint(uint256)",
    "0x6a627842": "mint(address)",
    "0x00fdd58e": "mint(address,uint256)",
    "0x4f1ef286": "upgradeToAndCall(address,bytes)",
    "0x3659cfe6": "upgradeTo(address)",
    "0x5c60da1b": "implementation()",
    "0x8f283970": "drain()",
    "0x3f4ba83a": "unpause()",
    "0x8456cb59": "pause()",
    "0xf48f7360": "setFee(uint256)",
    "0x092f3ee0": "setMaxTxAmount(uint256)",
    "0xc8b3bf68": "setWalletLimit(uint256)",
    "0x6080c494": "disableLimits()",
    "0xd8a9be58": "enableTrading()",
    "0x4a8b62d1": "setSwapBackSettings(uint256,bool)",
}

# Patterns that strongly indicate rug contracts
RUG_BYTECODE_PATTERNS = [
    # Hidden mint functions
    rb"hidden\s+mint",
    rb"airdropClaim",
    rb"airdrop_register",
    # Fee manipulation
    rb"setFeeRate",
    rb"setTaxFee",
    rb"setMaxFee",
    # Anti-selling mechanisms
    rb"isBlacklisted",
    rb"_blacklist",
    rb"blacklisted",
    rb"tradingOpen",
    rb"tradingEnabled",
    rb"launchedAt",
]


class ContractDiffAnalyzer:
    """Compare contract bytecode against known rug patterns and clone databases."""

    def __init__(self) -> None:
        self._initialized = False
        self._rug_hashes: dict[str, dict[str, Any]] = {}
        self._selector_map: dict[str, list[str]] = {}  # selector -> contract addresses

    async def _ensure_init(self) -> None:
        """Load known rug patterns from RAG on first use."""
        if self._initialized:
            return

        try:
            from app.domains.scanners.rag_citations import query_rag_citations

            # Load known rug contract hashes from RAG
            cits = await query_rag_citations(
                topic="known rug contract bytecode hash clone",
                chain="ethereum",
                scanner_type="contract_diff",
                max_citations=20,
                min_similarity=0.3,
            )
            for c in cits:
                meta = c.get("metadata", {})
                addr = meta.get("address", "")
                bhash = meta.get("bytecode_hash", "")
                if addr and bhash:
                    self._rug_hashes[bhash] = {
                        "address": addr,
                        "name": meta.get("name", "unknown"),
                        "flagged_as": meta.get("flagged_as", "rug"),
                        "similarity": c.get("similarity", 0),
                        "collection": c.get("collection", ""),
                        "reference": c.get("reference", ""),
                    }

            self._initialized = True
            if self._rug_hashes:
                logger.info(f"Loaded {len(self._rug_hashes)} known rug bytecode hashes")
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("_ensure_init_error", exc_info=True)
            logger.warning(f"Could not load rug hashes: {e}")
            self._initialized = True  # Don't retry on every call

    def _hash_bytecode(self, bytecode: str) -> str:
        """Hash bytecode for comparison. Strip metadata hash first."""
        # Remove CBOR metadata from end of bytecode (introduced in Solidity 0.6+)
        # The metadata hash is after the last STOP (0x00) opcode boundary
        clean = bytecode.lower().replace("0x", "")

        # Try to strip Solidity metadata (CBOR-encoded)
        # Look for known metadata markers
        for marker in ["a1", "a2", "65766d", "627a6574"]:  # CBOR + solc markers
            idx = clean.rfind(marker)
            if idx > len(clean) // 2:  # Only strip if near the end
                clean = clean[:idx]

        return hashlib.sha256(clean.encode()).hexdigest()

    def _extract_selectors(self, bytecode: str) -> list[str]:
        """Extract 4-byte function selectors from bytecode using PUSH4 pattern."""
        selectors = []
        clean = bytecode.lower().replace("0x", "")

        # PUSH4 = 0x63, followed by 4 bytes, then typically EQ (0x14) or other comparison
        pattern = re.compile(r"63([0-9a-f]{8})14")
        for m in pattern.finditer(clean):
            sel = "0x" + m.group(1)
            if sel not in selectors:
                selectors.append(sel)

        return selectors

    def _find_dangerous_functions(self, selectors: list[str]) -> list[str]:
        """Check selectors against known dangerous function signatures."""
        dangerous = []
        for sel in selectors:
            name = DANGEROUS_SELECTORS.get(sel)
            if name:
                dangerous.append(f"{sel} → {name}")
        return dangerous

    def _scan_rug_patterns(self, bytecode: str) -> list[str]:
        """Scan bytecode for known rug contract patterns."""
        found = []
        raw = bytecode.lower().encode() if not bytecode.startswith("0x") else bytecode[2:].lower().encode()

        for pattern in RUG_BYTECODE_PATTERNS:
            try:
                if pattern.lower() in raw:
                    found.append(pattern.decode(errors="replace"))
            except (ConnectionError, OSError, TimeoutError):
                pass
        return found

    def _compute_similarity(self, selectors_a: list[str], selectors_b: list[str]) -> float:
        """Compute Jaccard similarity between two selector sets."""
        if not selectors_a or not selectors_b:
            return 0.0
        set_a = set(selectors_a)
        set_b = set(selectors_b)
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union) if union else 0.0

    async def analyze(self, token_address: str, chain: str) -> ContractDiffReport:
        """Run contract diff analysis on a token."""
        await self._ensure_init()

        report = ContractDiffReport(token_address=token_address, chain=chain)

        try:
            bytecode = await self._fetch_bytecode(token_address, chain)
            if not bytecode:
                report.warnings.append("Could not fetch contract bytecode")
                return report

            # Hash the bytecode
            report.bytecode_hash = self._hash_bytecode(bytecode)
            report.runtime_bytecode_hash = hashlib.sha256(bytecode.lower().replace("0x", "").encode()).hexdigest()

            # Extract function selectors
            report.function_selectors = self._extract_selectors(bytecode)
            report.selector_count = len(report.function_selectors)

            # Check for dangerous functions
            report.dangerous_functions = self._find_dangerous_functions(report.function_selectors)

            # Check against known rug patterns
            rug_patterns = self._scan_rug_patterns(bytecode)

            # Check bytecode hash against known rug contracts
            if report.bytecode_hash in self._rug_hashes:
                match = self._rug_hashes[report.bytecode_hash]
                report.is_clone = True
                report.clone_of = match["address"]
                report.clone_similarity = 1.0
                report.known_rug_matches.append(
                    {
                        "type": "exact_hash_match",
                        "address": match["address"],
                        "name": match.get("name", "unknown"),
                        "flagged_as": match.get("flagged_as", "rug"),
                        "collection": match.get("collection", ""),
                    }
                )

            # Compute selector similarity against known rug contracts
            for bhash, info in self._rug_hashes.items():
                if bhash == report.bytecode_hash:
                    continue  # Already handled above
                other_selectors = info.get("selectors", [])
                if other_selectors:
                    sim = self._compute_similarity(report.function_selectors, other_selectors)
                    if sim > 0.7:  # High similarity threshold
                        report.known_rug_matches.append(
                            {
                                "type": "selector_similarity",
                                "address": info["address"],
                                "name": info.get("name", "unknown"),
                                "flagged_as": info.get("flagged_as", "rug"),
                                "similarity": round(sim, 3),
                                "collection": info.get("collection", ""),
                            }
                        )
                        if sim > report.clone_similarity:
                            report.clone_similarity = sim
                            report.clone_of = info["address"]

            # RAG citations
            try:
                from app.domains.scanners.rag_citations import query_rag_citations

                report.citations = await query_rag_citations(
                    topic=f"contract clone rug pattern {report.bytecode_hash[:8]}",
                    chain=chain,
                    scanner_type="contract_diff",
                    max_citations=3,
                    min_similarity=0.3,
                )
            except (httpx.HTTPError, KeyError, TypeError) as e:
                logger.debug("unknown_error", exc_info=True)
                logger.debug(f"Citation lookup failed: {e}")

            # Compute risk score
            risk_score = 0.0

            # Exact hash match = instant high risk
            if report.is_clone and any(m.get("type") == "exact_hash_match" for m in report.known_rug_matches):
                risk_score += 60.0

            # High selector similarity
            if report.clone_similarity > 0.8:
                risk_score += 30.0
            elif report.clone_similarity > 0.5:
                risk_score += 15.0

            # Dangerous functions
            danger_weight = {
                "withdraw": 10,
                "withdrawAll": 15,
                "drain": 20,
                "setOwner": 10,
                "mint": 8,
                "upgradeTo": 12,
            }
            for df in report.dangerous_functions:
                for key, weight in danger_weight.items():
                    if key.lower() in df.lower():
                        risk_score += weight
                        break

            # Rug bytecode patterns
            if rug_patterns:
                risk_score += min(len(rug_patterns) * 5, 25)

            # Multiple known rug matches
            if len(report.known_rug_matches) >= 3:
                risk_score += 15

            report.risk_score = min(risk_score, 100)

            if report.risk_score >= 70:
                report.risk_level = "CRITICAL"
            elif report.risk_score >= 40:
                report.risk_level = "HIGH"
            elif report.risk_score >= 20:
                report.risk_level = "MEDIUM"
            else:
                report.risk_level = "LOW"

            # Warnings
            if report.is_clone:
                report.warnings.append(
                    f"Contract bytecode matches known {report.known_rug_matches[0].get('flagged_as', 'rug')} "
                    f"contract at {report.clone_of}"
                )
            if report.clone_similarity > 0.7 and not report.is_clone:
                report.warnings.append(
                    f"Contract selectors {report.clone_similarity:.0%} similar to known rug at {report.clone_of}"
                )
            if report.dangerous_functions:
                report.warnings.append(f"Dangerous functions detected: {', '.join(report.dangerous_functions[:3])}")
            if rug_patterns:
                report.warnings.append(f"Rug-pattern bytecode markers found: {', '.join(rug_patterns[:3])}")

            # Enhance warnings with RAG citations
            try:
                from app.domains.scanners.rag_citations import build_citation_string

                if report.citations and report.warnings:
                    cit_str = build_citation_string(report.citations)
                    report.warnings[-1] += f" {cit_str}"
            except (AttributeError, ImportError):
                pass

        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Contract diff analysis failed: {e}")
            report.warnings.append(f"Analysis error: {str(e)[:100]}")

        return report

    # Chain name → chain ID mapping for EVM chains
    CHAIN_TO_ID = {
        "ethereum": 1,
        "eth": 1,
        "bsc": 56,
        "bnb": 56,
        "polygon": 137,
        "matic": 137,
        "base": 8453,
        "arbitrum": 42161,
        "arb": 42161,
        "optimism": 10,
        "op": 10,
        "avalanche": 43114,
        "avax": 43114,
        "fantom": 250,
        "ftm": 250,
    }

    async def _fetch_bytecode(self, address: str, chain: str) -> str | None:
        """Fetch contract bytecode from RPC using consensus client."""
        chain_lower = chain.lower()

        # Solana: program data via cached consensus RPC
        if chain_lower == "solana":
            try:
                from app.caching_shield.rpc_cache import get_rpc_cache

                cache = get_rpc_cache()
                result = await cache.get_account_info(address, chain="solana")
                if result and result.value:
                    # Solana account data is base64-encoded in value.data[0]
                    account_data = result.value
                    if isinstance(account_data, dict):
                        data_arr = account_data.get("data", [])
                        if isinstance(data_arr, list) and data_arr:
                            # Return the base64-encoded program data
                            return data_arr[0] if data_arr[1] == "base64" else str(data_arr[0])
                    return str(account_data)
            except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
                logger.debug("_fetch_bytecode_error", exc_info=True)
                logger.debug(f"Solana account info fetch failed: {e}")
            return None

        # EVM chains: eth_getCode via consensus RPC
        chain_id = self.CHAIN_TO_ID.get(chain_lower)
        if not chain_id:
            logger.warning(f"Unknown EVM chain for bytecode fetch: {chain}")
            return None

        try:
            from app.caching_shield.rpc_cache import get_rpc_cache

            cache = get_rpc_cache()
            result = await cache.query_with_cache(
                method="eth_getCode",
                params=[address, "latest"],
                chain=str(chain_id),
            )
            code_hex = result.value if result else None
            if code_hex and code_hex != "0x" and len(str(code_hex)) > 10:
                logger.info(
                    f"Got bytecode for {address} on {chain} (confidence={result.confidence}%, sources={result.response_count})"
                )
                return code_hex
            logger.debug(f"eth_getCode returned empty for {address} on {chain}")
        except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.debug(f"Consensus RPC bytecode fetch failed: {e}")

        # Fallback: direct public RPC call
        try:
            rpc_urls = {
                1: "https://ethereum-rpc.publicnode.com",
                56: "https://bsc-rpc.publicnode.com",
                137: "https://polygon-rpc.publicnode.com",
                8453: "https://base-rpc.publicnode.com",
                42161: "https://arbitrum-rpc.publicnode.com",
                10: "https://optimism-rpc.publicnode.com",
            }
            url = rpc_urls.get(chain_id)
            if url:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "eth_getCode",
                            "params": [address, "latest"],
                            "id": 1,
                        },
                    )
                    data = resp.json()
                    code = data.get("result", "0x")
                    if code and code != "0x" and len(code) > 10:
                        logger.info(f"Got bytecode via fallback RPC for {address}")
                        return code
        except (
            httpx.HTTPError,
            requests.RequestException,
            ConnectionError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            OSError,
        ) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.debug(f"Fallback RPC failed: {e}")

        return None
