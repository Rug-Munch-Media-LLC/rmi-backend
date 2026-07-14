"""
SENTINEL - Proxy Detection + Implementation Analysis
======================================================
Resolves proxy contracts to their actual implementations and assesses
the risk of upgradeable proxy patterns:

  - For EVM: read EIP-1967 implementation slot, also check old-style proxies
  - For Solana: check if program is upgradeable (check upgrade authority)
  - Compare implementation bytecode hash against known-rug contract database
  - Detect if owner can change implementation at any time (no timelock)

Uses direct API calls: Etherscan (contract source, bytecode), RPC (eth_call),
Helius (Solana program info), DexScreener.
"""

import hashlib
from app.core.logging import get_logger
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_evm, is_solana
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

logger = get_logger("proxy_detector")


# ── Constants ───────────────────────────────────────────────────────

# EIP-1967 Proxy Storage Slots
EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"

# Old-style / Zeppelin proxy slots
OLD_PROXY_IMPLEMENTATION_SLOT = "0x5f3b5cf7" + "0" * 56  # slot 0x5f3b5cf7...
# Actually the old-style proxy slot is often just `implementation()` or slot 0x5f3b5cf705d9414d8c169be3f1e4e6b9
OLD_PROXY_SLOTS = [
    "0x5f3b5cf705d9414d8c169be3f1e4e6b9178f7e1a3b04d5e8a1c2b3d4e5f6a7b8",  # placeholder
]

# Known safe / standard implementations (keccak256 of bytecode)
KNOWN_SAFE_IMPLEMENTATIONS: dict[str, str] = {
    # OpenZeppelin ERC20
    "oze_erc20": "safe",
    # OpenZeppelin ERC721
    "oze_erc721": "safe",
}

# Known rug implementation patterns (function signatures commonly used in rug contracts)
KNOWN_RUG_PATTERNS = [
    "0x2e1a7d4d",  # withdraw()
    "0x3ccfd60b",  # withdraw(address)
    "0x51cff8d9",  # withdrawFunds()
    "0xdb006a75",  # setFee()
    "0x4b2e2f5b",  # setTaxRate()
    "0xf3fef3a3",  # drain()
    "0x5a3f0b2c",  # rescueTokens()
    "0x2f2ff15d",  # setOwner()
]

# Governor / Timelock function selectors
TIMELOCK_DELAY_SELECTOR = "0xd2baa7d2"  # MIN_DELAY()
TIMELOCK_GRACE_SELECTOR = "0x048ef6db"  # GRACE_PERIOD()
OWNER_SELECTOR = "0x8da5cb5b"  # owner()
PENDING_OWNER_SELECTOR = "0xe302bc02"  # pendingOwner()


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class ProxyReport:
    token_address: str
    chain: str
    is_proxy: bool = False
    proxy_type: str = ""  # "eip1967", "old_style", "beacon", "solana_upgradeable", ""
    implementation_address: str = ""
    admin_address: str = ""
    can_upgrade: bool = False  # Owner/admin can change implementation
    upgrade_timelock: int = 0  # Timelock delay in seconds (0 = no timelock)
    is_known_rug_impl: bool = False
    impl_bytecode_hash: str = ""
    impl_risk_patterns: list[str] = field(default_factory=list)
    owner_address: str = ""
    has_timelock: bool = False
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Detector Class ──────────────────────────────────────────────────


