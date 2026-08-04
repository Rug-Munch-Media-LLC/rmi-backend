"""
SENTINEL - Address Labeler
==========================
Resolves wallet/contract addresses to known labels from multiple sources:
  1. Etherscan API - public address labels
  2. RolodETH - public GitHub label database
  3. walletLabels.xyz - public address label API
  4. bluepages.fyi - public address label API
  5. Internal RAG - known_scams and wallet_profiles databases

Combines all label sources into a unified response with risk assessment.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_registry import CHAINS, is_evm
from app.core.logging import get_logger
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("address_labeler")

# ── RolodETH cache (avoid re-fetching on every call) ──────────────────

_ROLODETH_CACHE: dict[str, list[dict]] | None = None
_ROLODETH_LAST_FETCH: float = 0.0
_ROLODETH_TTL = 3600  # 1 hour cache

# ── Known scam patterns / addresses (built-in database) ─────────────

BUILTIN_KNOWN_SCAMS: dict[str, dict] = {
    # Notorious rug-pull deployers and scam addresses
    "0x0000000000000000000000000000000000000000": {"label": "Null address", "category": "burn"},
    "0xdead000000000000000000000000000000000000": {"label": "Dead address", "category": "burn"},
}

# Known exchange deposit addresses (used by is_exchange check)
# We already have cex_hot_wallets in chain_registry, but this provides
# additional context for label matching
BUILTIN_EXCHANGE_LABELS: dict[str, str] = {
    # Common EOA labels
}

# ── Report dataclass ─────────────────────────────────────────────────


@dataclass
class LabelEntry:
    """A single label from a specific source."""

    source: str
    label: str
    category: str = ""  # exchange, mev, scam, defi, wallet, contract, bridge, etc.
    confidence: float = 1.0


@dataclass
class AddressLabelReport:
    """Aggregated address label report."""

    address: str
    chain: str

    labels: list[dict[str, Any]] = field(default_factory=list)
    primary_label: str = ""
    primary_category: str = ""

    # Flags
    is_exchange: bool = False
    is_mev_bot: bool = False
    is_known_scam: bool = False
    is_defi_protocol: bool = False
    is_bridge: bool = False
    isDeployer: bool = False  # noqa: N815 — legacy report field name (public API)

    # Risk
    risk_score: float = 0.0
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Labeler class ────────────────────────────────────────────────────


@sentinel_module(key="address_labels", tier="premium", description="Known-good / known-bad address labeling of holders")
class AddressLabeler:
    """Resolve addresses to known labels from multiple sources in parallel."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")

    # ── Source 1: Etherscan ─────────────────────────────────────────

    async def _query_etherscan(self, address: str, chain: str) -> list[LabelEntry]:
        """Fetch address label from Etherscan account API."""
        entries = []
        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return entries
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return entries

        try:
            # Etherscan account API returns public name/tag for labeled addresses
            url = f"{chain_cfg.explorer_api_url}?module=account&action=addressinfo&address={address}&apikey={key}"
            resp = await self._http.get(url)
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("result", {})
                if isinstance(result, dict):
                    name = result.get("name", "")
                    tag = result.get("tag", "")
                    if name:
                        entries.append(
                            LabelEntry(
                                source="etherscan",
                                label=name,
                                category=self._infer_category(name + " " + tag),
                            )
                        )
                    if tag and tag != name:
                        entries.append(
                            LabelEntry(
                                source="etherscan_tag",
                                label=tag,
                                category=self._infer_category(tag),
                            )
                        )
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_query_etherscan_error", exc_info=True)
            logger.debug(f"Etherscan label query failed for {address}: {e}")
        return entries

    # ── Source 2: RolodETH ──────────────────────────────────────────

    async def _fetch_rolodeth(self) -> dict[str, list[dict]]:
        """Fetch and cache RolodETH labels from GitHub."""
        global _ROLODETH_CACHE, _ROLODETH_LAST_FETCH
        import time

        now = time.monotonic()
        if _ROLODETH_CACHE and (now - _ROLODETH_LAST_FETCH) < _ROLODETH_TTL:
            return _ROLODETH_CACHE

        try:
            url = "https://raw.githubusercontent.com/exdx/rolodETH/main/data/labels.json"
            resp = await self._http.get(url, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                # Build address-indexed dict: { "0x...": [{label, category, ...}] }
                index: dict[str, list[dict]] = {}
                if isinstance(data, list):
                    for entry in data:
                        addr = entry.get("address", "").lower()
                        if addr:
                            index.setdefault(addr, []).append(
                                {
                                    "label": entry.get("label", ""),
                                    "category": entry.get("category", entry.get("type", "")),
                                }
                            )
                elif isinstance(data, dict):
                    # Could be address → label mapping
                    for addr, info in data.items():
                        addr_norm = addr.lower()
                        if isinstance(info, str):
                            index.setdefault(addr_norm, []).append(
                                {
                                    "label": info,
                                    "category": "",
                                }
                            )
                        elif isinstance(info, dict):
                            index.setdefault(addr_norm, []).append(
                                {
                                    "label": info.get("label", info.get("name", "")),
                                    "category": info.get("category", info.get("type", "")),
                                }
                            )
                        elif isinstance(info, list):
                            for sub in info:
                                if isinstance(sub, dict):
                                    index.setdefault(addr_norm, []).append(
                                        {
                                            "label": sub.get("label", sub.get("name", "")),
                                            "category": sub.get("category", sub.get("type", "")),
                                        }
                                    )

                _ROLODETH_CACHE = index
                _ROLODETH_LAST_FETCH = now
                return index
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.debug(f"RolodETH fetch failed: {e}")

        return _ROLODETH_CACHE or {}

    async def _query_rolodeth(self, address: str) -> list[LabelEntry]:
        """Query RolodETH for address labels."""
        entries = []
        index = await self._fetch_rolodeth()
        addr_norm = address.lower()
        labels = index.get(addr_norm, [])
        for lbl in labels:
            entries.append(
                LabelEntry(
                    source="rolodeth",
                    label=lbl.get("label", ""),
                    category=lbl.get("category", ""),
                )
            )
        return entries

    # ── Source 3: walletLabels.xyz ─────────────────────────────────

    async def _query_walletlabels(self, address: str) -> list[LabelEntry]:
        """Query walletLabels.xyz API for address labels."""
        entries = []
        try:
            url = f"https://api.walletlabels.xyz/v1/address/{address}"
            resp = await self._http.get(url, timeout=10.0)
            if resp.status_code == 200:
                body = resp.json()
                # Parse response - format may vary
                labels = body.get("labels", body.get("data", []))
                if isinstance(labels, list):
                    for lbl in labels:
                        if isinstance(lbl, dict):
                            entries.append(
                                LabelEntry(
                                    source="walletlabels",
                                    label=lbl.get("label", lbl.get("name", "")),
                                    category=lbl.get("category", lbl.get("type", "")),
                                    confidence=lbl.get("confidence", 1.0),
                                )
                            )
                        elif isinstance(lbl, str):
                            entries.append(
                                LabelEntry(
                                    source="walletlabels",
                                    label=lbl,
                                )
                            )
                elif isinstance(labels, dict):
                    label = labels.get("label", labels.get("name", ""))
                    if label:
                        entries.append(
                            LabelEntry(
                                source="walletlabels",
                                label=label,
                                category=labels.get("category", ""),
                            )
                        )
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_query_walletlabels_error", exc_info=True)
            logger.debug(f"walletLabels.xyz query failed for {address}: {e}")
        return entries

    # ── Source 4: bluepages.fyi ────────────────────────────────────

    async def _query_bluepages(self, address: str) -> list[LabelEntry]:
        """Query bluepages.fyi API for address labels."""
        entries = []
        try:
            url = f"https://api.bluepages.fyi/api/v1/labels/{address}"
            resp = await self._http.get(url, timeout=10.0)
            if resp.status_code == 200:
                body = resp.json()
                # bluepages may return a list of labels or single label
                items = body if isinstance(body, list) else [body]
                for item in items:
                    if isinstance(item, dict):
                        label = item.get("label", item.get("name", ""))
                        if label:
                            entries.append(
                                LabelEntry(
                                    source="bluepages",
                                    label=label,
                                    category=item.get("category", item.get("type", "")),
                                    confidence=item.get("confidence", 1.0),
                                )
                            )
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_query_bluepages_error", exc_info=True)
            logger.debug(f"bluepages.fyi query failed for {address}: {e}")
        return entries

    # ── Source 5: Internal RAG ────────────────────────────────────

    async def _query_internal_rag(self, address: str, chain: str) -> list[LabelEntry]:
        """Query internal RAG service for known scam and wallet profile data."""
        entries = []
        try:
            from app.rag_service import RAGService

            rag = RAGService()

            # Query known_scams collection
            results = await rag.query(
                f"address:{address} chain:{chain}",
                collection="known_scams",
                limit=5,
            )
            if results:
                for r in results:
                    label = r.get("label", r.get("name", r.get("project", "")))
                    if label:
                        entries.append(
                            LabelEntry(
                                source="rag_known_scams",
                                label=label,
                                category="scam",
                            )
                        )

            # Query wallet_profiles collection
            results = await rag.query(
                f"address:{address}",
                collection="wallet_profiles",
                limit=5,
            )
            if results:
                for r in results:
                    label = r.get("label", r.get("name", r.get("alias", "")))
                    if label:
                        entries.append(
                            LabelEntry(
                                source="rag_wallet_profiles",
                                label=label,
                                category=r.get("category", "wallet"),
                            )
                        )
        except ImportError:
            logger.debug("RAG service not available for address labeling")
        except (httpx.HTTPError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.debug(f"Internal RAG query failed for {address}: {e}")
        return entries

    # ── Source 6: Built-in database ────────────────────────────────

    def _query_builtin(self, address: str, chain: str) -> list[LabelEntry]:
        """Check built-in known databases for address labels."""
        entries = []
        addr_lower = address.lower()

        # Known scams
        if addr_lower in BUILTIN_KNOWN_SCAMS:
            info = BUILTIN_KNOWN_SCAMS[addr_lower]
            entries.append(
                LabelEntry(
                    source="builtin",
                    label=info["label"],
                    category=info["category"],
                )
            )

        # CEX hot wallets from chain_registry
        chain_cfg = CHAINS.get(chain)
        if chain_cfg:
            for cex_name, wallets in chain_cfg.cex_hot_wallets.items():
                for w in wallets:
                    if w.lower() == addr_lower:
                        entries.append(
                            LabelEntry(
                                source="builtin_cex",
                                label=cex_name,
                                category="exchange",
                            )
                        )
                        break

        return entries

    # ── Category inference ─────────────────────────────────────────

    def _infer_category(self, text: str) -> str:
        """Infer label category from text content."""
        text_lower = text.lower()
        if any(
            w in text_lower
            for w in (
                "exchange",
                "binance",
                "coinbase",
                "kraken",
                "okx",
                "bybit",
                "kucoin",
                "bitfinex",
                "gemini",
                "bitget",
                "mexc",
                "huobi",
                "htx",
                "gate.io",
                "upbit",
                "bitstamp",
                "poloniex",
            )
        ):
            return "exchange"
        if any(w in text_lower for w in ("mev", "sandwich", "flashbot", "jit", "front-run", "arbitrageur", "solver")):
            return "mev"
        if any(w in text_lower for w in ("rug", "scam", "exploit", "hack", "phish", "drainer", "impersonator", "fake")):
            return "scam"
        if any(w in text_lower for w in ("bridge", "hop", "across", "synapse", "stargate", "wormhole", "portal")):
            return "bridge"
        if any(
            w in text_lower
            for w in (
                "uniswap",
                "sushiswap",
                "curve",
                "aave",
                "compound",
                "maker",
                "lido",
                "yearn",
                "balancer",
                "1inch",
                "pancakeswap",
                "raydium",
            )
        ):
            return "defi"
        if any(w in text_lower for w in ("deployer", "creator", "factory", "contract")):
            return "contract"
        return ""

    # ── Risk computation ────────────────────────────────────────────

    def _compute_risk(self, report: AddressLabelReport) -> None:
        """Compute risk score from collected labels."""
        score = 0.0
        warnings = []
        red_flags = []

        # Known scam = very high risk
        if report.is_known_scam:
            score += 60
            red_flags.append("CRITICAL: Address is labeled as known scam/exploit")

        # Exchange addresses are generally safe
        if report.is_exchange:
            score -= 10  # reduce risk (exchanges are established entities)
            # But don't go below 0

        # MEV bot = moderate concern
        if report.is_mev_bot:
            score += 15
            warnings.append("MEDIUM: Address identified as MEV bot")

        # DeFi protocol = context-dependent
        if report.is_defi_protocol:
            # DeFi protocols are neutral - no risk adjustment
            pass

        # Bridge addresses are neutral
        if report.is_bridge:
            pass

        # Multiple scam labels = escalate
        scam_labels = [line for line in report.labels if line.get("category") == "scam"]
        if len(scam_labels) >= 2:
            score += 25
            red_flags.append(f"CRITICAL: {len(scam_labels)} sources flag address as scam")

        # No labels found = unknown entity (slightly suspicious)
        if not report.labels:
            score += 10
            warnings.append("LOW: No known labels found for address - unknown entity")

        # Negative risk from exchange doesn't go below 0
        score = max(0.0, min(100.0, score))
        report.risk_score = score

        if score >= 70:
            report.risk_level = "CRITICAL"
        elif score >= 45:
            report.risk_level = "HIGH"
        elif score >= 25:
            report.risk_level = "MEDIUM"
        else:
            report.risk_level = "LOW"

        report.warnings = warnings
        report.red_flags = red_flags

    # ── Main analysis entry point ──────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> AddressLabelReport:
        """Resolve address labels from all sources in parallel.

        This method analyzes the token_address (contract) itself.
        For standalone address lookups, use lookup_address() instead.
        """
        return await self.lookup_address(token_address, chain)

    async def lookup_address(self, address: str, chain: str) -> AddressLabelReport:
        """Resolve any address to known labels from multiple sources.

        Fetches from all 6 sources in parallel, then combines results.
        """
        report = AddressLabelReport(address=address, chain=chain)

        # Source 6: Built-in database (sync)
        builtin_entries = self._query_builtin(address, chain)

        # Run all async label sources in parallel
        async def _empty() -> list[LabelEntry]:
            return []

        tasks = []

        # Source 1: Etherscan (EVM only)
        if is_evm(chain):
            tasks.append(self._query_etherscan(address, chain))
        else:
            tasks.append(_empty())

        # Source 2: RolodETH (EVM only, mainly Ethereum mainnet addresses)
        if is_evm(chain):
            tasks.append(self._query_rolodeth(address))
        else:
            tasks.append(_empty())

        # Source 3: walletLabels.xyz
        tasks.append(self._query_walletlabels(address))

        # Source 4: bluepages.fyi
        tasks.append(self._query_bluepages(address))

        # Source 5: Internal RAG
        tasks.append(self._query_internal_rag(address, chain))

        # Gather results
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except httpx.HTTPError as e:
            logger.debug("_empty_error", exc_info=True)
            logger.warning(f"Label gathering failed for {address}: {e}")
            results = []

        # Collect all label entries
        all_entries = list(builtin_entries)
        for result in results:
            if isinstance(result, Exception):
                logger.debug(f"Label source failed: {result}")
                continue
            if isinstance(result, list):
                all_entries.extend(result)

        # Deduplicate and convert to dicts
        seen_labels = set()
        unique_entries = []
        for entry in all_entries:
            key = (entry.source, entry.label.lower())
            if key not in seen_labels and entry.label:
                seen_labels.add(key)
                unique_entries.append(entry)
                report.labels.append(
                    {
                        "source": entry.source,
                        "label": entry.label,
                        "category": entry.category,
                        "confidence": entry.confidence,
                    }
                )

        # Determine flags from labels
        for lbl in report.labels:
            cat = lbl.get("category", "").lower()
            label_text = lbl.get("label", "").lower()

            if cat == "exchange" or self._is_exchange_label(label_text):
                report.is_exchange = True
            if cat == "mev" or self._is_mev_label(label_text):
                report.is_mev_bot = True
            if cat == "scam" or self._is_scam_label(label_text):
                report.is_known_scam = True
            if cat == "defi":
                report.is_defi_protocol = True
            if cat == "bridge":
                report.is_bridge = True
            if cat == "contract" and "deployer" in label_text:
                report.isDeployer = True

        # Also check built-in CEX wallets
        chain_cfg = CHAINS.get(chain)
        if chain_cfg:
            addr_norm = address.lower()
            for _cex_name, wallets in chain_cfg.cex_hot_wallets.items():
                if addr_norm in [w.lower() for w in wallets]:
                    report.is_exchange = True

        # Set primary label
        if report.labels:
            # Prefer non-empty category labels
            categorized = [line for line in report.labels if line.get("category")]
            primary = categorized[0] if categorized else report.labels[0]
            report.primary_label = primary["label"]
            report.primary_category = primary.get("category", "")

        # Compute risk
        self._compute_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"known scam address wallet label {chain}",
                chain=chain,
                address=address,
                scanner_type="address_labeling",
            )
            report.citations = rag_cits
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except (ConnectionError, OSError, TimeoutError):
            pass

        return report

    def _is_exchange_label(self, label: str) -> bool:
        """Check if label text indicates an exchange."""
        exchange_keywords = [
            "binance",
            "coinbase",
            "kraken",
            "okx",
            "bybit",
            "kucoin",
            "bitfinex",
            "gemini",
            "bitget",
            "mexc",
            "huobi",
            "htx",
            "gate.io",
            "upbit",
            "bitstamp",
            "poloniex",
            "bitmex",
            "crypto.com",
            "bithumb",
            "korbit",
            "lbank",
            "bitmart",
            "exchange",
            "hot wallet",
            "deposit",
            "withdrawal",
        ]
        return any(kw in label for kw in exchange_keywords)

    def _is_mev_label(self, label: str) -> bool:
        """Check if label text indicates an MEV bot."""
        mev_keywords = [
            "mev",
            "sandwich",
            "flashbot",
            "jit",
            "front-run",
            "arbitrageur",
            "solver",
            "searcher",
            "builder",
        ]
        return any(kw in label for kw in mev_keywords)

    def _is_scam_label(self, label: str) -> bool:
        """Check if label text indicates a scam/rug."""
        scam_keywords = [
            "rug",
            "scam",
            "exploit",
            "hack",
            "phish",
            "drainer",
            "impersonator",
            "fake",
            "impersonation",
            "suspicious",
        ]
        return any(kw in label for kw in scam_keywords)
