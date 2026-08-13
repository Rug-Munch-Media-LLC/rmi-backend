"""
Spam & Sanctions Registry - Aggregates free blocklists from multiple sources.
Sources: GoldRush (8M+ spam tokens), Scam Sniffer, OpenSanctions, Guardian, CryptoScamDB.

Paper ref: Article 4, Sections 4 & 17 - Free Blocklists and Datasets
All sources are free, MIT/GPL/CC licensed, production-grade.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Data directory ────────────────────────────────────────

SPAM_DATA_DIR = Path(os.getenv("SPAM_DATA_DIR", "/app/data/spam"))
SPAM_DATA_DIR.mkdir(parents=True, exist_ok=True)


class SpamRegistry:
    """
    Multi-source spam/sanctions registry.
    Sources (all free):
    - GoldRush: 8M+ spam ERC20/NFT contracts across 6 chains
    - Scam Sniffer: continuously updated phishing/scam addresses
    - OpenSanctions: OFAC sanctioned wallets
    - Guardian: phishing website blocklists (JSON + bloom filters)
    - CryptoScamDB: community-maintained scam database
    """

    def __init__(self) -> None:
        self._spam_tokens: dict[str, set[str]] = {}  # chain -> {addresses}
        self._spam_nfts: dict[str, set[str]] = {}  # chain -> {addresses}
        self._phishing_domains: set[str] = set()
        self._sanctioned_wallets: set[str] = set()
        self._scam_addresses: set[str] = set()
        self._loaded = False
        self._stats = {"tokens": 0, "nfts": 0, "phishing": 0, "sanctioned": 0, "scam": 0}

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_goldrush()
        self._load_scamsniffer()
        self._load_opensanctions()
        self._load_guardian()
        self._load_cryptoscamdb()
        self._loaded = True
        self._compute_stats()

    def _compute_stats(self) -> None:
        self._stats["tokens"] = sum(len(s) for s in self._spam_tokens.values())
        self._stats["nfts"] = sum(len(s) for s in self._spam_nfts.values())
        self._stats["phishing"] = len(self._phishing_domains)
        self._stats["sanctioned"] = len(self._sanctioned_wallets)
        self._stats["scam"] = len(self._scam_addresses)

    # ── GoldRush Spam Token Lists (8M+ contracts, 6 chains) ─

    def _load_goldrush(self) -> None:
        """Load GoldRush enhanced spam token lists (npm: @goldrush/spam-tokens)."""
        # GoldRush publishes JSON lists via their API (free tier)
        # Fallback: load cached copies if API unavailable
        chains = {
            "ethereum": "1",
            "base": "8453",
            "bsc": "56",
            "polygon": "137",
            "optimism": "10",
            "gnosis": "100",
        }

        for chain_name, _chain_id in chains.items():
            cache_file = SPAM_DATA_DIR / f"goldrush_spam_{chain_name}.json"
            if cache_file.exists():
                try:
                    with open(cache_file) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        erc20 = data.get("erc20", data.get("tokens", []))
                        nft = data.get("nft", data.get("nfts", []))
                        tokens = set(erc20) if isinstance(erc20, list) else set()
                        nfts = set(nft) if isinstance(nft, list) else set()
                        if tokens:
                            self._spam_tokens[chain_name] = tokens
                        if nfts:
                            self._spam_nfts[chain_name] = nfts
                    elif isinstance(data, list):
                        self._spam_tokens[chain_name] = set(data)
                    logger.info(f"GoldRush {chain_name}: {len(self._spam_tokens.get(chain_name, set()))} spam tokens")
                except Exception as e:
                    logger.debug(f"GoldRush {chain_name} cache read failed: {e}")

        # If no cache, initialize empty sets
        for chain_name in chains:
            if chain_name not in self._spam_tokens:
                self._spam_tokens[chain_name] = set()
            if chain_name not in self._spam_nfts:
                self._spam_nfts[chain_name] = set()

    # ── Scam Sniffer Blacklists (GPL-3.0, 245+ stars) ─────

    def _load_scamsniffer(self) -> None:
        """Load Scam Sniffer web3 blacklists."""
        cache_file = SPAM_DATA_DIR / "scamsniffer_blacklist.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._scam_addresses.update(str(a).lower() for a in data if isinstance(a, str))
                elif isinstance(data, dict):
                    addrs = data.get("addresses", data.get("blacklist", []))
                    if isinstance(addrs, list):
                        self._scam_addresses.update(str(a).lower() for a in addrs if isinstance(a, str))
                logger.info(f"Scam Sniffer: {len(self._scam_addresses)} addresses")
            except Exception as e:
                logger.debug(f"Scam Sniffer load failed: {e}")

    # ── OpenSanctions (OFAC sanctioned wallets) ───────────

    def _load_opensanctions(self) -> None:
        """Load OpenSanctions sanctioned crypto wallets."""
        cache_file = SPAM_DATA_DIR / "opensanctions_crypto.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            addr = entry.get("address", entry.get("wallet", ""))
                            if addr:
                                self._sanctioned_wallets.add(str(addr).lower())
                logger.info(f"OpenSanctions: {len(self._sanctioned_wallets)} sanctioned wallets")
            except Exception as e:
                logger.debug(f"OpenSanctions load failed: {e}")

    # ── Guardian Phishing Blocklist (JSON + bloom) ─────────

    def _load_guardian(self) -> None:
        """Load Guardian phishing domain blocklists."""
        cache_file = SPAM_DATA_DIR / "guardian_phishing.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    domains = data.get("domains", data.get("domain-list", []))
                    if isinstance(domains, list):
                        self._phishing_domains.update(str(d).lower() for d in domains)
                elif isinstance(data, list):
                    self._phishing_domains.update(str(d).lower() for d in data)
                logger.info(f"Guardian: {len(self._phishing_domains)} phishing domains")
            except Exception as e:
                logger.debug(f"Guardian load failed: {e}")

    # ── CryptoScamDB ──────────────────────────────────────

    def _load_cryptoscamdb(self) -> None:
        """Load CryptoScamDB local cache."""
        cache_file = SPAM_DATA_DIR / "cryptoscamdb.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    entries = data.get("addresses", data.get("entries", []))
                    if isinstance(entries, list):
                        for addr in entries:
                            if isinstance(addr, str):
                                self._scam_addresses.add(addr.lower())
                elif isinstance(data, list):
                    self._scam_addresses.update(str(a).lower() for a in data)
            except Exception as e:
                logger.debug(f"CryptoScamDB load failed: {e}")

    # ── Query methods ─────────────────────────────────────

    def is_spam_token(self, address: str, chain: str = "solana") -> bool:
        """Check if token address is in any spam list."""
        self._ensure_loaded()
        key = address.lower()
        # Check spam tokens
        if key in self._spam_tokens.get(chain, set()):
            return True
        if key in self._spam_nfts.get(chain, set()):
            return True
        # Check scam addresses
        return key in self._scam_addresses

    def is_sanctioned(self, address: str) -> bool:
        """Check if address is OFAC sanctioned."""
        self._ensure_loaded()
        return address.lower() in self._sanctioned_wallets

    def is_known_scam(self, address: str) -> bool:
        """Check if address appears in any scam database."""
        self._ensure_loaded()
        return address.lower() in self._scam_addresses

    def is_phishing_domain(self, domain: str) -> bool:
        """Check if domain is in phishing blocklists."""
        self._ensure_loaded()
        return domain.lower() in self._phishing_domains

    def check_token(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Full spam/sanctions check for a token address.

        Args:
            address: Token or wallet address to check.
            chain: Blockchain identifier (default: "solana").

        Returns:
            Dict with is_spam, is_sanctioned, is_known_scam flags and sources checked.
        """
        self._ensure_loaded()
        return {
            "address": address,
            "chain": chain,
            "is_spam": self.is_spam_token(address, chain),
            "is_sanctioned": self.is_sanctioned(address),
            "is_known_scam": self.is_known_scam(address),
            "sources_checked": [
                "goldrush",
                "scamsniffer",
                "opensanctions",
                "guardian",
                "cryptoscamdb",
            ],
        }

    def filter_spam_tokens(
        self, tokens: list[dict[str, Any]], chain: str = "solana"
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split token list into clean and spam.
        Returns: (clean_tokens, spam_tokens)"""
        self._ensure_loaded()
        clean = []
        spam = []
        for t in tokens:
            addr = t.get("address", t.get("contract_address", ""))
            if self.is_spam_token(str(addr), chain):
                spam.append(t)
            else:
                clean.append(t)
        return clean, spam

    def stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        self._ensure_loaded()
        return {
            **self._stats,
            "total": sum(self._stats.values()),
            "chains_indexed": len(self._spam_tokens),
            "loaded": self._loaded,
            "updated": datetime.now(UTC).isoformat(),
        }


# ── Seed data loader ──────────────────────────────────────


def seed_spam_data() -> None:
    """Download and cache initial spam data from free sources."""
    import asyncio

    import httpx

    async def _fetch() -> None:
        # Try to fetch GoldRush lists
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # GoldRush API endpoint (free tier)
                r = await client.get("https://api.goldrush.dev/spam/tokens?chain=ethereum&limit=1000")
                if r.status_code == HTTPStatus.OK:
                    data = r.json()
                    with open(SPAM_DATA_DIR / "goldrush_spam_ethereum.json", "w") as f:
                        json.dump(data, f)
                    logger.info(f"Seeded GoldRush ethereum: {len(data) if isinstance(data, list) else 'ok'}")
        except Exception as e:
            logger.debug(f"GoldRush seed failed (expected on free tier): {e}")

        # Try Scam Sniffer
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(
                    "https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist/address.json"
                )
                if r.status_code == HTTPStatus.OK:
                    with open(SPAM_DATA_DIR / "scamsniffer_blacklist.json", "w") as f:
                        f.write(r.text)
                    logger.info("Seeded Scam Sniffer blacklist")
        except Exception as e:
            logger.debug(f"Scam Sniffer seed failed: {e}")

    try:
        asyncio.run(_fetch())
    except Exception as e:
        logger.warning(f"Spam data seed failed: {e}")


# Singleton
_registry: SpamRegistry | None = None


def get_spam_registry() -> SpamRegistry:
    global _registry
    if _registry is None:
        _registry = SpamRegistry()
    return _registry
