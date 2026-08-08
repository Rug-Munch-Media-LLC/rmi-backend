"""
Unified Label Service - Merges wallet_label_loader + address_labeler into one call.
===============================================================================
Sources:
  Static (loaded at startup into Redis):
    1. crypto-wallet-address-labels (105K+ Solana, 16K BSC/ETH, 15K phishing)
    2. OFAC crypto-sanctions-list
    3. Etherscan malicious labels CSV
    4. Internal wallet_database.json (Omega Forensic)

  Live (API calls on demand):
    5. Etherscan API - public address labels
    6. RolodETH - public GitHub label database
    7. walletLabels.xyz - public address label API
    8. bluepages.fyi - public address label API

  Built-in:
    9. CEX hot wallet labels from chain_registry

Returns a unified label report with deduplication and confidence scoring.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger

logger = get_logger("wallet_memory.labels")

# ── RolodETH cache ──────────────────────────────────────────────

_ROLODETH_CACHE: dict[str, list[dict[str, Any]]] | None = None
_ROLODETH_LAST_FETCH: float = 0.0
_ROLODETH_TTL = 3600  # 1 hour


class LabelService:
    """Unified label resolution across static + live + built-in sources."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._builtin_cex: dict[str, str] = {}
        self._load_builtin_cex()

    def _load_builtin_cex(self) -> None:
        """Load CEX hot wallet labels from chain_registry."""
        try:
            from app.chain_registry import CEX_HOT_WALLETS

            for _chain, wallets in CEX_HOT_WALLETS.items():
                for addr, label in wallets.items():
                    self._builtin_cex[addr.lower()] = label
        except (AttributeError, ImportError):
            pass

    @staticmethod
    def _build_static_label(label_data: dict[str, Any], chain: str) -> dict[str, Any]:
        """Build a label dict from static Redis label data."""
        return {
            "source": f"static_{label_data.get('chain', chain)}",
            "label": label_data.get("address_name")
            or label_data.get("dapp_name")
            or label_data.get("protocol_name")
            or label_data.get("label_type", ""),
            "category": label_data.get("label_type") or label_data.get("label_subtype", ""),
            "confidence": 0.95,
        }

    @staticmethod
    def _build_ofac_label(ofac_data: dict[str, Any]) -> dict[str, Any]:
        """Build a label dict from OFAC sanctions data."""
        return {
            "source": "ofac",
            "label": ofac_data.get("name", "OFAC Sanctioned"),
            "category": "sanctioned",
            "confidence": 1.0,
        }

    @staticmethod
    def _build_wallet_label(wallet_data: dict[str, Any], category: str) -> dict[str, Any]:
        """Build a label dict from Omega Forensic wallet data."""
        return {
            "source": "omega_forensic",
            "label": wallet_data.get("display_name") or wallet_data.get("category", ""),
            "category": category,
            "confidence": 0.9,
        }

    @staticmethod
    def _build_cex_label(name: str) -> dict[str, Any]:
        """Build a label dict for a built-in CEX address."""
        return {
            "source": "builtin_cex",
            "label": name,
            "category": "exchange",
            "confidence": 1.0,
        }

    async def resolve(self, address: str, chain: str) -> dict[str, Any] | None:
        """
        Resolve an address across all label sources.

        Returns:
            {
                "address": str,
                "chain": str,
                "labels": [{"source": str, "label": str, "category": str, "confidence": float}],
                "primary_label": str,
                "primary_category": str,
                "is_exchange": bool,
                "is_mev_bot": bool,
                "is_known_scam": bool,
                "is_defi_protocol": bool,
                "is_bridge": bool,
                "is_sanctioned": bool,
                "risk_contribution": float,
            }
        """
        addr = address.lower().strip()
        all_labels: list[dict[str, Any]] = []
        flags = {
            "is_exchange": False,
            "is_mev_bot": False,
            "is_known_scam": False,
            "is_defi_protocol": False,
            "is_bridge": False,
            "is_sanctioned": False,
        }

        # ── Source 0: ClickHouse — 39.4M wallet_labels (FASTEST, LOCAL) ──
        try:
            from app.domains.labels.sources.clickhouse_labels import (
                query_clickhouse_labels,
            )

            ch_labels = await query_clickhouse_labels(addr, chain)
            for lbl in ch_labels:
                label_dict = {
                    "source": "clickhouse_wallet_memory",
                    "label": lbl.label_value,
                    "category": lbl.label_type,
                    "confidence": lbl.confidence,
                }
                all_labels.append(label_dict)
                # Set flags based on label attributes
                cat = lbl.label_type.lower()
                if cat == "exchange":
                    flags["is_exchange"] = True
                elif cat == "mev":
                    flags["is_mev_bot"] = True
                elif cat in ("scam", "phishing", "hack", "exploit", "fraud", "category"):
                    flags["is_known_scam"] = True
                elif cat == "defi":
                    flags["is_defi_protocol"] = True
                elif cat == "bridge":
                    flags["is_bridge"] = True
                # Check attributes for sanctioned flag
                if lbl.attributes.get("is_sanctioned"):
                    flags["is_sanctioned"] = True

                # Write-through to Redis for future fast lookups
                try:
                    from .storage import get_storage

                    storage = get_storage()
                    redis = await storage._get_redis() if hasattr(storage, "_get_redis") else None
                    if not redis:
                        await storage._ensure_connections()
                        redis = storage._redis
                    if redis:
                        cache_key = f"rmi:label:{lbl.chain}:{addr}"
                        import json as _json

                        await redis.setex(cache_key, 3600, _json.dumps(label_dict))
                except Exception:  # noqa: S110 — best-effort cache
                    pass
        except Exception as e:
            logger.debug(f"ClickHouse label lookup failed for {addr}: {e}")

        # ── Source 1: Static Redis labels (wallet_label_loader) ───
        try:
            from .storage import get_storage

            storage = get_storage()
            redis = await storage._get_redis() if hasattr(storage, "_get_redis") else None
            # Try the direct redis method
            if not redis:
                await storage._ensure_connections()
                redis = storage._redis

            if redis:
                # Check chain-specific label
                label_json = await redis.get(f"rmi:label:{chain}:{addr}")
                if not label_json and not is_solana(chain):
                    # Try ethereum as fallback for EVM chains
                    label_json = await redis.get(f"rmi:label:ethereum:{addr}")

                if label_json:
                    label_data = json.loads(label_json)
                    all_labels.append(self._build_static_label(label_data, chain))

                # Check OFAC
                ofac_json = await redis.get(f"rmi:label:ofac:{addr}")
                if ofac_json:
                    ofac_data = json.loads(ofac_json)
                    flags["is_sanctioned"] = True
                    flags["is_known_scam"] = True
                    all_labels.append(self._build_ofac_label(ofac_data))

                # Check wallet database (Omega Forensic)
                wallet_json = await redis.get(f"rmi:wallet:labeled:{addr}")
                if wallet_json:
                    wallet_data = json.loads(wallet_json)
                    cat = wallet_data.get("category", "")
                    if cat in ("rug_puller", "scammer", "hacker", "phisher"):
                        flags["is_known_scam"] = True
                    all_labels.append(self._build_wallet_label(wallet_data, cat))
        except Exception as e:
            logger.debug(f"Static label lookup failed for {addr}: {e}")

        # ── Source 2: Built-in CEX labels ─────────────────────────
        if addr in self._builtin_cex:
            label = self._builtin_cex[addr]
            flags["is_exchange"] = True
            all_labels.append(self._build_cex_label(label))

        # ── Source 3: Etherscan API (live, EVM only) ──────────────
        if is_evm(chain) and self._etherscan_key:
            try:
                etherscan_labels = await self._query_etherscan(addr, chain)
                all_labels.extend(etherscan_labels)
                for lbl in etherscan_labels:
                    cat = lbl.get("category", "").lower()
                    if cat == "exchange":
                        flags["is_exchange"] = True
                    elif cat == "mev":
                        flags["is_mev_bot"] = True
                    elif cat in ("phishing", "scam", "hack"):
                        flags["is_known_scam"] = True
                    elif cat == "defi":
                        flags["is_defi_protocol"] = True
                    elif cat == "bridge":
                        flags["is_bridge"] = True
            except Exception as e:
                logger.debug(f"Etherscan label failed for {addr}: {e}")

        # ── Source 4: RolodETH (live, Ethereum, cached) ───────────
        if chain in ("ethereum", "eth"):
            try:
                rolodeth_labels = await self._query_rolodeth(addr)
                all_labels.extend(rolodeth_labels)
            except Exception as e:
                logger.debug(f"RolodETH failed for {addr}: {e}")

        # ── Source 5: walletLabels.xyz (live) ────────────────────
        try:
            wl_labels = await self._query_walletlabels_xyz(addr)
            all_labels.extend(wl_labels)
        except Exception as e:
            logger.debug(f"walletLabels.xyz failed for {addr}: {e}")

        # ── Source 6: bluepages.fyi (live) ────────────────────────
        try:
            bp_labels = await self._query_bluepages(addr)
            all_labels.extend(bp_labels)
        except Exception as e:
            logger.debug(f"bluepages.fyi failed for {addr}: {e}")

        # ── Deduplicate and rank ─────────────────────────────────
        deduped = self._deduplicate(all_labels)
        primary = self._pick_primary(deduped, flags)

        # Calculate risk contribution from labels
        risk_contrib = self._label_risk_contribution(deduped, flags)

        return {
            "address": addr,
            "chain": chain,
            "labels": deduped,
            "primary_label": primary.get("label", ""),
            "primary_category": primary.get("category", ""),
            **flags,
            "risk_contribution": risk_contrib,
        }

    # ── Live API methods ────────────────────────────────────────

    async def _query_etherscan(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Query Etherscan account API for address labels."""
        from app.domains.scanners.dev_reputation import ETHERSCAN_NETWORKS

        network = ETHERSCAN_NETWORKS.get(chain)
        if not network:
            return []

        url = network["url"]
        key = network["key"] or self._etherscan_key
        if not key:
            return []

        try:
            resp = await self._http.get(
                url,
                params={
                    "module": "account",
                    "action": "getaddressinfo",
                    "address": address,
                    "apikey": key,
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("status") != "1":
                return []

            result = data.get("result", {})
            labels = []
            if isinstance(result, dict):
                tag = result.get("tag", "")
                name = result.get("name", "")
                if name:
                    labels.append(
                        {
                            "source": "etherscan",
                            "label": name,
                            "category": self._infer_category(tag or name),
                            "confidence": 0.85,
                        }
                    )
            return labels
        except (ConnectionError, OSError, TimeoutError):
            return []

    async def _query_rolodeth(self, address: str) -> list[dict[str, Any]]:
        """Query RolodETH GitHub label database."""
        global _ROLODETH_CACHE, _ROLODETH_LAST_FETCH
        import time

        now = time.time()
        if _ROLODETH_CACHE is None or (now - _ROLODETH_LAST_FETCH) > _ROLODETH_TTL:
            try:
                resp = await self._http.get("https://raw.githubusercontent.com/RoleTiker/rolodETH/main/rolodETH.json")
                if resp.status_code == 200:
                    _ROLODETH_CACHE = resp.json()
                    _ROLODETH_LAST_FETCH = now
            except (ConnectionError, OSError, TimeoutError):
                pass

        if not _ROLODETH_CACHE:
            return []

        labels = []
        addr_lower = address.lower()
        for addr_key, entries in _ROLODETH_CACHE.items():
            if addr_lower == addr_key.lower():
                for entry in entries:
                    labels.append(
                        {
                            "source": "rolodeth",
                            "label": entry.get("name", entry.get("label", "")),
                            "category": self._infer_category(entry.get("tag", "")),
                            "confidence": 0.80,
                        }
                    )
                break

        return labels

    async def _query_walletlabels_xyz(self, address: str) -> list[dict[str, Any]]:
        """Query walletLabels.xyz API."""
        try:
            resp = await self._http.get(f"https://api.walletlabels.xyz/v1/address/{address}")
            if resp.status_code != 200:
                return []
            data = resp.json()
            labels = []
            for item in data.get("labels", data.get("result", [])):
                if isinstance(item, dict):
                    labels.append(
                        {
                            "source": "walletlabels.xyz",
                            "label": item.get("label", item.get("name", "")),
                            "category": self._infer_category(item.get("category", "")),
                            "confidence": 0.75,
                        }
                    )
            return labels
        except (ConnectionError, OSError, TimeoutError):
            return []

    async def _query_bluepages(self, address: str) -> list[dict[str, Any]]:
        """Query bluepages.fyi API."""
        try:
            resp = await self._http.get(f"https://bluepages.fyi/api/v1/address/{address}")
            if resp.status_code != 200:
                return []
            data = resp.json()
            labels = []
            entry = data.get("label", data.get("name", ""))
            if entry:
                labels.append(
                    {
                        "source": "bluepages.fyi",
                        "label": entry,
                        "category": self._infer_category(data.get("category", "")),
                        "confidence": 0.70,
                    }
                )
            return labels
        except (ConnectionError, OSError, TimeoutError):
            return []

    # ── Helpers ──────────────────────────────────────────────────

    def _infer_category(self, text: str) -> str:
        """Infer label category from text."""
        if not text:
            return "unknown"
        t = text.lower()
        if any(w in t for w in ("exchange", "binance", "coinbase", "kraken", "okx", "bitfinex")):
            return "exchange"
        if any(w in t for w in ("mev", "bot", "flashbot")):
            return "mev"
        if any(w in t for w in ("phish", "scam", "hack", "exploit", "rug", "drainer")):
            return "scam"
        if any(w in t for w in ("defi", "aave", "uniswap", "curve", "compound", "lido")):
            return "defi"
        if any(w in t for w in ("bridge", "hop", "across", "synapse", "stargate")):
            return "bridge"
        if any(w in t for w in ("deployer", "creator", "factory")):
            return "deployer"
        if any(w in t for w in ("sanction", "ofac")):
            return "sanctioned"
        return "wallet"

    def _deduplicate(self, labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate labels from different sources."""
        seen = set()
        deduped = []
        for lbl in labels:
            key = f"{lbl.get('label', '').lower()}:{lbl.get('category', '').lower()}"
            if key not in seen:
                seen.add(key)
                deduped.append(lbl)
        return deduped

    def _pick_primary(self, labels: list[dict[str, Any]], flags: dict[str, Any]) -> dict[str, Any]:
        """Pick the most informative primary label."""
        if not labels:
            return {"label": "", "category": "unknown"}

        # Priority: sanctioned > scam > exchange > defi > bridge > mev > wallet
        priority = {
            "sanctioned": 0,
            "scam": 1,
            "exchange": 2,
            "defi": 3,
            "bridge": 4,
            "mev": 5,
            "deployer": 6,
            "wallet": 7,
            "unknown": 8,
        }

        sorted_labels = sorted(
            labels,
            key=lambda line: (
                priority.get(line.get("category", "unknown"), 8),
                -line.get("confidence", 0),
            ),
        )
        return sorted_labels[0] if sorted_labels else {"label": "", "category": "unknown"}

    def _label_risk_contribution(self, labels: list[dict[str, Any]], flags: dict[str, Any]) -> float:
        """Calculate risk score contribution from labels (0-40 of 100)."""
        score = 0.0

        # Flags contribute directly
        if flags.get("is_sanctioned"):
            score += 40
        elif flags.get("is_known_scam"):
            score += 30
        if flags.get("is_exchange"):
            score -= 5  # Exchanges are generally safe
        if flags.get("is_defi_protocol"):
            score -= 3  # Known DeFi is slightly positive

        # Label categories contribute
        for lbl in labels:
            cat = lbl.get("category", "").lower()
            if cat == "sanctioned":
                score = max(score, 40)
            elif cat == "scam":
                score += 15
            elif cat == "mev":
                score += 5

        return max(0, min(40, score))


# ── Global singleton ────────────────────────────────────────────

_label_service: LabelService | None = None


def get_label_service() -> LabelService:
    global _label_service
    if _label_service is None:
        _label_service = LabelService()
    return _label_service
