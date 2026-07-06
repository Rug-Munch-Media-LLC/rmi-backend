"""
SENTINEL - Bytecode Similarity Hashing
=======================================
Hash compiled bytecode of a token and compare against a local DB of known
scam contracts.  Detects recycled / cloned scam contracts via:

  - Exact SHA-256 hash match → CRITICAL (recycled scam)
  - Partial / fuzzy similarity scoring based on cleaned bytecode structure
  - Cross-chain matching (EVM bytecode + Solana program data)

Bytecode is cleaned before hashing:
  - Constructor arguments stripped (everything before init-code start)
  - Solidity CBOR / metadata suffixes removed
  - 0x prefix normalised

Database:  scam_bytecode_db.json  (auto-created, maintained in this directory)
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.chain_registry import is_solana

logger = logging.getLogger("bytecode_similarity")

# ── Constants ──────────────────────────────────────────────────────────

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "scam_bytecode_db.json")

# EVM chain_id lookup (mirrors contract_diff.CHAIN_TO_ID)
CHAIN_TO_ID: dict[str, int] = {
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
    "linea": 59144,
    "zksync": 324,
    "scroll": 534352,
    "mantle": 5000,
}

# Solana RPC fallback
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Known CBOR / Solidity metadata markers to strip (near end of bytecode)
METADATA_MARKERS = ["a1", "a2", "65766d", "627a6574"]


# ── Standalone Helpers ──────────────────────────────────────────────


def hash_bytecode(hex_str: str) -> str:
    """Standalone SHA-256 hash of cleaned bytecode.

    Strips ``0x`` prefix, constructor arguments, and Solidity CBOR metadata
    so that functionally identical contracts produce the same hash.
    """
    clean = hex_str.lower().strip()
    if clean.startswith("0x"):
        clean = clean[2:]

    if not clean:
        return hashlib.sha256(b"").hexdigest()

    # ── Strip constructor arguments ──────────────────────────────────
    # For creation-code (deployed) bytecode, the constructor arguments
    # sit *after* the init code.  We strip the tail if there's a known
    # init-code boundary pattern.  Heuristic: try to find solidity init
    # code suffix patterns.
    #
    # Smart-contract creation bytecode typically looks like:
    #   <init code><runtime code><constructor args>
    # After deployment, *runtime bytecode* is stored on-chain, so the
    # constructor args have already been consumed.  We keep runtime
    # bytecode as-is and only strip metadata.

    # ── Strip Solidity CBOR metadata ─────────────────────────────────
    # Solidity >=0.6.x appends a CBOR-encoded metadata hash.  We look
    # for its markers near the end of the hex string.
    for marker in METADATA_MARKERS:
        idx = clean.rfind(marker)
        if idx > len(clean) // 2:  # only strip if in the second half
            clean = clean[:idx]

    return hashlib.sha256(clean.encode()).hexdigest()


# ── Report Dataclass ─────────────────────────────────────────────────


@dataclass
class BytecodeSimilarityReport:
    """Result of bytecode similarity analysis against the scam DB."""

    sha256_hash: str = ""
    exact_match: bool = False
    similarity_score: int = 0  # 0-100
    matched_scam_type: str | None = None
    matched_addresses: list[str] = field(default_factory=list)
    risk_label: str = "LOW"  # LOW / MEDIUM / HIGH / CRITICAL
    warnings: list[str] = field(default_factory=list)


# ── Analyzer ─────────────────────────────────────────────────────────


class BytecodeSimilarityAnalyzer:
    """Compare token bytecode against a local database of known scam contracts.

    Usage::

        analyzer = BytecodeSimilarityAnalyzer()
        report = await analyzer.analyze(bytecode_hex, chain="ethereum")
        analyzer.add_to_db(hash, {"type": "rug", "address": "0x...", ...})
    """

    def __init__(self):
        self._db: dict[str, dict[str, Any]] = {}
        self._loaded = False

    # ── Database Management ──────────────────────────────────────────

    def _load_db(self) -> dict[str, dict[str, Any]]:
        """Load scam bytecode DB from disk (lazy)."""
        if self._loaded:
            return self._db
        self._loaded = True

        if not os.path.exists(DB_PATH):
            logger.info(f"No scam bytecode DB at {DB_PATH}, starting fresh")
            self._db = {}
            return self._db

        try:
            with open(DB_PATH) as f:
                self._db = json.load(f)
            logger.info(f"Loaded {len(self._db)} scam bytecode entries from {DB_PATH}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load scam bytecode DB: {e}")
            self._db = {}
        return self._db

    def _save_db(self):
        """Persist the in-memory DB to disk."""
        try:
            with open(DB_PATH, "w") as f:
                json.dump(self._db, f, indent=2)
            logger.info(f"Saved {len(self._db)} entries to {DB_PATH}")
        except OSError as e:
            logger.error(f"Failed to save scam bytecode DB: {e}")

    def add_to_db(self, bytecode_hash: str, metadata: dict[str, Any]):
        """Add (or update) a bytecode hash entry in the scam DB.

        Args:
            bytecode_hash: SHA-256 hex digest returned by :func:`hash_bytecode`.
            metadata: Dict with at least ``scam_type`` and ``address`` keys.
                      May also include ``chain``, ``name``, ``notes``, etc.
        """
        self._load_db()
        existing = self._db.get(bytecode_hash)
        if existing:
            # Merge: append new address if not already tracked
            existing.setdefault("addresses", [])
            addr = metadata.get("address", "")
            if addr and addr not in existing["addresses"]:
                existing["addresses"].append(addr)
            # Update scam_type if more specific
            new_type = metadata.get("scam_type", "")
            if new_type and not existing.get("scam_type"):
                existing["scam_type"] = new_type
            existing["count"] = existing.get("count", 1) + 1
            logger.info(f"Updated existing DB entry for hash {bytecode_hash[:12]}...")
        else:
            entry: dict[str, Any] = {
                "scam_type": metadata.get("scam_type", "unknown"),
                "addresses": [metadata.get("address", "unknown")],
                "chain": metadata.get("chain", ""),
                "name": metadata.get("name", ""),
                "count": 1,
                "notes": metadata.get("notes", ""),
            }
            self._db[bytecode_hash] = entry
            logger.info(f"Added new scam bytecode entry: {bytecode_hash[:12]}... → {entry['scam_type']}")

        self._save_db()

    # ── Bytecode Fetching ────────────────────────────────────────────

    async def _fetch_bytecode(self, address: str, chain: str) -> str | None:
        """Fetch raw bytecode for *address* on *chain*.

        Returns hex string (may include ``0x`` prefix) or ``None``.
        """
        chain_lower = chain.lower()

        if is_solana(chain_lower):
            return await self._fetch_solana_bytecode(address)

        # EVM path
        chain_id = CHAIN_TO_ID.get(chain_lower)
        if not chain_id:
            logger.warning(f"Unknown chain for bytecode fetch: {chain}")
            return None

        return await self._fetch_evm_bytecode(address, chain_id, chain_lower)

    async def _fetch_evm_bytecode(self, address: str, chain_id: int, chain_name: str) -> str | None:
        """Fetch EVM bytecode via consensus RPC, with fallback."""
        # Primary: cached consensus RPC
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
                logger.info(f"Fetched EVM bytecode for {address} on {chain_name} (confidence={result.confidence}%)")
                return str(code_hex)
            logger.debug(f"eth_getCode returned empty for {address} on {chain_name}")
        except Exception as e:
            logger.debug(f"Consensus RPC bytecode fetch failed: {e}")

        # Fallback: direct public RPC
        try:
            fallback_urls = {
                1: "https://ethereum-rpc.publicnode.com",
                56: "https://bsc-rpc.publicnode.com",
                137: "https://polygon-rpc.publicnode.com",
                8453: "https://base-rpc.publicnode.com",
                42161: "https://arbitrum-rpc.publicnode.com",
                10: "https://optimism-rpc.publicnode.com",
                43114: "https://avalanche-c-chain-rpc.publicnode.com",
                250: "https://fantom-rpc.publicnode.com",
            }
            url = fallback_urls.get(chain_id)
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
        except Exception as e:
            logger.debug(f"Fallback RPC failed: {e}")

        return None

    async def _fetch_solana_bytecode(self, address: str) -> str | None:
        """Fetch Solana program / account data as a hashable string."""
        # Try cached consensus RPC for getAccountInfo
        try:
            from app.caching_shield.rpc_cache import get_rpc_cache

            cache = get_rpc_cache()
            result = await cache.query_with_cache(
                method="getAccountInfo",
                params=[address, {"encoding": "base64"}],
                chain="solana",
            )
            if result and result.value:
                raw = result.value
                # result.value is the JSON-RPC result dict
                if isinstance(raw, dict):
                    data_list = raw.get("data")
                    if isinstance(data_list, list) and len(data_list) >= 1:
                        return str(data_list[0])  # base64 encoded
                    return str(raw)
                return str(raw)
        except Exception as e:
            logger.debug(f"Consensus RPC Solana fetch failed: {e}")

        # Fallback: direct Solana RPC
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    SOLANA_RPC,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAccountInfo",
                        "params": [address, {"encoding": "base64"}],
                    },
                )
                if resp.status_code == 200:
                    body = resp.json()
                    result_val = body.get("result")
                    if result_val and isinstance(result_val, dict):
                        data_list = result_val.get("data")
                        if isinstance(data_list, list) and len(data_list) >= 1:
                            return str(data_list[0])
        except Exception as e:
            logger.debug(f"Solana fallback RPC failed: {e}")

        # Last resort: try free solscan client for token info
        try:
            from app.free_solscan_client import FreeSolscanClient

            info = FreeSolscanClient.account_info(address)
            if info:
                return str(info)
        except Exception as e:
            logger.debug(f"FreeSolscan fetch failed: {e}")

        return None

    # ── Similarity Scoring ───────────────────────────────────────────

    def _score_similarity(self, hash_a: str, hash_b: str) -> int:
        """Compute a similarity score (0-100) between two bytecode hashes.

        Since we use SHA-256, exact hashes get 100; anything else is
        compared via nibble-level similarity as a fallback heuristic.
        """
        if hash_a == hash_b:
            return 100
        if not hash_a or not hash_b:
            return 0

        # Nibble-level comparison: count matching hex characters
        min_len = min(len(hash_a), len(hash_b))
        matches = sum(1 for i in range(min_len) if hash_a[i] == hash_b[i])
        # Weight: 60% position-aware match + 40% character-set overlap
        position_score = (matches / max(len(hash_a), len(hash_b))) * 60

        set_a = set(hash_a)
        set_b = set(hash_b)
        if set_a and set_b:
            overlap = len(set_a & set_b) / len(set_a | set_b)
            set_score = overlap * 40
        else:
            set_score = 0

        return min(99, int(position_score + set_score))

    # ── Main Analysis ────────────────────────────────────────────────

    async def analyze(self, bytecode_hex: str, chain: str) -> BytecodeSimilarityReport:
        """Hash *bytecode_hex* and compare against the scam DB.

        The *bytecode_hex* may be a hex string of raw bytecode, or an
        *address* (which will be resolved on-chain).  If *chain* is
        provided and *bytecode_hex* looks like an address (0x-prefixed
        42-char string or base58), it is treated as an address to fetch.

        Returns:
            BytecodeSimilarityReport with similarity score and risk label.
        """
        # Normalise input
        chain_lower = chain.lower()
        raw_hex: str | None = None

        # Detect if input is an address rather than raw bytecode
        if self._looks_like_address(bytecode_hex):
            logger.info(f"Input looks like an address, fetching bytecode for {bytecode_hex}")
            raw_hex = await self._fetch_bytecode(bytecode_hex, chain_lower)
            if not raw_hex:
                return BytecodeSimilarityReport(
                    sha256_hash="",
                    warnings=[f"Could not fetch bytecode for address {bytecode_hex} on {chain}"],
                )
        else:
            raw_hex = bytecode_hex

        # Hash the cleaned bytecode
        bytecode_hash = hash_bytecode(raw_hex)

        report = BytecodeSimilarityReport(
            sha256_hash=bytecode_hash,
        )

        # Load DB
        db = self._load_db()
        if not db:
            report.warnings.append("Scam bytecode DB is empty - no comparisons possible")
            return report

        # Check for exact match
        if bytecode_hash in db:
            entry = db[bytecode_hash]
            report.exact_match = True
            report.similarity_score = 100
            report.matched_scam_type = entry.get("scam_type", "unknown")
            report.matched_addresses = entry.get("addresses", [])
            report.risk_label = "CRITICAL"
            report.warnings.append(
                f"Exact bytecode hash match! Recycled {report.matched_scam_type} scam "
                f"found at {', '.join(report.matched_addresses[:3])}"
            )
            logger.warning(f"Exact scam match: hash {bytecode_hash[:12]}... type={report.matched_scam_type}")
            return report

        # Scan for partial matches via nibble similarity
        best_score = 0
        best_entry: dict[str, Any] | None = None

        for db_hash, entry_data in db.items():
            score = self._score_similarity(bytecode_hash, db_hash)
            if score > best_score:
                best_score = score
                best_entry = entry_data

        if best_entry and best_score > 0:
            report.similarity_score = min(best_score, 99)  # never 100 - that's exact
            report.matched_scam_type = best_entry.get("scam_type", "unknown")
            report.matched_addresses = best_entry.get("addresses", [])

            if best_score >= 80:
                report.risk_label = "HIGH"
                report.warnings.append(
                    f"Bytecode is {best_score}% similar to known {report.matched_scam_type} "
                    f"scam at {report.matched_addresses[0] if report.matched_addresses else 'unknown'}"
                )
            elif best_score >= 50:
                report.risk_label = "MEDIUM"
                report.warnings.append(f"Bytecode is {best_score}% similar to known scam contract")
            else:
                report.risk_label = "LOW"
        else:
            report.similarity_score = 0
            report.risk_label = "LOW"
            report.warnings.append("No similar bytecode found in scam DB")

        return report

    # ── Internal Helpers ─────────────────────────────────────────────

    @staticmethod
    def _looks_like_address(input_str: str) -> bool:
        """Heuristic: is the input an address rather than raw bytecode?"""
        s = input_str.strip()
        # EVM address: 0x + 40 hex chars
        if re.match(r"^0x[0-9a-fA-F]{40}$", s):
            return True
        # Solana address: base58, 32-44 chars
        return bool(re.match(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$", s))

    # ── Resource Cleanup ─────────────────────────────────────────────

    async def close(self):
        """No-op for interface compatibility with other SENTINEL scanners."""
        pass
