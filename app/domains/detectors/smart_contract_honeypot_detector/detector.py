"""Main honeypot detector class."""
from __future__ import annotations

import os
import re
import time as time_mod
from datetime import UTC, datetime

from app.core.logging import get_logger

from .constants import BENIGN_SELECTORS, EVM_CHAINS, HONEYPOT_THRESHOLDS, KNOWN_MALICIOUS_SELECTORS
from .models import FindingSeverity, HoneypotAnalysisResult, HoneypotType, SecurityFinding, TokenInfo
from .utils import fetch_with_fallback

logger = get_logger(__name__)

class HoneypotDetector:
    def __init__(self, api_keys: dict[str, str] | None = None, enable_deep_scan: bool = False) -> None:
        self.api_keys = api_keys or {}
        self.enable_deep_scan = enable_deep_scan
        self._explorer_keys: dict[str, str] = self._load_explorer_keys()

    def _load_explorer_keys(self) -> dict[str, str]:
        return {"ethereum": os.getenv("ETHERSCAN_KEY", ""), "bsc": os.getenv("BSCSCAN_KEY", ""), "polygon": os.getenv("POLYGONSCAN_KEY", ""), "arbitrum": os.getenv("ARBISCAN_KEY", "")}

    async def analyze_token(self, address: str, chain: str = "ethereum", deep_scan: bool = False) -> HoneypotAnalysisResult:
        start = time_mod.monotonic()
        address = address.strip().lower()
        if not re.match(r"^0x[a-fA-F0-9]{40}$", address):
            return HoneypotAnalysisResult(token=TokenInfo(address=address, chain=chain), risk_score=0, risk_label="invalid", warnings=[f"Invalid EVM address format: {address}"])
        token_info = await self._gather_token_info(address, chain)
        result = HoneypotAnalysisResult(token=token_info)
        await self._analyze_fees(address, chain, result)
        await self._analyze_ownership(address, chain, result)
        await self._analyze_contract_functions(address, chain, result)
        await self._analyze_liquidity(address, chain, result)
        await self._analyze_holders(address, chain, result)
        await self._analyze_market_data(address, chain, result)
        if deep_scan or self.enable_deep_scan:
            await self._deep_scan(address, chain, result)
        result.risk_score = self._compute_risk_score(result)
        result.risk_label = self._risk_score_to_label(result.risk_score)
        result.scan_duration_ms = int((time_mod.monotonic() - start) * 1000)
        return result

    async def _gather_token_info(self, address: str, chain: str) -> TokenInfo:
        info = TokenInfo(address=address, chain=chain)
        chain_info = EVM_CHAINS.get(chain, {})
        try:
            data, _src = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                info.name = pair.get("baseToken", {}).get("name", "")
                info.symbol = pair.get("baseToken", {}).get("symbol", "")
                info.price_usd = float(pair.get("priceUsd", 0) or 0)
                info.liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                info.volume_24h_usd = float(pair.get("volume", {}).get("h24", 0) or 0)
                info.market_cap_usd = float(pair.get("fdv", 0) or 0)
                info.chain_data_sources.append("dexscreener")
                pair_addr = pair.get("pairAddress", "")
                if pair_addr:
                    info.chain_data_sources.append(f"pair:{pair_addr}")
        except Exception as e:
            logger.debug(f"DexScreener failed: {e}")
        explorer_url = chain_info.get("explorer", "")
        explorer_key = self._explorer_keys.get(chain, "")
        if explorer_url:
            try:
                url = f"{explorer_url}?module=contract&action=getsourcecode&address={address}"
                if explorer_key:
                    url += f"&apikey={explorer_key}"
                data, _ = await fetch_with_fallback([url])
                if data and data.get("status") == "1":
                    source = data["result"][0]
                    info.is_verified = source.get("SourceCode", "") != ""
                    info.creator_address = source.get("ContractCreator", "")
                    abi_str = source.get("ABI", "")
                    if abi_str and abi_str != "Contract source code not verified":
                        info.is_verified = True
                    info.chain_data_sources.append(f"explorer:{chain}")
            except Exception as e:
                logger.debug(f"Explorer failed: {e}")
        return info

    async def _analyze_fees(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        try:
            data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    buy_tx = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
                    sell_tx = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0
                    if buy_tx > 10 and sell_tx == 0:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.SELL_RESTRICTION, severity=FindingSeverity.CRITICAL, description=f"Cannot sell tokens - 0 sells in 24h vs {buy_tx} buys", detail=f"Found on {pair.get('dexId', '?')} - {pair.get('pairAddress', '')[:12]}..."))
                    elif buy_tx > 50 and sell_tx < buy_tx * 0.05:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.HIGH_SELL_TAX, severity=FindingSeverity.HIGH, description="Extremely low sell volume - possible sell restriction", detail=f"Buys: {buy_tx}, Sells: {sell_tx} (ratio: {sell_tx / max(buy_tx, 1):.2%})"))
        except Exception as e:
            logger.debug(f"Fee analysis via DexScreener failed: {e}")
        try:
            data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                price_change = pair.get("priceChange", {}).get("h24", 0)
                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                if abs(price_change) > 90 and volume < 100:
                    result.warnings.append(f"Extreme price change ({price_change:+.1f}%) with negligible volume")
                if liquidity > 0 and volume > liquidity * 20:
                    result.findings.append(SecurityFinding(finding_type=HoneypotType.RUG_PULL, severity=FindingSeverity.MEDIUM, description=f"Unusually high volume/liquidity ratio ({volume / liquidity:.0f}x)", detail="Possible wash trading or manipulation"))
        except Exception:
            logger.debug("dex_market_analysis_failed %s", address[:12])

    async def _analyze_ownership(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                impl_storage_slot = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
                payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getStorageAt", "params": [address, impl_storage_slot, "latest"]}
                try:
                    resp = await client.post(rpc_url, json=payload)
                    if resp.status_code == 200:
                        impl_data = resp.json().get("result", "")
                        if impl_data and impl_data != "0x" + "0" * 64:
                            result.token.is_proxy = True
                            result.token.implementation_address = "0x" + impl_data[-40:]
                            result.findings.append(SecurityFinding(finding_type=HoneypotType.PROXY_UPGRADE, severity=FindingSeverity.HIGH, description="Upgradable proxy contract detected", detail=f"Implementation at {result.token.implementation_address}", code_reference="EIP-1967 proxy storage slot"))
                except Exception:
                    logger.debug("impl_storage_failed %s", address[:12])
                admin_slot = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
                try:
                    payload["params"] = [address, admin_slot, "latest"]
                    resp = await client.post(rpc_url, json=payload)
                    if resp.status_code == 200:
                        admin_data = resp.json().get("result", "")
                        if admin_data and admin_data != "0x" + "0" * 64:
                            result.token.owner_address = "0x" + admin_data[-40:]
                except Exception:
                    logger.debug("admin_slot_failed %s", address[:12])
        except Exception as e:
            logger.debug(f"Ownership analysis failed: {e}")

    async def _analyze_contract_functions(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]}
                resp = await client.post(rpc_url, json=payload)
                if resp.status_code != 200:
                    return
                bytecode = resp.json().get("result", "")
                if not bytecode or bytecode == "0x":
                    result.warnings.append("No contract bytecode found - not a contract")
                    return
                bytecode = bytecode[2:] if bytecode.startswith("0x") else bytecode
                selectors_found: set[str] = set()
                for i in range(0, len(bytecode) - 7, 2):
                    if bytecode[i:
                        i+2] == "63":
                        selector = "0x" + bytecode[i+2:i+10]
                        if len(selector) == 10:
                            selectors_found.add(selector)
                    if bytecode[i:
                        i+2] == "7f":
                        potential = "0x" + bytecode[i+2:i+10]
                        if len(potential) == 10:
                            selectors_found.add(potential)
                for selector in selectors_found:
                    if selector in BENIGN_SELECTORS:
                        continue
                    if selector in KNOWN_MALICIOUS_SELECTORS:
                        sig, htype, desc = KNOWN_MALICIOUS_SELECTORS[selector]
                        if selector == "0xf2fde38b" and htype == HoneypotType.UNKNOWN:
                            continue
                        severity = FindingSeverity.MEDIUM
                        if htype in (HoneypotType.SELFDESTRUCT, HoneypotType.LIQUIDITY_DRAIN):
                            severity = FindingSeverity.CRITICAL
                        elif htype in (HoneypotType.OWNER_MINT, HoneypotType.PROXY_UPGRADE, HoneypotType.BLACKLIST, HoneypotType.TRADING_PAUSE):
                            severity = FindingSeverity.HIGH
                        result.findings.append(SecurityFinding(finding_type=htype, severity=severity, description=desc or f"Malicious function pattern: {sig}", detail=f"Function: {sig}", code_reference=f"Selector {selector} in bytecode"))
                if "ff" in bytecode:
                    if not any(f.finding_type == HoneypotType.SELFDESTRUCT for f in result.findings):
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.SELFDESTRUCT, severity=FindingSeverity.CRITICAL, description="SELFDESTRUCT opcode found in contract bytecode", detail="Contract can be permanently destroyed - all funds lost", code_reference="SELFDESTRUCT (0xff) in bytecode"))
                if "f4" in bytecode and not result.token.is_proxy:
                    result.findings.append(SecurityFinding(finding_type=HoneypotType.PROXY_UPGRADE, severity=FindingSeverity.MEDIUM, description="DELEGATECALL used - possible proxy or delegate pattern", detail="Contract uses delegatecall pattern for execution forwarding", code_reference="DELEGATECALL (0xf4) in bytecode"))
        except Exception as e:
            logger.debug(f"Bytecode analysis failed: {e}")

    async def _analyze_liquidity(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        try:
            data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    pair_addr = pair.get("pairAddress", "")
                    if liquidity < HONEYPOT_THRESHOLDS["low_liquidity_threshold"]:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.RUG_PULL, severity=FindingSeverity.CRITICAL if liquidity < HONEYPOT_THRESHOLDS["tiny_liquidity_threshold"] else FindingSeverity.HIGH, description=f"Extremely low liquidity: ${liquidity:.2f}", detail=f"Pool: {pair.get('dexId', '?')} - {pair_addr[:12]}..."))
                    pair_created = pair.get("pairCreatedAt", 0)
                    if pair_created and liquidity > 1000:
                        age_hours = (datetime.now(UTC).timestamp() - pair_created / 1000) / 3600
                        if age_hours < 1:
                            result.warnings.append(f"Pool is less than 1 hour old ({age_hours:.1f}h) - very high risk")
                        elif age_hours < 24:
                            result.warnings.append(f"Pool is less than 24 hours old ({age_hours:.1f}h)")
        except Exception as e:
            logger.debug(f"Liquidity analysis failed: {e}")

    async def _analyze_holders(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        try:
            data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                result.chain_data_sources.append("dexscreener_holders")
        except Exception:
            logger.debug("holder_analysis_failed %s", address[:12])

    async def _analyze_market_data(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        try:
            data, _ = await fetch_with_fallback([f"https://api.dexscreener.com/latest/dex/tokens/{address}"])
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    price_change_h1 = pair.get("priceChange", {}).get("h1", 0) or 0
                    price_change_h24 = pair.get("priceChange", {}).get("h24", 0) or 0
                    volume_h24 = float(pair.get("volume", {}).get("h24", 0) or 0)
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    if price_change_h1 > 50 and liquidity < 50000:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.RUG_PULL, severity=FindingSeverity.HIGH, description=f"Suspicious pump pattern - +{price_change_h1:.0f}% in 1h with low liquidity"))
                    if price_change_h24 > 500 and liquidity < 50000:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.RUG_PULL, severity=FindingSeverity.CRITICAL, description=f"Extreme price movement (+{price_change_h24:.0f}%) with low liquidity"))
                    if liquidity > 0 and volume_h24 > liquidity * 50:
                        result.findings.append(SecurityFinding(finding_type=HoneypotType.RUG_PULL, severity=FindingSeverity.MEDIUM, description=f"Volume ({volume_h24:.0f}) is {volume_h24 / max(liquidity, 1):.0f}x liquidity - possible wash trading"))
                    result.chain_data_sources.append("dexscreener_market")
        except Exception:
            logger.debug("market_data_failed %s", address[:12])

    async def _deep_scan(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        import httpx
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return
        logger.debug(f"Deep scan enabled for {address[:10]} on {chain}")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
                if resp.status_code == 200:
                    latest_hex = resp.json().get("result", "0x0")
                    result.chain_data_sources.append(f"rpc_block:{int(latest_hex, 16)}")
        except Exception:
            logger.debug("deep_scan_failed %s", address[:12])

    def _compute_risk_score(self, result: HoneypotAnalysisResult) -> int:
        score = 0
        for finding in result.findings:
            if finding.severity == FindingSeverity.CRITICAL:
                score += 35
            elif finding.severity == FindingSeverity.HIGH:
                score += 20
            elif finding.severity == FindingSeverity.MEDIUM:
                score += 10
            elif finding.severity == FindingSeverity.LOW:
                score += 3
        score = min(score, 100)
        if score > 0 and score < 15:
            score = 15
        return score

    def _risk_score_to_label(self, score: int) -> str:
        if score >= 70:
            return "critical"
        if score >= 40:
            return "high"
        if score >= 20:
            return "medium"
        if score >= 5:
            return "low"
        return "safe"
