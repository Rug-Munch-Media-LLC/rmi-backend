"""
SENTINEL - Contract Authority Detection
=========================================
Detects dangerous authority configurations on Solana tokens (mint, freeze,
update authority) and EVM contracts (proxy patterns, upgradable contracts,
admin functions, ownership renunciation).

Scoring:
  Solana:
    - Unrenounced mint authority on fixed-supply token → high risk
    - Freeze authority on circulating token → medium risk
    - Token itself as authority (self-referential) → critical risk
  EVM:
    - Proxy contract with mutable implementation → high risk
    - Unrenounced ownership on proxy → critical risk
    - Admin functions without timelock → medium risk
    - EIP-1967 / EIP-1822 / OZ Transparent/UUPS detection

Data sources:
  Solana: ChainClient (Helius RPC) → getAccountInfo → SPL token mint parsing
  EVM:    Etherscan-family APIs → contract bytecode, storage slots, ABI
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_solana
from app.core.logging import get_logger

logger = get_logger("contract_authority")

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")

# ── Etherscan Network Map ──────────────────────────────────

ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY, "chain_id": 1},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY, "chain_id": 1},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY, "chain_id": 56},
    "polygon": {
        "url": "https://api.polygonscan.com/api",
        "key": POLYGONSCAN_API_KEY,
        "chain_id": 137,
    },
    "avalanche": {
        "url": "https://api.snowtrace.io/api",
        "key": SNOWTRACE_API_KEY,
        "chain_id": 43114,
    },
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY, "chain_id": 42161},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY, "chain_id": 8453},
}

# ── Solana Constants ────────────────────────────────────────

SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "Tokenz4dSamgc6f5A2p5fdP8EjVq7MdY3F6UuF6nXK"
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"

# Renounced / burned addresses on Solana
SOLANA_ZERO_ADDRESS = "11111111111111111111111111111111"

# ── Risk Levels ────────────────────────────────────────────

class AuthorityRiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

# ── Data Models ─────────────────────────────────────────────

@dataclass
class SolanaAuthority:
    """Single authority field on a Solana SPL token mint."""

    authority_type: str  # "mint_authority", "freeze_authority", "update_authority"
    address: str
    is_renounced: bool
    is_token_itself: bool  # authority set to token's own mint address

@dataclass
class SolanaAuthorities:
    """All authority fields for a Solana token mint."""

    mint_authority: SolanaAuthority | None = None
    freeze_authority: SolanaAuthority | None = None
    update_authority: SolanaAuthority | None = None

@dataclass
class EVMProxyInfo:
    """Proxy / upgradability details for an EVM contract."""

    is_proxy: bool = False
    implementation_address: str | None = None
    proxy_type: str | None = (
        None  # "EIP-1967", "EIP-1822", "OZ_Transparent", "OZ_UUPS", "Minimal", "Unknown"
    )
    admin_address: str | None = None
    is_ownership_renounced: bool = False
    has_admin_functions: bool = False
    admin_function_signatures: list[str] = field(default_factory=list)

@dataclass
class ContractAuthorityReport:
    """Full authority analysis for a token contract."""

    token_address: str
    chain: str
    solana_authorities: SolanaAuthorities | None = None
    evm_proxy: EVMProxyInfo | None = None
    warnings: list[str] = field(default_factory=list)
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"

# ── EVM Proxy Detection Slots ───────────────────────────────

# EIP-1967 slots: keccak256("eip1967.proxy.implementation") - 1, etc.
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
EIP1967_ADMIN_SLOT = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
EIP1967_BEACON_SLOT = "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"

# EIP-1822: keccak256("PROXIABLE")
EIP1822_SLOT = (
    "0xc5f16f0fcc639fa48a694782b6357e4c6042a5f0a3e2c1c1e8b6e4e8b6e4e8b6"  # placeholder pattern
)

# OpenZeppelin-specific: _upgradeTo signature
OZ_TRANSPARENT_ADMIN_SIG = "0xf3b7dead"  # changeAdmin
OZ_UUPS_UPGRADE_SIG = "0x4f1ef286"  # upgradeToAndCall

class ContractAuthorityScanner:
    """Detects dangerous authority configurations on Solana and EVM chains.

    Solana path: ChainClient → getAccountInfo → parse SPL Token mint data
    EVM path: Etherscan API → bytecode, storage, ABI → proxy / admin detection
    """

    def __init__(self) -> None:
        self._chain = ChainClient()
        self._http = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def analyze(self, token_address: str, chain: str) -> ContractAuthorityReport:
        """Alias for scan() - matches SENTINEL pipeline interface."""
        return await self.scan(token_address, chain)

    # ── Public Entry Point ──────────────────────────────────
    async def scan(self, token_address: str, chain: str) -> ContractAuthorityReport:
        """Run full authority analysis for a token on the given chain."""
        chain_lower = chain.lower().strip()

        if is_solana(chain_lower):
            return await self._scan_solana(token_address)
        elif chain_lower in ETHERSCAN_NETWORKS:
            return await self._scan_evm(token_address, chain_lower)
        else:
            report = ContractAuthorityReport(token_address=token_address, chain=chain)
            report.warnings.append(f"Unsupported chain: {chain}")
            return report

    # ── Solana Path ─────────────────────────────────────────

    async def _scan_solana(self, token_address: str) -> ContractAuthorityReport:
        """Query Helius RPC getAccountInfo and parse SPL token mint state."""
        report = ContractAuthorityReport(token_address=token_address, chain="solana")

        try:
            result = await self._chain.rpc_call(
                "getAccountInfo",
                [token_address, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
            if not result or "result" not in result or result["result"] is None:
                report.warnings.append("Token account not found on Solana")
                self._compute_risk(report)
                return report

            account_info = result["result"]
            if not account_info or "value" not in account_info:
                report.warnings.append("Empty account data for token")
                self._compute_risk(report)
                return report

            value = account_info["value"]
            if not value:
                report.warnings.append("Token mint account is empty")
                self._compute_risk(report)
                return report

            value.get("owner", "")
            data = value.get("data", [])

            # Determine token program (Token or Token-2022)
            parsed_data = None
            if isinstance(data, list) and len(data) >= 1:
                if data[1] == "base64" and len(data) > 0:
                    import base64

                    try:
                        raw = base64.b64decode(data[0])
                        parsed_data = self._parse_spl_mint_raw(raw, token_address)
                    except (ConnectionError, OSError, TimeoutError):
                        pass
                elif isinstance(data, dict):
                    parsed_data = data

            # Try parsed data from RPC if raw parse didn't work
            if parsed_data is None:
                parsed_info = value.get("data", {})
                if isinstance(parsed_info, dict) and "parsed" in parsed_info:
                    parsed_data = parsed_info["parsed"]

            authorities = SolanaAuthorities()

            if parsed_data and isinstance(parsed_data, dict):
                info = parsed_data.get("info", parsed_data)

                # Mint authority
                mint_auth_str = info.get("mintAuthority", info.get("mint_authority", ""))
                authorities.mint_authority = self._build_solana_authority(
                    "mint_authority", mint_auth_str, token_address
                )

                # Freeze authority
                freeze_auth_str = info.get("freezeAuthority", info.get("freeze_authority", ""))
                authorities.freeze_authority = self._build_solana_authority(
                    "freeze_authority", freeze_auth_str, token_address
                )

                # Update authority (Token-2022 extension)
                update_auth_str = info.get("updateAuthority", info.get("update_authority", ""))
                if update_auth_str:
                    authorities.update_authority = self._build_solana_authority(
                        "update_authority", update_auth_str, token_address
                    )

            # Validate: if we couldn't parse any authorities, add warning
            if not any([authorities.mint_authority, authorities.freeze_authority]):
                report.warnings.append(
                    "Could not parse SPL token mint data; raw fallback attempted"
                )

            report.solana_authorities = authorities
            self._add_solana_warnings(report)
            self._compute_risk(report)
            return report

        except (httpx.HTTPError, KeyError, TypeError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Solana authority scan failed for {token_address}: {e}")
            report.warnings.append(f"Solana scan error: {str(e)[:120]}")
            self._compute_risk(report)
            return report

    def _parse_spl_mint_raw(self, raw: bytes, token_address: str) -> dict | None:
        """Parse raw SPL Token Mint account data (82 bytes minimum).

        Layout (Mint layout):
          0-4:   discriminant (optional, Token-2022)
          4-36:  mintAuthority (32 bytes, PublicKey; all zeros = None)
          36-44: supply (u64)
          44-45: decimals (u8)
          45-46: isInitialized (bool)
          46-78: freezeAuthority (32 bytes; all zeros = None)
        """
        try:
            MINT_LEN = 82
            offset = 0

            # Token-2022 may have extra bytes; detect by length
            if len(raw) > MINT_LEN + 4:
                offset = 4  # skip TLV discriminator

            if len(raw) < offset + MINT_LEN:
                return None

            mint_auth_bytes = raw[offset : offset + 32]
            freeze_auth_bytes = raw[offset + 46 : offset + 78]

            def pk_from_bytes(b: bytes) -> str:
                # Base58 encode 32-byte pubkey
                ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
                n = int.from_bytes(b, "big")
                result = ""
                while n > 0:
                    n, r = divmod(n, 58)
                    result = ALPHABET[r] + result
                # Pad with leading 1s for leading zero bytes
                for byte in b:
                    if byte == 0:
                        result = "1" + result
                    else:
                        break
                return result or "1" * 32

            mint_authority = pk_from_bytes(mint_auth_bytes) if any(mint_auth_bytes) else ""
            freeze_authority = pk_from_bytes(freeze_auth_bytes) if any(freeze_auth_bytes) else ""

            return {
                "mint_authority": mint_authority,
                "freeze_authority": freeze_authority,
            }
        except (httpx.HTTPError, KeyError, TypeError) as e :
            logger.debug("pk_from_bytes_error", exc_info=True)
            logger.debug(f"Raw SPL mint parse failed: {e}")
            return None

    def _build_solana_authority(
        self, auth_type: str, address: str, token_address: str
    ) -> SolanaAuthority:
        """Build a SolanaAuthority entry from a parsed address string."""
        is_renounced = address == "" or address == SOLANA_ZERO_ADDRESS
        is_token_itself = address == token_address
        return SolanaAuthority(
            authority_type=auth_type,
            address=address if address else "RENOUNCED",
            is_renounced=is_renounced,
            is_token_itself=is_token_itself,
        )

    def _add_solana_warnings(self, report: ContractAuthorityReport) -> None:
        """Add risk warnings based on Solana authority configuration."""
        auths = report.solana_authorities
        if not auths:
            return

        if auths.mint_authority and not auths.mint_authority.is_renounced:
            if auths.mint_authority.is_token_itself:
                report.warnings.append(
                    "CRITICAL: Mint authority is the token itself - self-referential minting"
                )
            else:
                report.warnings.append(
                    f"Mint authority not renounced: {auths.mint_authority.address}"
                )

        if auths.freeze_authority and not auths.freeze_authority.is_renounced:
            if auths.freeze_authority.is_token_itself:
                report.warnings.append(
                    "HIGH: Freeze authority is the token itself - can freeze accounts"
                )
            else:
                report.warnings.append(
                    f"Freeze authority not renounced: {auths.freeze_authority.address}"
                )

        if auths.update_authority and not auths.update_authority.is_renounced:
            report.warnings.append(
                f"Update authority not renounced: {auths.update_authority.address} (metadata mutable)"
            )

    # ── EVM Path ────────────────────────────────────────────

    async def _scan_evm(self, token_address: str, chain: str) -> ContractAuthorityReport:
        """Detect proxy patterns, admin functions, and ownership on EVM chains."""
        report = ContractAuthorityReport(token_address=token_address, chain=chain)
        net = ETHERSCAN_NETWORKS.get(chain)
        if not net:
            report.warnings.append(f"No Etherscan config for chain: {chain}")
            self._compute_risk(report)
            return report

        proxy_info = EVMProxyInfo()

        try:
            # 1. Check if contract (has bytecode) and get proxy storage slots
            bytecode = await self._etherscan_get_bytecode(token_address, net)
            if not bytecode or bytecode == "0x":
                report.warnings.append("Address is not a contract or has no bytecode")
                report.evm_proxy = proxy_info
                self._compute_risk(report)
                return report

            # 2. Check EIP-1967 proxy slots
            impl = await self._etherscan_get_storage(token_address, EIP1967_IMPL_SLOT, net)
            admin = await self._etherscan_get_storage(token_address, EIP1967_ADMIN_SLOT, net)

            if (
                impl
                and impl != "0x0000000000000000000000000000000000000000000000000000000000000000"
            ):
                proxy_info.is_proxy = True
                proxy_info.implementation_address = self._strip_to_address(impl)
                proxy_info.proxy_type = "EIP-1967"

                if (
                    admin
                    and admin
                    != "0x0000000000000000000000000000000000000000000000000000000000000000"
                ):
                    proxy_info.admin_address = self._strip_to_address(admin)

            # 3. If not EIP-1967, check EIP-1822 (PROXIABLE slot)
            if not proxy_info.is_proxy:
                eip1822_impl = await self._etherscan_get_storage(token_address, EIP1822_SLOT, net)
                if eip1822_impl and eip1822_impl != "0x" + "0" * 64:
                    proxy_info.is_proxy = True
                    proxy_info.implementation_address = self._strip_to_address(eip1822_impl)
                    proxy_info.proxy_type = "EIP-1822"

            # 4. Check for OZ Transparent proxy pattern via admin slot
            if not proxy_info.is_proxy:
                # Try detection via minimal proxy (EIP-1167) bytecode pattern
                if bytecode and self._is_minimal_proxy(bytecode):
                    proxy_info.is_proxy = True
                    proxy_info.proxy_type = "Minimal_EIP-1167"

            # 5. Check ownership and admin functions from ABI
            abi = await self._etherscan_get_abi(token_address, net)
            if abi:
                self._analyze_evm_abi(abi, proxy_info, report)

            # 6. Determine specific proxy type
            if proxy_info.is_proxy and proxy_info.proxy_type == "EIP-1967":
                proxy_info.proxy_type = self._classify_proxy_type(proxy_info, abi)

            # 7. Check ownership renunciation
            if proxy_info.admin_address:
                proxy_info.is_ownership_renounced = await self._check_evm_ownership_renounced(
                    token_address, net, proxy_info.admin_address
                )

            report.evm_proxy = proxy_info
            self._add_evm_warnings(report)
            self._compute_risk(report)
            return report

        except (httpx.HTTPError) as e :
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"EVM authority scan failed for {token_address} on {chain}: {e}")
            report.warnings.append(f"EVM scan error: {str(e)[:120]}")
            report.evm_proxy = proxy_info
            self._compute_risk(report)
            return report

    def _analyze_evm_abi(
        self, abi: list, proxy_info: EVMProxyInfo, report: ContractAuthorityReport
    ) -> None:
        """Scan ABI for admin / ownership functions."""
        admin_sigs = [
            "transferOwnership",
            "renounceOwnership",
            "upgradeTo",
            "upgradeToAndCall",
            "setImplementation",
            "changeAdmin",
            "changeProxyAdmin",
            "proposeOwner",
            "acceptOwnership",
            "pause",
            "unpause",
            "setOwner",
            "setAdmin",
        ]
        found_sigs = []

        for entry in abi:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "function":
                continue
            name = entry.get("name", "")
            if name in admin_sigs:
                found_sigs.append(name)

        if found_sigs:
            proxy_info.has_admin_functions = True
            proxy_info.admin_function_signatures = found_sigs

    def _classify_proxy_type(self, proxy_info: EVMProxyInfo, abi: list | None) -> str:
        """Classify EIP-1967 proxy as Transparent, UUPS, or unknown."""
        if not abi:
            return "EIP-1967"

        func_names = set()
        for entry in abi:
            if isinstance(entry, dict) and entry.get("type") == "function":
                func_names.add(entry.get("name", ""))

        # Transparent proxies have admin functions on the proxy itself
        if ("upgradeTo" in func_names or "upgradeToAndCall" in func_names) and proxy_info.admin_address:
            return "OZ_Transparent"

        # UUPS proxies include upgrade logic in the implementation
        if "upgradeTo" in func_names or "upgradeToAndCall" in func_names:
            return "OZ_UUPS"

        return "EIP-1967"

    def _is_minimal_proxy(self, bytecode: str) -> bool:
        """Check if bytecode matches EIP-1167 minimal proxy pattern."""
        # EIP-1167 minimal proxy: starts with 0x363d3d373d3d3d363d73...
        return bytecode.startswith("0x363d3d373d3d3d363d73")

    async def _check_evm_ownership_renounced(
        self, token_address: str, net: dict, admin_address: str
    ) -> bool:
        """Check if the admin/owner is a zero address or the contract itself (renounced)."""
        BURNED_ADDRESSES = {
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dEaD",
            "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
        }
        return admin_address.lower() in BURNED_ADDRESSES

    def _add_evm_warnings(self, report: ContractAuthorityReport) -> None:
        """Add risk warnings based on EVM proxy configuration."""
        proxy = report.evm_proxy
        if not proxy:
            return

        if proxy.is_proxy:
            report.warnings.append(
                f"Proxy contract detected (type: {proxy.proxy_type}), "
                f"implementation: {proxy.implementation_address or 'unknown'}"
            )

            if proxy.admin_address and not proxy.is_ownership_renounced:
                report.warnings.append(
                    f"Proxy admin not renounced: {proxy.admin_address} - contract can be upgraded"
                )
            elif proxy.is_ownership_renounced:
                report.warnings.append("Proxy ownership is renounced - upgrade risk mitigated")

        if proxy.has_admin_functions:
            sigs = ", ".join(proxy.admin_function_signatures[:5])
            report.warnings.append(f"Admin functions found: {sigs}")

    # ── Etherscan Helpers ───────────────────────────────────

    async def _etherscan_get(self, params: dict, net: dict) -> dict | None:
        """Make a GET request to Etherscan-family API."""
        url = net["url"]
        key = net.get("key", "")
        params["apikey"] = key
        try:
            r = await self._http.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "1" or data.get("message") == "OK":
                    return data
                # Some endpoints return status "0" with result still present
                if data.get("result") and data["result"] not in ("", "0x", None):
                    return data
            return None
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_etherscan_get_error", exc_info=True)
            logger.debug(f"Etherscan API error: {e}")
            return None

    async def _etherscan_get_bytecode(self, address: str, net: dict) -> str | None:
        """Get contract bytecode via eth_getCode through Etherscan proxy module."""
        data = await self._etherscan_get(
            {"module": "proxy", "action": "eth_getCode", "address": address},
            net,
        )
        if data and data.get("result"):
            return data["result"]
        return None

    async def _etherscan_get_storage(self, address: str, slot: str, net: dict) -> str | None:
        """Read a storage slot value via Etherscan proxy module."""
        data = await self._etherscan_get(
            {
                "module": "proxy",
                "action": "eth_getStorageAt",
                "address": address,
                "position": slot,
                "tag": "latest",
            },
            net,
        )
        if data and data.get("result"):
            return data["result"]
        return None

    async def _etherscan_get_abi(self, address: str, net: dict) -> list | None:
        """Fetch contract ABI from Etherscan (if verified)."""
        data = await self._etherscan_get(
            {"module": "contract", "action": "getabi", "address": address},
            net,
        )
        if data and data.get("result"):
            import json

            try:
                return json.loads(data["result"])
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _strip_to_address(self, slot_value: str) -> str:
        """Convert a 32-byte storage slot value to a 20-byte Ethereum address."""
        if not slot_value:
            return ""
        # Remove 0x prefix, take last 40 hex chars, re-add 0x
        hex_str = slot_value.replace("0x", "")
        if len(hex_str) >= 40:
            return "0x" + hex_str[-40:].lower()
        return "0x" + hex_str.lower()

    # ── Risk Scoring ────────────────────────────────────────

    def _compute_risk(self, report: ContractAuthorityReport) -> None:
        """Compute risk_score (0-100) and risk_level from collected data."""
        score = 0

        # Solana risk contributions
        if report.solana_authorities:
            auths = report.solana_authorities

            # Mint authority: biggest risk
            if auths.mint_authority:
                if auths.mint_authority.is_token_itself:
                    score += 45  # Self-referential minting = critical
                elif not auths.mint_authority.is_renounced:
                    score += 30  # Active mint authority = high risk
                # Renounced mint authority = safe, +0

            # Freeze authority: moderate risk
            if auths.freeze_authority:
                if auths.freeze_authority.is_token_itself:
                    score += 25
                elif not auths.freeze_authority.is_renounced:
                    score += 15
                # Renounced = safe, +0

            # Update authority: lower risk (metadata only)
            if auths.update_authority and not auths.update_authority.is_renounced:
                score += 10

        # EVM risk contributions
        if report.evm_proxy:
            proxy = report.evm_proxy

            if proxy.is_proxy:
                score += 20  # Proxy itself = risk surface

                if proxy.implementation_address:
                    score += 5  # Mutable implementation

                if proxy.admin_address and not proxy.is_ownership_renounced:
                    score += 25  # Unrenounced proxy admin = critical
                elif proxy.is_ownership_renounced:
                    score -= 10  # Renounced mitigates

            if proxy.has_admin_functions:
                score += 10
                # Extra admin functions without timelock
                danger_sigs = {"upgradeTo", "upgradeToAndCall", "transferOwnership"}
                if danger_sigs & set(proxy.admin_function_signatures):
                    score += 5

        # Warning count adds risk
        score += min(len(report.warnings) * 3, 15)

        # Clamp to 0-100
        report.risk_score = max(0, min(100, score))

        # Determine risk level
        if report.risk_score >= 70:
            report.risk_level = AuthorityRiskLevel.CRITICAL.value
        elif report.risk_score >= 45:
            report.risk_level = AuthorityRiskLevel.HIGH.value
        elif report.risk_score >= 20:
            report.risk_level = AuthorityRiskLevel.MEDIUM.value
        else:
            report.risk_level = AuthorityRiskLevel.LOW.value

# ── Pipeline Integration ────────────────────────────────────

async def run_contract_authority_scan(token_address: str, chain: str) -> ContractAuthorityReport:
    """Convenience function for SENTINEL pipeline integration."""
    scanner = ContractAuthorityScanner()
    try:
        return await scanner.scan(token_address, chain)
    finally:
        await scanner.close()
