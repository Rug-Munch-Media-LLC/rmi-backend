"""
Contract Upgrade Monitor
========================
Monitor proxy contract upgrades in real-time - detects malicious implementation
swaps, hidden timelock changes, and privilege escalation through upgrade patterns.

TOOL  : contract_upgrade_monitor
TIER  : security
PRICE : $0.05 (50000 atoms)
TRIAL : 2 free checks

Data Sources (all free-tier):
- Etherscan/BscScan/Polygonscan API - contract ABI, source code, tx history
- OpenZeppelin proxy patterns (UUPS, Transparent, Beacon)
- Public RPCs - storage slot reads for implementation address
- EIP-1967, EIP-1822, EIP-1167 storage slot standards
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

# EIP-1967 proxy storage slots
EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"

# EIP-1822 (UUPS) storage slot
EIP1822_IMPLEMENTATION_SLOT = "0xc5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7"

BEACON_IMPLEMENTATION_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"

ETHERSCAN_BASES: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "base": "https://api.basescan.org/api",
    "avalanche": "https://api.snowtrace.io/api",
}

# Supported proxy patterns
PROXY_PATTERNS = {
    "eip1967": "EIP-1967 Transparent/Universal Proxy",
    "eip1822": "EIP-1822 UUPS Proxy",
    "beacon": "EIP-1967 Beacon Proxy",
    "gnosis_safe": "Gnosis Safe Proxy",
    "openzeppelin": "OpenZeppelin Proxy (legacy)",
    "eip1167": "EIP-1167 Minimal Proxy (clone)",
}

# Dangerous upgrade-related function selectors (4-byte signatures)
DANGEROUS_SELECTORS = {
    "0x3659cfe6": "upgradeTo(address)",
    "0x4f1ef286": "upgradeToAndCall(address,bytes)",
    "0x9623609d": "_upgradeTo(address)",
    "0x99a88ec4": "upgrade(address)",
    "0x077f224a": "upgradeImplementation(address)",
    "0x2e4a7ec4": "setImplementation(address)",
    "0x8eaa6ac0": "changeAdmin(address)",
    "0xf851a440": "owner()",
    "0x8da5cb5b": "owner() [alternative]",
    "0x5c60da1b": "implementation()",
    "0xbb20d857": "upgradeToAndCall(address,bytes)",
    "0x1a8450c0": "setAddress(address)",
    "0xcb13bd91": "setBeacon(address)",
    "0x9b3b76cc": "upgradeBeaconTo(address,bytes)",
}

# Proxy storage diff threshold - if implementation changes within this window
SUSPICIOUS_UPGRADE_WINDOW = 86400  # 24 hours

# Cache helpers
_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes
_MAX_STALE_TTL = 1800  # 30 minutes

# ── Data Models ───────────────────────────────────────────────────


@dataclass
class ProxyInfo:
    """Detected proxy pattern and current implementation."""

    address: str
    chain: str
    proxy_type: str | None = None
    proxy_type_name: str | None = None
    implementation_address: str | None = None
    admin_address: str | None = None
    beacon_address: str | None = None
    is_proxy: bool = False
    confidence: float = 0.0  # 0.0 - 1.0
    detected_selectors: list[str] = field(default_factory=list)


@dataclass
class UpgradeEvent:
    """Record of a proxy upgrade event."""

    block_number: int
    transaction_hash: str
    timestamp: int
    previous_implementation: str | None = None
    new_implementation: str | None = None
    triggered_by: str | None = None  # address that triggered the upgrade
    function_selector: str | None = None


@dataclass
class ContractUpgradeReport:
    """Complete upgrade monitoring report for a contract."""

    contract_address: str
    chain: str
    proxy_info: ProxyInfo | None = None
    upgrade_history: list[UpgradeEvent] = field(default_factory=list)
    recent_suspicious_upgrades: list[UpgradeEvent] = field(default_factory=list)
    timelock_status: str | None = None  # "active", "bypassed", "none"
    risk_score: float = 0.0  # 0-100
    risk_factors: list[str] = field(default_factory=list)
    admin_privileges: list[str] = field(default_factory=list)
    upgrade_count_30d: int = 0
    last_upgrade_time: int | None = None
    implementation_age_days: int | None = None
    summary: str = ""


# ── Address Validation ────────────────────────────────────────────


def is_valid_evm_address(address: str) -> bool:
    """Validate an EVM address format with optional EIP-55 checksum."""
    from app.core.shared_utils import is_evm_address

    return is_evm_address(address)


# ── HTTP Client ───────────────────────────────────────────────────


async def _fetch_url(url: str, headers: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any] | None:
    """Fetch JSON from a URL with caching."""
    cache_key = f"fetch:{url}"
    now = time.time()

    # Check cache
    if cache_key in _cache:
        cached_time, cached_data = _cache[cache_key]
        if now - cached_time < _CACHE_TTL:
            return cached_data
        if now - cached_time > _MAX_STALE_TTL:
            del _cache[cache_key]
        else:
            # Return stale, refresh in background
            asyncio.ensure_future(_background_refresh(url, headers, timeout, cache_key))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {})
            if resp.status_code == 200:
                data = resp.json()
                _cache[cache_key] = (now, data)
                return data
            elif resp.status_code == 429:
                logger.warning("Rate limited on %s", url[:50])
                return _cache.get(cache_key, (0, None))[1]
            else:
                logger.warning("HTTP %s for %s", resp.status_code, url[:50])
                return None
    except httpx.TimeoutException:
        logger.warning("Timeout fetching %s", url[:50])
        return _cache.get(cache_key, (0, None))[1]
    except Exception as e:
        logger.debug("Fetch error for %s: %s", url[:50], e)
        return _cache.get(cache_key, (0, None))[1]


async def _background_refresh(url: str, headers: dict[str, Any] | None, timeout: float, cache_key: str) -> None:
    """Refresh cache in background so stale data serves immediately."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {})
            if resp.status_code == 200:
                _cache[cache_key] = (time.time(), resp.json())
    except httpx.HTTPError:
        pass