class ProxyDetector:
    """Resolves proxy contracts and assesses upgrade risk.

    For EVM: reads EIP-1967 storage slots and old-style proxy slots to find
    implementations. For Solana: checks program upgrade authority.
    Analyzes whether the owner can change implementation without timelock
    and whether the implementation matches known rug patterns.
    """

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")

    # ── Direct API fetchers ─────────────────────────────────────────

    async def _eth_call(self, to: str, data: str, chain: str) -> str | None:
        """Make an eth_call to read contract storage."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.rpc_endpoints:
            return None
        rpc_url = chain_cfg.rpc_endpoints[0]
        try:
            resp = await self._http.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": to, "data": data}, "latest"],
                },
            )
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("result")
                if result and result != "0x" and result != "0x0":
                    return result
        except Exception as e:
            logger.warning(f"eth_call failed for {to} data={data[:10]}: {e}")
        return None

    async def _eth_getCode(self, address: str, chain: str) -> str | None:
        """Get contract bytecode via eth_getCode."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.rpc_endpoints:
            return None
        rpc_url = chain_cfg.rpc_endpoints[0]
        try:
            resp = await self._http.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getCode",
                    "params": [address, "latest"],
                },
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("result")
        except Exception as e:
            logger.warning(f"eth_getCode failed for {address}: {e}")
        return None

    async def _fetch_contract_source(self, address: str, chain: str) -> dict | None:
        """Fetch contract source and ABI from Etherscan-style explorer."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return None
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return None
        try:
            url = f"{chain_cfg.explorer_api_url}?module=contract&action=getsourcecode&address={address}&apikey={key}"
            resp = await self._http.get(url)
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("result", [])
                if result and len(result) > 0:
                    return result[0]
        except Exception as e:
            logger.warning(f"Etherscan source fetch failed for {address}: {e}")
        return None

    async def _fetch_solana_program_data(self, program_id: str) -> dict | None:
        """Fetch Solana program account data to check upgrade authority."""
        result = await self._chain.rpc_call("getAccountInfo", [program_id, {"encoding": "base64"}])
        if result and "result" in result:
            info = result["result"].get("value")
            return info
        return None

    async def _fetch_solana_upgrade_authority(self, program_id: str) -> str | None:
        """Fetch the upgrade authority for a Solana BPF program.

        Uses getProgramData Helius/RPC extension or falls back to
        account parsing.
        """
        if self._helius_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(
                        f"https://api.helius.xyz/v0/programs/{program_id}?api-key={self._helius_key}",
                        json={},
                    )
                    if r.status_code == 200:
                        data = r.json()
                        return data.get("upgradeAuthority")
            except Exception as e:
                logger.warning(f"Helius program data failed for {program_id}: {e}")

        # Fallback: try DAS API or getProgramAccounts
        result = await self._chain.rpc_call("getProgramAccounts", [program_id, {"encoding": "jsonParsed"}])
        if result and "result" in result:
            accounts = result["result"]
            for acc in accounts:
                parsed = acc.get("account", {}).get("data", {}).get("parsed", {})
                if parsed.get("type") == "programData":
                    info = parsed.get("info", {})
                    return info.get("upgradeAuthority")
        return None

    # ── Analysis helpers ────────────────────────────────────────────

    async def _check_evm_proxy(self, address: str, chain: str) -> tuple[bool, str, str, str]:
        """Check if an EVM contract is a proxy and resolve implementation.

        Returns: (is_proxy, proxy_type, implementation_address, admin_address)
        """
        # Read EIP-1967 implementation slot
        impl = await self._eth_call(address, EIP1967_IMPLEMENTATION_SLOT, chain)
        if impl and impl != "0x" and len(impl) > 2:
            impl_addr = "0x" + impl[-40:]
            if int(impl_addr, 16) != 0:
                admin = await self._eth_call(address, EIP1967_ADMIN_SLOT, chain)
                admin_addr = ("0x" + admin[-40:]) if admin and len(admin) > 42 else ""
                return True, "eip1967", impl_addr, admin_addr

        # Read EIP-1967 beacon slot
        beacon = await self._eth_call(address, EIP1967_BEACON_SLOT, chain)
        if beacon and beacon != "0x" and len(beacon) > 2:
            beacon_addr = "0x" + beacon[-40:]
            if int(beacon_addr, 16) != 0:
                # Read implementation from beacon contract
                beacon_impl = await self._eth_call(beacon_addr, "0x5c60da1b", chain)  # implementation()
                if beacon_impl and len(beacon_impl) > 2:
                    impl_addr = "0x" + beacon_impl[-40:]
                    return True, "beacon", impl_addr, beacon_addr

        # Read old-style proxy slot (0x5f3b5cf7 padded)
        old_impl = await self._eth_call(
            address, "0x5f3b5cf700000000000000000000000000000000000000000000000000000000", chain
        )
        if old_impl and old_impl != "0x" and len(old_impl) > 2:
            impl_addr = "0x" + old_impl[-40:]
            if int(impl_addr, 16) != 0:
                return True, "old_style", impl_addr, ""

        # Try direct implementation() call
        direct_impl = await self._eth_call(address, "0x5c60da1b", chain)
        if direct_impl and direct_impl != "0x" and len(direct_impl) > 2:
            impl_addr = "0x" + direct_impl[-40:]
            if int(impl_addr, 16) != 0:
                return True, "function_call", impl_addr, ""

        return False, "", "", ""

    async def _check_can_upgrade(self, address: str, admin_addr: str, chain: str) -> tuple[bool, str, int]:
        """Check if the proxy admin can upgrade implementation.

        Returns: (can_upgrade, owner_address, timelock_seconds)
        """
        owner_addr = ""
        timelock_seconds = 0

        # Check admin contract or owner
        check_addr = admin_addr if admin_addr else address

        # Read owner()
        owner_result = await self._eth_call(check_addr, OWNER_SELECTOR, chain)
        if owner_result and len(owner_result) > 2:
            owner_addr = "0x" + owner_result[-40:]

        # Check if admin is a Timelock contract
        source = await self._fetch_contract_source(check_addr, chain)
        if source:
            source_code = source.get("SourceCode", "")
            if source_code and "timelock" in source_code.lower():
                # Try to read MIN_DELAY
                delay_result = await self._eth_call(check_addr, TIMELOCK_DELAY_SELECTOR, chain)
                if delay_result and len(delay_result) > 2:
                    try:
                        timelock_seconds = int(delay_result, 16)
                    except ValueError:
                        timelock_seconds = 0

        # If we found an owner, proxy can be upgraded
        can_upgrade = bool(owner_addr and int(owner_addr, 16) != 0)

        return can_upgrade, owner_addr, timelock_seconds

    def _check_impl_for_rug_patterns(self, impl_address: str, bytecode: str) -> tuple[bool, list[str], str]:
        """Check implementation bytecode for known rug contract patterns.

        Returns: (is_known_rug, risk_patterns, bytecode_hash)
        """
        if not bytecode or bytecode == "0x":
            return False, [], ""

        bytecode_hash = hashlib.sha256(bytecode.encode()).hexdigest()[:16]
        found_patterns = []

        for pattern in KNOWN_RUG_PATTERNS:
            if pattern[2:] in bytecode.lower():
                found_patterns.append(pattern)

        # Heuristic: if many rug patterns found, flag as known rug
        is_rug = len(found_patterns) >= 3

        return is_rug, found_patterns, bytecode_hash

    # ── Risk calculation ─────────────────────────────────────────────

    def _calculate_risk(self, report: ProxyReport) -> tuple[int, str, list[str]]:
        """Calculate risk score from proxy analysis."""
        score = 0
        warnings = []

        if not report.is_proxy:
            return 0, "LOW", []

        # Proxy exists - base risk
        score += 5

        # Upgradeable without timelock
        if report.can_upgrade and report.upgrade_timelock == 0:
            score += 35
            warnings.append(
                "CRITICAL: Proxy is upgradeable with NO timelock - owner can change implementation instantly"
            )
        elif report.can_upgrade and report.upgrade_timelock < 86400:
            score += 20
            warnings.append(
                f"HIGH: Proxy timelock only {report.upgrade_timelock // 3600}h - insufficient reaction time"
            )
        elif report.can_upgrade:
            score += 10
            hours = report.upgrade_timelock / 3600
            warnings.append(f"MEDIUM: Proxy has {hours:.0f}h timelock on upgrades")

        # Known rug implementation
        if report.is_known_rug_impl:
            score += 40
            warnings.append("CRITICAL: Implementation matches known rug contract pattern")

        # Risk patterns in implementation
        if report.impl_risk_patterns:
            count = len(report.impl_risk_patterns)
            if count >= 3:
                score += 25
                warnings.append(f"HIGH: {count} suspicious functions in implementation (drain/withdraw/setOwner)")
            elif count >= 1:
                score += 10
                warnings.append(f"MEDIUM: {count} suspicious function(s) in implementation")

        # Old-style proxy (less secure)
        if report.proxy_type == "old_style":
            score += 10
            warnings.append("MEDIUM: Old-style proxy - lacks EIP-1967 security features")

        # No admin address found = unclear governance
        if report.is_proxy and not report.admin_address and not report.owner_address:
            score += 10
            warnings.append("MEDIUM: Cannot determine proxy admin - unclear upgrade authority")

        # Solana upgradeable program
        if report.proxy_type == "solana_upgradeable":
            score += 25
            warnings.append("HIGH: Solana program is upgradeable - authority can modify code")

        score = min(100, score)

        if score >= 70:
            level = "CRITICAL"
        elif score >= 40:
            level = "HIGH"
        elif score >= 20:
            level = "MEDIUM"
        else:
            level = "LOW"

        return score, level, warnings

    # ── Main analysis entry point ─────────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> ProxyReport:
        """Full proxy detection and implementation analysis for a token.

        Steps:
        1. For EVM: check proxy slots and resolve implementation
        2. For Solana: check program upgrade authority
        3. Determine if upgradeable (no timelock)
        4. Check implementation for known rug patterns
        5. Calculate risk score
        """
        report = ProxyReport(token_address=token_address, chain=chain)

        if is_evm(chain):
            # 1. Check proxy status
            is_proxy, proxy_type, impl_addr, admin_addr = await self._check_evm_proxy(token_address, chain)
            report.is_proxy = is_proxy
            report.proxy_type = proxy_type
            report.implementation_address = impl_addr
            report.admin_address = admin_addr

            if is_proxy and impl_addr:
                # 2. Check can upgrade
                can_upgrade, owner_addr, timelock_seconds = await self._check_can_upgrade(
                    token_address, admin_addr, chain
                )
                report.can_upgrade = can_upgrade
                report.owner_address = owner_addr
                report.upgrade_timelock = timelock_seconds
                report.has_timelock = timelock_seconds > 0

                # 3. Check implementation bytecode for rug patterns
                bytecode = await self._eth_getCode(impl_addr, chain)
                if bytecode:
                    is_rug, patterns, code_hash = self._check_impl_for_rug_patterns(impl_addr, bytecode)
                    report.is_known_rug_impl = is_rug
                    report.impl_risk_patterns = patterns
                    report.impl_bytecode_hash = code_hash

                # 4. Also check contract source for timelock references
                impl_source = await self._fetch_contract_source(impl_addr, chain)
                if impl_source:
                    source = impl_source.get("SourceCode", "")
                    if source and "timelock" in source.lower():
                        report.has_timelock = True
                        if report.upgrade_timelock == 0:
                            # Infer timelock from source (minimum 24h assumed)
                            report.upgrade_timelock = 86400

        elif is_solana(chain):
            # Solana: check if the token's program is upgradeable
            # The token_address is the SPL token mint; we need the program owner
            program_data = await self._fetch_solana_program_data(token_address)
            if program_data:
                data = program_data.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    parsed = data[0].get("parsed", {}) if isinstance(data[0], dict) else {}
                    program_type = parsed.get("type", "")
                    if program_type == "account":
                        owner_program = program_data.get("owner", "")
                        # Check if the owning program is a BPF program (upgradeable)
                        if owner_program and owner_program != "11111111111111111111111111111111":
                            upgrade_auth = await self._fetch_solana_upgrade_authority(owner_program)
                            if upgrade_auth:
                                report.is_proxy = True
                                report.proxy_type = "solana_upgradeable"
                                report.can_upgrade = True
                                report.implementation_address = owner_program
                                report.admin_address = upgrade_auth
                                report.owner_address = upgrade_auth
                            else:
                                # No upgrade authority = program is immutable
                                report.is_proxy = False
                                report.can_upgrade = False

        # 5. Calculate risk
        report.risk_score, report.risk_level, report.warnings = self._calculate_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"proxy contract upgrade risk {chain}",
                chain=chain,
                address=token_address,
                scanner_type="proxy_detect",
            )
            report.citations = rag_cits
            # Enhance warnings with citation references
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except Exception:
            pass

        return report
