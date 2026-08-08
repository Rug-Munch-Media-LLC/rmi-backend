"""Main MEV sandwich detector class."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

from .analysis import _check_pool_vulnerability, _estimate_mev_vulnerability_score, _is_sandwich_pattern
from .models import (
    MEVAttackType,
    MEVBotProfile,
    MEVScanReport,
    MEVSeverity,
    MEVTransaction,
    PoolDexType,
    PoolInfo,
    SandwichAttack,
)
from .registry import KNOWN_MEV_BOTS

logger = get_logger(__name__)


class MEVSandwichDetector:
    def __init__(self, chains: list[str] | None = None, data_dir: str | None = None) -> None:
        self.chains = chains or ["ethereum", "bsc", "arbitrum", "base", "optimism", "polygon"]
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "..", "data")
        self._bot_registry: dict[str, dict[str, Any]] = {}
        self._known_bots: dict[str, MEVBotProfile] = {}
        self._pools: dict[str, PoolInfo] = {}
        self._load_bot_registry()

    def _load_bot_registry(self) -> None:
        for addr, info in KNOWN_MEV_BOTS.items():
            addr_lower = addr.lower()
            self._bot_registry[addr_lower] = info
            bot = MEVBotProfile(
                address=addr_lower,
                chain=info["chain"],
                name=info["name"],
                attack_types=[
                    MEVAttackType(t) for t in info.get("tags", []) if t in {at.value for at in MEVAttackType}
                ],
                tags=info.get("tags", []),
            )
            self._known_bots[addr_lower] = bot
        bot_file = os.path.join(self.data_dir, "mev_bots.json")
        if os.path.exists(bot_file):
            try:
                with open(bot_file) as f:
                    extra_bots = json.load(f)
                for addr, info in extra_bots.items():
                    addr_lower = addr.lower()
                    if addr_lower not in self._bot_registry:
                        self._bot_registry[addr_lower] = info
                        self._known_bots[addr_lower] = MEVBotProfile(
                            address=addr_lower,
                            chain=info.get("chain", "unknown"),
                            name=info.get("name", ""),
                            attack_types=[
                                MEVAttackType(t)
                                for t in info.get("tags", [])
                                if t in {at.value for at in MEVAttackType}
                            ],
                            tags=info.get("tags", []),
                        )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load MEV bot registry: {e}")

    def is_known_bot(self, address: str) -> MEVBotProfile | None:
        return self._known_bots.get(address.lower())

    def identify_bot(self, address: str, chain: str) -> MEVBotProfile:
        addr_lower = address.lower()
        known = self._known_bots.get(addr_lower)
        if known:
            return known
        return MEVBotProfile(
            address=addr_lower,
            chain=chain,
            name=f"unknown_bot_{addr_lower[:8]}",
            attack_types=[MEVAttackType.SANDWICH],
            tags=["suspected_bot"],
        )

    async def scan(self, pool_address: str | None = None, block_range: tuple[int, int] | None = None) -> MEVScanReport:
        report = MEVScanReport()
        report.chains_scanned = self.chains
        for chain in self.chains:
            try:
                chain_result = await self._check_chain_mev_activity(chain, block_range)
                report.transactions_analyzed += chain_result.get("txs_checked", 0)
                for candidate in chain_result.get("sandwich_candidates", []):
                    frontrun = candidate.get("frontrun_tx", {})
                    victim = candidate.get("victim_tx", {})
                    backrun = candidate.get("backrun_tx", {})
                    if isinstance(frontrun, str):
                        continue
                    extracted, victim_loss, slippage = self._estimate_sandwich_value(chain, frontrun, victim, backrun)
                    bot_addr = candidate["bot_address"]
                    bot_profile = self.identify_bot(bot_addr, chain)
                    sandwich = SandwichAttack(
                        victim_address=candidate["victim_address"],
                        token_in=victim.get("token_in", "unknown"),
                        token_out=victim.get("token_out", "unknown"),
                        chain=chain,
                        pool_address=candidate["pool"],
                        block_number=victim.get("block_number", 0),
                        frontrun_tx=MEVTransaction(
                            tx_hash=frontrun.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim.get("block_number", 0),
                            tx_index=frontrun.get("tx_index", 0),
                            from_address=bot_addr,
                            to_address=candidate["pool"],
                            value_eth=frontrun.get("value_eth", 0),
                            gas_price_gwei=frontrun.get("gas_price_gwei", 0),
                            gas_used=frontrun.get("gas_used", 0),
                            method_signature=frontrun.get("method_signature", ""),
                        ),
                        victim_tx=MEVTransaction(
                            tx_hash=victim.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim.get("block_number", 0),
                            tx_index=victim.get("tx_index", 0),
                            from_address=candidate["victim_address"],
                            to_address=candidate["pool"],
                            value_eth=victim.get("value_eth", 0),
                            gas_price_gwei=victim.get("gas_price_gwei", 0),
                            gas_used=victim.get("gas_used", 0),
                            method_signature=victim.get("method_signature", ""),
                        ),
                        backrun_tx=MEVTransaction(
                            tx_hash=backrun.get("tx_hash", ""),
                            chain=chain,
                            block_number=victim.get("block_number", 0),
                            tx_index=backrun.get("tx_index", 0),
                            from_address=bot_addr,
                            to_address=candidate["pool"],
                            value_eth=backrun.get("value_eth", 0),
                            gas_price_gwei=backrun.get("gas_price_gwei", 0),
                            gas_used=backrun.get("gas_used", 0),
                            method_signature=backrun.get("method_signature", ""),
                        ),
                        bot_address=bot_addr,
                        estimated_extracted_usd=extracted,
                        victim_loss_usd=victim_loss,
                        slippage_impact_pct=slippage,
                        confidence=candidate["confidence"],
                        severity=MEVSeverity.CRITICAL
                        if extracted > 100
                        else MEVSeverity.HIGH
                        if extracted > 10
                        else MEVSeverity.MEDIUM
                        if extracted > 1
                        else MEVSeverity.LOW,
                        bot_name=bot_profile.name,
                    )
                    report.sandwiches.append(sandwich)
                    bot_profile.attacks_detected += 1
                    bot_profile.total_extracted_usd += extracted
                    if bot_profile.address not in [b.address for b in report.bots_detected]:
                        report.bots_detected.append(bot_profile)
                for pattern in chain_result.get("suspicious_patterns", []):
                    entry = {
                        "chain": chain,
                        "type": pattern["type"],
                        "bot_address": pattern["bot"],
                        "victim_address": pattern["victim"],
                        "tx_hash": pattern["tx"],
                        "target_tx_hash": pattern.get("target_tx", ""),
                    }
                    if pattern["type"] == "frontrun":
                        report.frontruns.append(entry)
                    else:
                        report.backruns.append(entry)
            except Exception as e:
                err = f"Chain {chain} scan error: {e}"
                logger.error(err)
                report.errors.append(err)
        for pool in self._pools.values():
            try:
                risk = await self._analyze_pool_mev_risk(pool)
                report.vulnerable_pools.append(risk)
                report.pools_analyzed += 1
            except Exception as e:
                logger.warning(f"Pool analysis error for {pool.address}: {e}")
        report.scan_end = datetime.now(UTC).isoformat()
        return report

    async def _check_chain_mev_activity(self, chain: str, block_range: tuple[int, int] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "chain": chain,
            "suspicious_patterns": [],
            "sandwich_candidates": [],
            "bot_hits": [],
            "txs_checked": 0,
        }
        data = await self._query_databus("fetch", {"data_type": "transactions", "chain": chain, "limit": 100})
        if not data:
            return result
        raw_txs: list[Any] = []
        if isinstance(data, list):
            raw_txs = data
        elif isinstance(data, dict):
            raw_txs = data.get("results", data.get("transactions", []))
        transactions = [t for t in raw_txs if isinstance(t, dict)]
        result["txs_checked"] = len(transactions)
        pool_groups: dict[str, list[dict[str, Any]]] = {}
        for tx in transactions:
            to_addr = str(tx.get("to_address", "") or "").lower()
            if to_addr:
                pool_groups.setdefault(to_addr, []).append(tx)
        for pool_addr, txs in pool_groups.items():
            sorted_txs = sorted(txs, key=lambda t: t.get("tx_index", 0))
            for i in range(len(sorted_txs) - 2):
                f_tx, v_tx, b_tx = sorted_txs[i], sorted_txs[i + 1], sorted_txs[i + 2]
                f_sender = f_tx.get("from_address", "")
                v_sender = v_tx.get("from_address", "")
                b_sender = b_tx.get("from_address", "")
                if not f_sender or not v_sender or not b_sender:
                    continue
                if f_sender == b_sender and f_sender != v_sender:
                    confidence = _is_sandwich_pattern(f_tx, v_tx, b_tx)
                    if confidence > 0.5:
                        result["sandwich_candidates"].append(
                            {
                                "pool": pool_addr,
                                "frontrun_tx": f_tx,
                                "victim_tx": v_tx,
                                "backrun_tx": b_tx,
                                "bot_address": f_sender,
                                "victim_address": v_sender,
                                "confidence": confidence,
                            }
                        )
                f_is_bot = self.is_known_bot(f_sender)
                b_is_bot = self.is_known_bot(b_sender)
                if f_is_bot and f_sender != v_sender:
                    result["suspicious_patterns"].append(
                        {
                            "type": "frontrun",
                            "bot": f_sender,
                            "victim": v_sender,
                            "tx": f_tx.get("tx_hash", ""),
                            "target_tx": v_tx.get("tx_hash", ""),
                        }
                    )
                if b_is_bot and b_sender != v_sender:
                    result["suspicious_patterns"].append(
                        {
                            "type": "backrun",
                            "bot": b_sender,
                            "victim": v_sender,
                            "tx": b_tx.get("tx_hash", ""),
                            "target_tx": v_tx.get("tx_hash", ""),
                        }
                    )
                for sender in [f_sender, b_sender]:
                    bot = self.is_known_bot(sender)
                    if bot and sender not in [h["address"] for h in result["bot_hits"]]:
                        result["bot_hits"].append({"address": sender, "name": bot.name, "detected_in": pool_addr})
        return result

    async def _query_databus(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            import httpx

            base_url = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")
            async with httpx.AsyncClient(timeout=10) as client:
                if params:
                    resp = await client.post(f"{base_url}/{endpoint}", json=params)
                else:
                    resp = await client.get(f"{base_url}/{endpoint}")
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"DataBus query failed ({resp.status_code}): {endpoint}")
        except ImportError:
            logger.debug("httpx not available, skipping DataBus query")
        except Exception as e:
            logger.error(f"DataBus query error: {e}")
        return None

    def _estimate_sandwich_value(
        self, chain: str, frontrun: dict[str, Any], victim: dict[str, Any], backrun: dict[str, Any]
    ) -> tuple[float, float, float]:
        bot_frontrun_gas = (frontrun.get("gas_price_gwei", 0) or 0) * (frontrun.get("gas_used", 21000) or 21000)
        bot_backrun_gas = (backrun.get("gas_price_gwei", 0) or 0) * (backrun.get("gas_used", 21000) or 21000)
        frontrun_value = frontrun.get("value_eth", 0) or 0
        backrun_value = backrun.get("value_eth", 0) or 0
        victim_value = victim.get("value_eth", 0) or 0
        eth_price = 3000.0
        gross_extracted = (backrun_value - frontrun_value) * eth_price
        gas_cost_eth = (bot_frontrun_gas + bot_backrun_gas) * 1e-9
        extracted_usd = max(0, gross_extracted - gas_cost_eth * eth_price)
        if frontrun_value > 0 and victim_value > 0:
            ratio = frontrun_value / max(victim_value, 0.001)
            slippage_pct = min(ratio * 0.05, 0.5)
        else:
            slippage_pct = 0.01
        victim_loss_usd = victim_value * eth_price * slippage_pct
        return extracted_usd, victim_loss_usd, slippage_pct * 100

    async def _analyze_pool_mev_risk(self, pool: PoolInfo) -> dict[str, Any]:
        flags = _check_pool_vulnerability(
            {"liquidity_usd": pool.liquidity_usd, "volume_24h_usd": pool.volume_24h_usd, "fee_tier": pool.fee_tier}
        )
        score = _estimate_mev_vulnerability_score(flags)
        return {
            "pool_address": pool.address,
            "chain": pool.chain,
            "name": f"{pool.token0_symbol}/{pool.token1_symbol}",
            "dex_type": pool.dex_type.value,
            "liquidity_usd": pool.liquidity_usd,
            "mev_vulnerability_score": round(score, 2),
            "flags": flags,
            "assessment": "high" if score > 0.6 else "medium" if score > 0.3 else "low",
        }

    def register_pool(self, pool: PoolInfo) -> None:
        self._pools[pool.address.lower()] = pool

    def register_pools_from_dict(self, pools: list[dict[str, Any]]) -> None:
        for p in pools:
            try:
                dex_type = PoolDexType(p.get("dex_type", "unknown"))
            except ValueError:
                dex_type = PoolDexType.UNKNOWN
            pool = PoolInfo(
                address=p["address"].lower(),
                chain=p.get("chain", "ethereum"),
                dex_type=dex_type,
                token0=p.get("token0", ""),
                token1=p.get("token1", ""),
                token0_symbol=p.get("token0_symbol", ""),
                token1_symbol=p.get("token1_symbol", ""),
                liquidity_usd=p.get("liquidity_usd", 0),
                volume_24h_usd=p.get("volume_24h_usd", 0),
                fee_tier=p.get("fee_tier", 3000),
            )
            self.register_pool(pool)

    def save_bot_registry(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        bot_file = os.path.join(self.data_dir, "mev_bots.json")
        serializable = {}
        for addr, profile in self._known_bots.items():
            serializable[addr] = {
                "name": profile.name,
                "chain": profile.chain,
                "tags": profile.tags,
                "attacks_detected": profile.attacks_detected,
                "total_extracted_usd": round(profile.total_extracted_usd, 2),
            }
        try:
            with open(bot_file, "w") as f:
                json.dump(serializable, f, indent=2)
            logger.info(f"Saved {len(serializable)} bot profiles to {bot_file}")
        except OSError as e:
            logger.error(f"Failed to save bot registry: {e}")