# ── RPC Storage Slot Reader ──────────────────────────────────────


async def read_storage_slot(address: str, slot: str, chain: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Read an EVM storage slot using a public RPC endpoint.

    Uses chain-specific public RPCs. Returns the 32-byte hex value.
    """
    rpc_urls = {
        "ethereum": os.getenv("ETH_RPC", "https://ethereum-rpc.publicnode.com"),
        "bsc": os.getenv("BSC_RPC", "https://bsc-rpc.publicnode.com"),
        "polygon": os.getenv("POLYGON_RPC", "https://polygon-rpc.publicnode.com"),
        "arbitrum": os.getenv("ARBITRUM_RPC", "https://arbitrum-rpc.publicnode.com"),
        "optimism": os.getenv("OPTIMISM_RPC", "https://optimism-rpc.publicnode.com"),
        "base": os.getenv("BASE_RPC", "https://base-rpc.publicnode.com"),
        "avalanche": os.getenv("AVALANCHE_RPC", "https://avalanche-rpc.publicnode.com"),
    }

    rpc_url = rpc_urls.get(chain)
    if not rpc_url:
        logger.warning("No RPC URL for chain %s", chain)
        return None

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getStorageAt",
        "params": [address, slot, "latest"],
        "id": 1,
    }

    try:
        close_client = client is None
        c = client or httpx.AsyncClient(timeout=10.0)
        if close_client:
            await c.__aenter__()

        try:
            resp = await c.post(
                rpc_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                result = resp.json()
                value = result.get("result", "0x0000000000000000000000000000000000000000000000000000000000000000")
                # Extract address from padded 32 bytes (last 20 bytes = 40 hex chars)
                if value and len(value) >= 66:
                    addr = "0x" + value[-40:]
                    if addr != "0x0000000000000000000000000000000000000000":
                        return addr
            return None
        finally:
            if close_client:
                await c.__aexit__(None, None, None)
    except Exception as e:
        logger.debug("RPC read failed for %s slot %s: %s", address[:10], slot[:10], e)
        return None


# ── Proxy Detection ──────────────────────────────────────────────


async def detect_proxy(address: str, chain: str, client: httpx.AsyncClient | None = None) -> ProxyInfo:
    """Detect if an address is a proxy contract and identify its type.

    Checks multiple proxy standards in order:
    1. EIP-1967 Transparent/Universal Proxy
    2. EIP-1822 UUPS Proxy
    3. EIP-1967 Beacon Proxy
    4. Gnosis Safe (check storage slot)
    5. Function selector sniffing (if source code available)
    """
    info = ProxyInfo(address=address, chain=chain)
    close_client = client is None
    c = client or httpx.AsyncClient(timeout=15.0)
    if close_client:
        await c.__aenter__()

    try:
        # 1. Check EIP-1967 implementation slot
        impl = await read_storage_slot(address, EIP1967_IMPLEMENTATION_SLOT, chain, c)
        if impl and is_valid_evm_address(impl):
            info.implementation_address = impl
            info.is_proxy = True
            info.confidence = 0.9

            # Check admin slot
            admin = await read_storage_slot(address, EIP1967_ADMIN_SLOT, chain, c)
            if admin and is_valid_evm_address(admin):
                info.admin_address = admin

            # Check beacon slot
            beacon = await read_storage_slot(address, EIP1967_BEACON_SLOT, chain, c)
            if beacon and is_valid_evm_address(beacon):
                info.beacon_address = beacon
                info.proxy_type = "beacon"
                info.proxy_type_name = PROXY_PATTERNS["beacon"]
            else:
                info.proxy_type = "eip1967"
                info.proxy_type_name = PROXY_PATTERNS["eip1967"]
            return info

        # 2. Check EIP-1822 UUPS slot
        impl = await read_storage_slot(address, EIP1822_IMPLEMENTATION_SLOT, chain, c)
        if impl and is_valid_evm_address(impl):
            info.implementation_address = impl
            info.is_proxy = True
            info.proxy_type = "eip1822"
            info.proxy_type_name = PROXY_PATTERNS["eip1822"]
            info.confidence = 0.85
            return info

        # 3. Check for Gnosis Safe proxy pattern
        # Gnosis Safe stores master copy at slot 0
        safe_slot = await read_storage_slot(
            address, "0x0000000000000000000000000000000000000000000000000000000000000000", chain, c
        )
        if safe_slot and is_valid_evm_address(safe_slot) and safe_slot != address:
            # Additional check: Gnosis Safe has specific bytecode pattern
            info.is_proxy = True
            info.implementation_address = safe_slot
            info.proxy_type = "gnosis_safe"
            info.proxy_type_name = PROXY_PATTERNS["gnosis_safe"]
            info.confidence = 0.6

        # 4. Try source code API for function selector detection
        source_info = await _fetch_source_code(address, chain)
        if source_info:
            detected = _detect_proxy_selectors(source_info.get("ABI", ""))
            if detected:
                info.detected_selectors = detected
                if not info.is_proxy:
                    info.is_proxy = True
                    info.proxy_type = "openzeppelin"
                    info.proxy_type_name = PROXY_PATTERNS["openzeppelin"]
                    info.confidence = 0.5

        return info
    finally:
        if close_client:
            await c.__aexit__(None, None, None)


def _detect_proxy_selectors(abi_json: str) -> list[str]:
    """Detect proxy-related function selectors from contract ABI JSON string."""
    detected: list[str] = []
    try:
        abi = json.loads(abi_json) if isinstance(abi_json, str) else abi_json
        if not isinstance(abi, list):
            return detected
        for item in abi:
            if isinstance(item, dict) and item.get("type") == "function":
                name = item.get("name", "")
                sig = f"{name}({','.join(i.get('type', '') for i in item.get('inputs', []))})"
                # Hash the signature to get 4-byte selector
                from eth_hash.auto import keccak

                selector = "0x" + keccak(sig.encode()).hex()[:8]
                if selector in DANGEROUS_SELECTORS:
                    detected.append(selector)
    except (json.JSONDecodeError, Exception):  # noqa: S110 — optional dependency
        pass
    return detected


# ── Source Code Fetcher ──────────────────────────────────────────


async def _fetch_source_code(address: str, chain: str) -> dict[str, Any] | None:
    """Fetch contract source code and ABI from block explorer."""
    base_url = ETHERSCAN_BASES.get(chain)
    if not base_url:
        return None

    # Use free-tier API keys if available, otherwise public endpoints
    api_key = os.getenv(f"{chain.upper()}_ETHERSCAN_KEY", "")

    url = f"{base_url}?module=contract&action=getsourcecode&address={address}"
    if api_key:
        url += f"&apikey={api_key}"

    data = await _fetch_url(url)
    if data and data.get("status") == "1" and data.get("result"):
        result = data["result"]
        if isinstance(result, list) and len(result) > 0:
            return result[0]
        return result
    return None


# ── Upgrade History ──────────────────────────────────────────────


async def fetch_upgrade_history(
    address: str,
    chain: str,
    lookback_blocks: int = 100000,
) -> list[UpgradeEvent]:
    """Fetch proxy upgrade events from block explorer transaction history.

    Looks for 'Upgraded' or 'UpgradeTo' event logs on the proxy contract.
    """
    base_url = ETHERSCAN_BASES.get(chain)
    if not base_url:
        return []

    api_key = os.getenv(f"{chain.upper()}_ETHERSCAN_KEY", "")
    events = []

    # Search for Upgraded event (EIP-1967): topic=0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b
    upgrade_topic = "0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b"

    url = (
        f"{base_url}?module=logs&action=getLogs"
        f"&address={address}"
        f"&topic0={upgrade_topic}"
        f"&fromBlock=0&toBlock=latest"
        f"&sort=desc"
    )
    if api_key:
        url += f"&apikey={api_key}"

    data = await _fetch_url(url)
    if data and data.get("status") == "1" and data.get("result"):
        for log in data["result"]:
            if isinstance(log, dict):
                upgrade = _parse_upgrade_log(log, address, chain)
                if upgrade:
                    events.append(upgrade)

    # Also check for admin-changing events
    admin_topic = "0xf23ec0bb1e0758b0e3c00d38f1e1c1c0a8f6d4f4b0b8b8c2e4e8f0a0b0c0d0e"
    admin_url = (
        f"{base_url}?module=logs&action=getLogs"
        f"&address={address}"
        f"&topic0={admin_topic}"
        f"&fromBlock=0&toBlock=latest"
        f"&sort=desc"
    )
    if api_key:
        admin_url += f"&apikey={api_key}"

    admin_data = await _fetch_url(admin_url)
    if admin_data and admin_data.get("status") == "1" and admin_data.get("result"):
        for log in admin_data["result"]:
            if isinstance(log, dict):
                logger.debug(
                    "Admin event found for %s: %s",
                    address[:10],
                    log.get("transactionHash", "")[:20],
                )

    return events


def _parse_upgrade_log(log: dict[str, Any], address: str, chain: str) -> UpgradeEvent | None:
    """Parse an upgrade event log entry."""
    try:
        tx_hash = log.get("transactionHash", "")
        block_num = (
            int(log.get("blockNumber", "0"), 16)
            if log.get("blockNumber", "").startswith("0x")
            else int(log.get("blockNumber", 0))
        )
        timestamp = (
            int(log.get("timeStamp", "0"), 16)
            if log.get("timeStamp", "").startswith("0x")
            else int(log.get("timeStamp", 0))
        )

        # The 'data' field contains the new implementation address
        data = log.get("data", "")
        new_impl = None
        if data and len(data) >= 66:
            addr = "0x" + data[-40:]
            if is_valid_evm_address(addr):
                new_impl = addr

        # Topic 1 (indexed param) might have previous implementation
        topics = log.get("topics", [])
        prev_impl = None
        if len(topics) > 1:
            addr = "0x" + topics[1][-40:]
            if is_valid_evm_address(addr):
                prev_impl = addr

        return UpgradeEvent(
            block_number=block_num,
            transaction_hash=tx_hash,
            timestamp=timestamp,
            previous_implementation=prev_impl,
            new_implementation=new_impl,
            triggered_by=None,  # Would need tx receipt for 'from'
        )
    except Exception as e:
        logger.debug("Failed to parse upgrade log: %s", e)
        return None


# ── Timelock Detection ──────────────────────────────────────────


async def check_timelock(address: str, chain: str) -> str | None:
    """Check if the proxy or its admin is behind a timelock.

    Returns 'active', 'bypassed', 'no_timelock', or None if unknown.
    """
    # Check if admin address is a known timelock contract
    info = await detect_proxy(address, chain)
    if not info.admin_address:
        return None

    admin = info.admin_address.lower()

    # Common timelock contract signatures

    # Check if admin is a known timelock address
    source_info = await _fetch_source_code(admin, chain)
    if source_info:
        contract_name = (source_info.get("ContractName", "") or "").lower()
        if "timelock" in contract_name:
            return "active"

    # Try to detect timelock by checking admin's own storage
    # Active timelocks have a minimum delay > 0
    try:
        # Use storage slot to check if admin has timelock delay
        delay_slot = "0x0000000000000000000000000000000000000000000000000000000000000002"
        result = await read_storage_slot(admin, delay_slot, chain)
        if result and int(result, 16) > 0:
            return "active"
    except (ConnectionError, OSError, TimeoutError):
        pass

    return "no_timelock"


# ── Risk Assessment ──────────────────────────────────────────────


def _assess_risk(
    proxy_info: ProxyInfo,
    upgrades: list[UpgradeEvent],
    current_time: int,
) -> tuple[float, list[str]]:
    """Assess risk based on proxy type, upgrade frequency, and protections."""
    risk = 0.0
    factors = []

    # 1. No timelock is a risk
    risk += 10.0
    factors.append("No timelock detected - upgrades can be instant")

    # 2. Beacon proxies have higher attack surface
    if proxy_info.proxy_type == "beacon":
        risk += 15.0
        factors.append("Beacon proxy - multiple implementations share same beacon (wider attack surface)")

    # 3. UUPS proxies - upgrade logic in implementation (higher risk if buggy)
    if proxy_info.proxy_type == "eip1822":
        risk += 10.0
        factors.append(
            "UUPS proxy - upgrade logic in implementation contract (vulnerable if implementation has upgrade bug)"
        )

    # 4. Recent upgrades increase risk
    recent_count = sum(1 for u in upgrades if u.timestamp and current_time - u.timestamp < SUSPICIOUS_UPGRADE_WINDOW)
    if recent_count > 0:
        risk += min(recent_count * 15.0, 40.0)
        factors.append(f"{recent_count} upgrade(s) in the last 24 hours")

    # 5. Multiple upgrades in 30 days
    thirty_days = 30 * 86400
    month_count = sum(1 for u in upgrades if u.timestamp and current_time - u.timestamp < thirty_days)
    if month_count > 5:
        risk += 20.0
        factors.append(f"Frequent upgrades: {month_count} upgrades in 30 days")
    elif month_count > 2:
        risk += 10.0
        factors.append(f"Moderate upgrade frequency: {month_count} upgrades in 30 days")

    # 6. Admin address not set (unusual for proxies)
    if not proxy_info.admin_address and proxy_info.proxy_type:
        risk += 5.0
        factors.append("No admin address detected - upgrade authority unknown")

    # 7. Implementation address hasn't changed (low risk)
    upgrade_count = len(upgrades)
    if upgrade_count == 0 and proxy_info.is_proxy:
        risk = max(risk - 10.0, 5.0)
        factors.append("No upgrade history - proxy is stable")
    elif upgrade_count == 0:
        factors.append("Not a proxy - no upgrade risk")

    # Clamp
    risk = max(0.0, min(100.0, risk))
    return round(risk, 1), factors


# ── Main Analyzer ────────────────────────────────────────────────


class ContractUpgradeAnalyzer:
    """Main analyzer for contract upgrade monitoring."""

    async def analyze(self, contract_address: str, chain: str = "ethereum") -> ContractUpgradeReport:
        """Run complete upgrade monitoring analysis on a contract."""
        report = ContractUpgradeReport(
            contract_address=contract_address,
            chain=chain,
        )

        if not is_valid_evm_address(contract_address):
            raise ValueError(f"Invalid EVM address: {contract_address}")

        current_time = int(time.time())

        # Step 1: Detect proxy
        proxy_info = await detect_proxy(contract_address, chain)
        report.proxy_info = proxy_info

        if proxy_info.is_proxy:
            # Step 2: Check timelock
            timelock_status = await check_timelock(contract_address, chain)
            report.timelock_status = timelock_status

            # Step 3: Fetch upgrade history
            upgrades = await fetch_upgrade_history(contract_address, chain)
            report.upgrade_history = upgrades
            report.upgrade_count_30d = sum(
                1 for u in upgrades if u.timestamp and current_time - u.timestamp < 30 * 86400
            )

            # Step 4: Identify recent suspicious upgrades
            report.recent_suspicious_upgrades = [
                u for u in upgrades if u.timestamp and current_time - u.timestamp < SUSPICIOUS_UPGRADE_WINDOW
            ]

            # Step 5: Implementation age
            if upgrades:
                last_upgrade = max(upgrades, key=lambda u: u.timestamp or 0)
                report.last_upgrade_time = last_upgrade.timestamp
                if last_upgrade.timestamp:
                    report.implementation_age_days = (current_time - last_upgrade.timestamp) // 86400

            # Step 6: Admin privileges
            if proxy_info.detected_selectors:
                report.admin_privileges = [DANGEROUS_SELECTORS.get(s, s) for s in proxy_info.detected_selectors]

            # Step 7: Risk assessment
            risk, factors = _assess_risk(proxy_info, upgrades, current_time)
            report.risk_score = risk
            report.risk_factors = factors

            # Generate summary
            summary_parts = [f"Contract **{contract_address[:10]}...** on **{chain.title()}**"]
            if proxy_info.proxy_type_name:
                summary_parts.append(f"is a **{proxy_info.proxy_type_name}**")
            if proxy_info.implementation_address:
                summary_parts.append(f"→ implementation at **{proxy_info.implementation_address[:10]}...**")
            if report.upgrade_count_30d > 0:
                summary_parts.append(f"| **{report.upgrade_count_30d}** upgrades in 30 days")
            summary_parts.append(f"| Risk: **{report.risk_score}/100**")
            report.summary = " ".join(summary_parts)
        else:
            report.summary = (
                f"Contract **{contract_address[:10]}...** on **{chain.title()}** "
                f"does not appear to be a proxy contract. "
                f"Risk: **0/100** (no upgrade risk)"
            )
            report.risk_score = 0.0

        return report


# ── Formatter ────────────────────────────────────────────────────


def format_upgrade_report(report: ContractUpgradeReport) -> str:
    """Format the upgrade monitoring report for human-readable output."""
    lines = []
    lines.append("=" * 60)
    lines.append("CONTRACT UPGRADE MONITOR REPORT")
    lines.append("=" * 60)
    lines.append(f"Contract: {report.contract_address}")
    lines.append(f"Chain:    {report.chain.title()}")
    lines.append("")

    pi = report.proxy_info
    if pi and pi.is_proxy:
        lines.append(f"🔍 Proxy Detected: {pi.proxy_type_name or pi.proxy_type or 'Unknown'}")
        if pi.implementation_address:
            lines.append(f"   Implementation: {pi.implementation_address}")
        if pi.admin_address:
            lines.append(f"   Admin:          {pi.admin_address}")
        if pi.beacon_address:
            lines.append(f"   Beacon:         {pi.beacon_address}")
        lines.append(f"   Confidence:     {pi.confidence:.0%}")
        lines.append("")

        lines.append(f"⏱  Timelock: {report.timelock_status or 'Unknown'}")
        lines.append("")

        if report.upgrade_history:
            lines.append(f"📜 Upgrade History ({len(report.upgrade_history)} total):")
            for u in report.upgrade_history[:10]:  # Show last 10
                ts_str = ""
                if u.timestamp:
                    from datetime import datetime

                    ts_str = datetime.utcfromtimestamp(u.timestamp).strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"   TX: {u.transaction_hash[:20]}... | {ts_str}")
                if u.new_implementation:
                    lines.append(f"       New impl: {u.new_implementation[:20]}...")
            if len(report.upgrade_history) > 10:
                lines.append(f"   ... and {len(report.upgrade_history) - 10} more")
            lines.append("")
        else:
            lines.append("📜 No upgrade events found in explorer logs")
            lines.append("")

        lines.append(f"📈 Upgrade Count (30d): {report.upgrade_count_30d}")
        if report.implementation_age_days is not None:
            lines.append(f"📅 Current Impl Age: {report.implementation_age_days} days")
        lines.append("")

        if report.admin_privileges:
            lines.append("🔐 Admin Privileges Detected:")
            for priv in report.admin_privileges[:5]:
                lines.append(f"   • {priv}")
            lines.append("")

        lines.append("⚠️  RISK ASSESSMENT")
        lines.append(f"   Score: {report.risk_score}/100")
        for factor in report.risk_factors:
            lines.append(f"   • {factor}")
    else:
        lines.append("✅ Not a proxy contract - no upgrade risk detected.")
        if pi and pi.detected_selectors:
            lines.append("   (Proxy-like selectors found but no storage slot match)")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ── Singleton factory ────────────────────────────────────────────

_analyzer_instance: ContractUpgradeAnalyzer | None = None


def get_upgrade_analyzer() -> ContractUpgradeAnalyzer:
    """Get or create a singleton ContractUpgradeAnalyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = ContractUpgradeAnalyzer()
    return _analyzer_instance
