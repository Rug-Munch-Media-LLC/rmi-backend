"""
Free Threat Intelligence Feeds - CryptoScamDB, GoPlus, Januus.
Paper ref: Article 3, Section 2 & Section 6 - Free Datasets and APIs.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


class ThreatIntelFeeds:
    """Aggregates free threat intelligence from multiple open sources."""

    def __init__(self) -> None:
        self._scamdb_cache: dict[str, dict[str, Any]] = {}
        self._goplus_cache: dict[str, dict[str, Any]] = {}

    # ── CryptoScamDB integration ──────────────────────────

    async def check_cryptoscamdb(self, address: str) -> dict[str, Any] | None:
        """Check if address is in CryptoScamDB (MIT-licensed, free)."""
        if address in self._scamdb_cache:
            return self._scamdb_cache[address]

        try:
            # CryptoScamDB API - free, no auth required
            url = f"https://api.cryptoscamdb.org/v1/check/{address}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    result = {
                        "address": address,
                        "is_scam": data.get("success", False),
                        "entries": data.get("result", {}).get("entries", [])[:5],
                        "source": "cryptoscamdb",
                    }
                    self._scamdb_cache[address] = result
                    return result
        except Exception as e:
            logger.debug(f"CryptoScamDB check failed: {e}")

        self._scamdb_cache[address] = None
        return None

    # ── GoPlus Security API ───────────────────────────────

    async def check_goplus(self, chain_id: str, address: str) -> dict[str, Any] | None:
        """GoPlus token security check (free, multi-chain)."""
        cache_key = f"{chain_id}:{address}"
        if cache_key in self._goplus_cache:
            return self._goplus_cache[cache_key]

        try:
            url = f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, params={"contract_addresses": address})
                if r.status_code == 200:
                    result = r.json().get("result", {}).get(address.lower(), {})
                    if result:
                        data = {
                            "address": address,
                            "chain": chain_id,
                            "is_honeypot": result.get("is_honeypot") == "1",
                            "can_take_back_ownership": result.get("can_take_back_ownership") == "1",
                            "is_open_source": result.get("is_open_source") != "0",
                            "is_blacklisted": result.get("is_blacklisted") == "1",
                            "buy_tax": float(result.get("buy_tax", "0")),
                            "sell_tax": float(result.get("sell_tax", "0")),
                            "is_mintable": result.get("is_mintable") == "1",
                            "is_proxy": result.get("is_proxy") == "1",
                            "source": "goplus",
                        }
                        # Calculate risk
                        score = 0
                        if data["is_honeypot"]:
                            score += 50
                        if data["can_take_back_ownership"]:
                            score += 20
                        if not data["is_open_source"]:
                            score += 15
                        if data["is_blacklisted"]:
                            score += 40
                        if data["buy_tax"] > 10:
                            score += 15
                        if data["sell_tax"] > 10:
                            score += 15
                        data["risk_score"] = min(100, score)
                        self._goplus_cache[cache_key] = data
                        return data
        except Exception as e:
            logger.debug(f"GoPlus check failed: {e}")

        self._goplus_cache[cache_key] = None
        return None

    # ── Januus Free Risk Scores ───────────────────────────

    async def check_januus(self, address: str) -> dict[str, Any] | None:
        """Januus free risk score (open-source, no license fee)."""
        try:
            url = f"https://api.januus.io/v1/risk/{address}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "address": address,
                        "risk_score": data.get("risk_score", 0),
                        "risk_level": data.get("risk_level", "unknown"),
                        "source": "januus",
                    }
        except Exception as e:
            logger.debug(f"Januus check failed: {e}")
        return None

    # ── Combined multi-source check ───────────────────────

    async def full_check(self, address: str, chain_id: str = "1") -> dict[str, Any]:
        """Run all free threat intel checks against an address."""
        results = {
            "address": address,
            "sources_checked": 0,
            "any_scam_flag": False,
            "findings": [],
        }

        # CryptoScamDB
        csdb = await self.check_cryptoscamdb(address)
        if csdb and csdb.get("is_scam"):
            results["any_scam_flag"] = True
            results["findings"].append(
                {
                    "source": "cryptoscamdb",
                    "severity": "high",
                    "detail": f"Found in CryptoScamDB with {len(csdb.get('entries', []))} entries",
                }
            )
        results["sources_checked"] += 1

        # GoPlus (for contract addresses)
        if len(address) > 30:  # Looks like a contract/token address
            goplus = await self.check_goplus(chain_id, address)
            if goplus:
                results["sources_checked"] += 1
                if goplus.get("is_honeypot"):
                    results["any_scam_flag"] = True
                    results["findings"].append(
                        {"source": "goplus", "severity": "critical", "detail": "Honeypot detected"}
                    )
                if goplus.get("risk_score", 0) > 50:
                    results["findings"].append(
                        {
                            "source": "goplus",
                            "severity": "high",
                            "detail": f"GoPlus risk score: {goplus['risk_score']}/100",
                        }
                    )

        # Januus
        januus = await self.check_januus(address)
        if januus and januus.get("risk_score", 0) > 50:
            results["sources_checked"] += 1
            results["any_scam_flag"] = True
            results["findings"].append(
                {
                    "source": "januus",
                    "severity": "medium",
                    "detail": f"Januus risk: {januus['risk_score']}/{januus.get('risk_level', '?')}",
                }
            )

        return results


# Singleton
_feeds: ThreatIntelFeeds | None = None


def get_threat_feeds() -> ThreatIntelFeeds:
    global _feeds
    if _feeds is None:
        _feeds = ThreatIntelFeeds()
    return _feeds
