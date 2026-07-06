"""
SENTINEL - Decompiler Analyzer (Heimdall + whatsABI-style)
==========================================================
Analyzes unverified contracts using Heimdall-rs decompilation and
whatsABI-style function selector extraction from bytecode.

For EVM unverified contracts:
  - Use Heimdall to decompile bytecode
  - Extract function selectors from bytecode (whatsABI approach)
  - Parse decompiled output for dangerous function patterns
  - Compare function signatures against known rug-pull database

For Solana:
  - Check program ID for known vulnerabilities
  - Attempt Anchor IDL extraction
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.chain_registry import CHAINS, is_evm, is_solana
from app.scanners.rag_citations import build_citation_string, query_rag_citations

logger = logging.getLogger("decompiler_analyzer")

# ── Paths ────────────────────────────────────────────────────────────

HEIMDALL_BIN = os.getenv("HEIMDALL_BIN", "/root/.bifrost/bin/heimdall")

# ── Known rug-pull function signatures (4-byte selectors) ───────────
# These are keccak256 hashes of common rug-pull function signatures

KNOWN_RUG_SELECTORS: dict[str, str] = {
    "0x2e1a7d4d": "withdraw(uint256)",
    "0x3ccfd60b": "withdraw()",
    "0x51cff8d9": "withdrawFunds(address,uint256)",
    "0xdb006a75": "setFeeRate(uint256)",
    "0x4b2e2f5b": "setTaxRate(uint256)",
    "0xf3fef3a3": "drain()",
    "0x5a3f0b2c": "rescueTokens(address,uint256)",
    "0x2f2ff15d": "setOwner(address)",
    "0x6d1b2296": "emergencyWithdraw()",
    "0x3f4ba83a": "emergencyWithdraw(uint256)",
    "0xa9059cbb": "transfer(address,uint256)",
    "0x40c10f19": "mint(address,uint256)",
    "0x0018e4f6": "mintTo(address,uint256)",
    "0xa0712d68": "mint(uint256)",
    "0xe2e7d1bd": "setMaxTxAmount(uint256)",
    "0xbf3eb6e0": "setMaxWalletAmount(uint256)",
    "0x42966c68": "burn(uint256)",
    "0xf2fde38b": "transferOwnership(address)",
    "0x715018a6": "renounceOwnership()",
    "0x8da5cb5b": "owner()",
    "0xe0e1896a": "setSwapAndLiquifyEnabled(bool)",
    "0xe9fadbee": "setTradingEnabled(bool)",
    "0xc8b76844": "setCooldownEnabled(bool)",
    "0xfe575a87": "setMaxBuyAmount(uint256)",
    "0xffeb8d4b": "setBlacklistEnabled(bool)",
    "0xec3cc297": "setExcludedFromFee(address,bool)",
    "0x4a3fcb64": "setBot(address,bool)",
    "0x3a4a5dcd": "blacklistAddress(address,bool)",
    "0xe0a8ea8c": "setFees(uint256,uint256,uint256)",
    "0x608080cd": "setFees(uint256,uint256)",
    "0xf4824f1d": "pullFunds()",
    "0x9e2814a4": "takeFunds(address,uint256)",
    "0x49f02cdc": "sweepFunds(address)",
    "0xba62ef1f": "manualWithdraw()",
    "0xb6b55f25": "deposit(uint256)",
    "0xd0e30db0": "deposit()",
}

# ── Dangerous function name patterns (checked in decompiled source) ──

DANGEROUS_NAME_PATTERNS = [
    (r"withdrawAll", "withdrawAll - can drain contract balance"),
    (r"drain\b", "drain - rug-pull indicator"),
    (r"setOwner\b", "setOwner - ownership manipulation"),
    (r"emergencyWithdraw", "emergencyWithdraw - can bypass locks"),
    (r"\bmint\b", "mint - unlimited supply risk"),
    (r"\brug\b", "rug - explicit rug function"),
    (r"pullFunds", "pullFunds - can extract funds"),
    (r"takeFunds", "takeFunds - can extract funds"),
    (r"sweepFunds", "sweepFunds - can sweep balances"),
    (r"rescueTokens", "rescueTokens - can steal tokens"),
    (r"takeOwnership", "takeOwnership - ownership takeover"),
    (r"forceWithdraw", "forceWithdraw - bypasses withdrawal limits"),
    (r"\bdump\b", "dump - sell-off function"),
    (r"manualWithdraw", "manualWithdraw - admin-controlled withdrawal"),
    (r"adminWithdraw", "adminWithdraw - admin-controlled withdrawal"),
    (r"ownerWithdraw", "ownerWithdraw - owner-controlled withdrawal"),
    (r"setSwapAndLiquify", "setSwapAndLiquify - can disable trading"),
    (r"setTradingEnabled", "setTradingEnabled - can freeze trading"),
    (r"setCooldownEnabled", "setCooldownEnabled - can trap holders"),
    (r"setBot\b", "setBot - can blacklist addresses"),
    (r"blacklist", "blacklist - can freeze individual wallets"),
    (r"setExcludedFromFee", "setExcludedFromFee - fee evasion for insiders"),
    (r"setMaxBuyAmount", "setMaxBuyAmount - can restrict buying"),
    (r"setMaxTxAmount", "setMaxTxAmount - can restrict transactions"),
    (r"setMaxWalletAmount", "setMaxWalletAmount - can restrict holdings"),
    (r"setFees\b", "setFees - can change fee to 100%"),
    (r"setFeeRate", "setFeeRate - can change fee rate"),
    (r"setTaxRate", "setTaxRate - can change tax rate"),
]

# ── Anchor IDL known patterns for Solana ─────────────────────────────

ANCHOR_DANGEROUS_IX_NAMES = [
    "withdraw_all",
    "drain",
    "set_authority",
    "emergency_withdraw",
    "mint_to",
    "rug",
    "pull_funds",
    "take_funds",
    "sweep_funds",
    "force_withdraw",
    "set_owner",
    "transfer_authority",
    "close_account",
    "close_vault",
    "set_fees",
    "set_tax_rate",
    "set_blacklist",
    "set_trading_enabled",
    "freeze_account",
]


# ── Report dataclass ─────────────────────────────────────────────────


@dataclass
class DecompilerReport:
    """Result of decompiler-based analysis."""

    token_address: str
    chain: str

    # Source verification
    is_verified: bool = False
    heimdall_ran: bool = False
    heimdall_output_path: str = ""

    # Extracted data
    function_selectors: list[str] = field(default_factory=list)
    function_signatures: dict[str, str] = field(default_factory=list)
    decompiled_functions: list[str] = field(default_factory=list)
    decompiled_source: str = ""

    # Risk findings
    dangerous_functions: list[str] = field(default_factory=list)
    known_rug_matches: list[str] = field(default_factory=list)
    solana_anchor_risks: list[str] = field(default_factory=list)

    # Risk
    risk_score: float = 0.0
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── 4-byte selector extraction (whatsABI approach) ───────────────────


def _extract_selectors_from_bytecode(bytecode: str) -> list[str]:
    """Extract function selectors from EVM bytecode using PUSH4 + EQ/DIV pattern.

    This replicates the core logic of whatsabi: find PUSH4 instructions
    followed by comparison operations, which indicate function dispatch.
    """
    if not bytecode or not bytecode.startswith("0x"):
        return []

    code = bytecode[2:]  # strip 0x
    selectors = set()

    i = 0
    while i < len(code) - 8:
        # PUSH4 = 0x63
        if code[i : i + 2] == "63":
            selector = "0x" + code[i + 2 : i + 10]
            # Verify it looks like a real selector (not random data)
            # Check if followed by EQ (14), or used in JUMPI context
            if i + 10 < len(code):
                next_byte = code[i + 10 : i + 12]
                if next_byte in ("14", "80", "90"):  # EQ, DUP1, SWAP1
                    # Skip common non-function selectors
                    if selector not in ("0x00000000", "0xffffffff"):
                        selectors.add(selector.lower())
            i += 10
        else:
            i += 2

    return sorted(selectors)


def _match_selectors_to_known(
    selectors: list[str],
) -> tuple[dict[str, str], list[str]]:
    """Match extracted selectors against known rug-pull function database.

    Returns: (signature_map, known_rug_matches)
    """
    sig_map = {}
    rug_matches = []

    for sel in selectors:
        if sel in KNOWN_RUG_SELECTORS:
            sig = KNOWN_RUG_SELECTORS[sel]
            sig_map[sel] = sig
            # Flag truly dangerous ones
            dangerous_sigs = [
                "withdraw(uint256)",
                "withdraw()",
                "withdrawFunds(address,uint256)",
                "drain()",
                "emergencyWithdraw()",
                "emergencyWithdraw(uint256)",
                "setOwner(address)",
                "mint(address,uint256)",
                "mint(uint256)",
                "mintTo(address,uint256)",
                "pullFunds()",
                "takeFunds(address,uint256)",
                "sweepFunds(address)",
                "setFees(uint256,uint256,uint256)",
                "setFees(uint256,uint256)",
                "blacklistAddress(address,bool)",
                "setBot(address,bool)",
                "setTradingEnabled(bool)",
                "setSwapAndLiquifyEnabled(bool)",
            ]
            if sig in dangerous_sigs:
                rug_matches.append(f"{sel} → {sig}")

    return sig_map, rug_matches


def _scan_decompiled_source(source: str) -> list[str]:
    """Scan decompiled source text for dangerous function patterns."""
    found = []
    for pattern, description in DANGEROUS_NAME_PATTERNS:
        if re.search(pattern, source, re.IGNORECASE):
            found.append(description)
    return found


# ── Analyzer class ────────────────────────────────────────────────────


class DecompilerAnalyzer:
    """Decompile and analyze unverified contracts for dangerous patterns."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=20.0)
        self._chain = ChainClient()
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._heimdall_available = shutil.which(HEIMDALL_BIN) is not None

    async def _get_bytecode(self, address: str, chain: str) -> str | None:
        """Get contract bytecode via RPC eth_getCode."""
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
                result = body.get("result")
                if result and result != "0x" and result != "0x0":
                    return result
        except Exception as e:
            logger.warning(f"eth_getCode failed for {address}: {e}")
        return None

    async def _check_verified(self, address: str, chain: str) -> bool:
        """Check if contract source is verified on explorer."""
        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return False
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return False
        try:
            url = f"{chain_cfg.explorer_api_url}?module=contract&action=getsourcecode&address={address}&apikey={key}"
            resp = await self._http.get(url)
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("result", [])
                if result and len(result) > 0:
                    source = result[0].get("SourceCode", "")
                    return bool(source and source not in ("", "0x", None))
        except Exception as e:
            logger.debug(f"Verified check failed for {address}: {e}")
        return False

    async def _heimdall_decompile(self, address: str, chain: str) -> tuple[bool, str, str]:
        """Run Heimdall decompiler on contract bytecode.

        Returns: (success, decompiled_source, output_path)
        """
        if not self._heimdall_available:
            return False, "", ""

        chain_cfg = CHAINS.get(chain)
        rpc_url = chain_cfg.rpc_endpoints[0] if chain_cfg and chain_cfg.rpc_endpoints else "https://eth.llamarpc.com"

        tmp_dir = tempfile.mkdtemp(prefix="heimdall_dec_")

        try:
            cmd = [
                HEIMDALL_BIN,
                "decompile",
                "--address",
                address,
                "--rpc-url",
                rpc_url,
                "--output",
                tmp_dir,
            ]
            logger.info(f"Running Heimdall decompile for {address} on {chain}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                logger.warning(f"Heimdall decompile failed for {address}: {stderr.decode()[:300]}")
                return False, "", tmp_dir

            # Find output files
            decompiled_source = ""
            output_path = ""

            for root, _dirs, files in os.walk(tmp_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if fname.endswith(".sol"):
                        with open(fpath, errors="replace") as f:
                            content = f.read()
                        if content:
                            decompiled_source = content
                            output_path = fpath
                    elif fname.endswith(".abi"):
                        with open(fpath, errors="replace") as f:
                            try:
                                abi_data = json.load(f)
                                # Extract function names from ABI
                                for item in abi_data:
                                    if item.get("type") == "function":
                                        name = item.get("name", "")
                                        if name:
                                            # We'll add these to decompiled_functions later
                                            pass
                            except json.JSONDecodeError:
                                pass

            if decompiled_source:
                return True, decompiled_source, output_path

            return False, "", tmp_dir

        except TimeoutError:
            logger.warning(f"Heimdall decompile timed out for {address}")
            return False, "", tmp_dir
        except Exception as e:
            logger.warning(f"Heimdall decompile error for {address}: {e}")
            return False, "", tmp_dir

    async def _extract_anchor_idl(self, program_id: str) -> dict | None:
        """Try to extract Anchor IDL from a Solana program.

        Anchor stores the IDL in the program's account data.
        """
        if not self._chain:
            return None

        try:
            # Anchor IDL is stored at a deterministic PDA
            # Try Helius DAS API first
            helius_key = os.getenv("HELIUS_API_KEY", "")
            if helius_key:
                url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
                resp = await self._http.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getProgramAccounts",
                        "params": [
                            program_id,
                            {"encoding": "base64", "filters": [{"dataSize": 100}]},
                        ],
                    },
                )
                if resp.status_code == 200:
                    body = resp.json()
                    accounts = body.get("result", [])
                    for acc in accounts:
                        # Look for IDL marker in account data
                        data = acc.get("account", {}).get("data", [""])[0]
                        if data and "anchor" in data[:100].lower():
                            return {"program_id": program_id, "has_idl": True}

            # Try direct method: anchor IDL account
            result = await self._chain.rpc_call(
                "getAccountInfo",
                [program_id, {"encoding": "jsonParsed"}],
            )
            if result and "result" in result:
                return result["result"]
        except Exception as e:
            logger.debug(f"Anchor IDL extraction failed for {program_id}: {e}")

        return None

    def _check_anchor_risks(self, idl_data: dict | None) -> list[str]:
        """Check Anchor IDL for dangerous instruction names."""
        risks = []
        if not idl_data:
            return risks

        # Try to find instructions in IDL
        instructions = idl_data.get("instructions", [])
        for ix in instructions:
            name = ix.get("name", "").lower()
            for dangerous in ANCHOR_DANGEROUS_IX_NAMES:
                if dangerous in name:
                    risks.append(f"Anchor IX '{ix.get('name')}' matches dangerous pattern '{dangerous}'")

        return risks

    def _compute_risk(self, report: DecompilerReport) -> None:
        """Compute risk score and level from decompiler findings."""
        score = 0.0
        warnings = []
        red_flags = []

        # Unverified contract is already somewhat suspicious
        if not report.is_verified:
            score += 15
            warnings.append("MEDIUM: Contract source is NOT verified - analysis via decompilation")

        # Known rug function selector matches
        for match in report.known_rug_matches:
            if any(d in match.lower() for d in ("drain", "rug", "withdrawall", "pullfunds")):
                score += 20
                red_flags.append(f"CRITICAL: Known rug selector found: {match}")
            elif any(d in match.lower() for d in ("emergencywithdraw", "setowner", "setfees", "blacklist")):
                score += 12
                red_flags.append(f"HIGH: Suspicious selector found: {match}")
            elif any(d in match.lower() for d in ("mint", "settrading", "setswap")):
                score += 8
                warnings.append(f"MEDIUM: Risky selector found: {match}")
            else:
                score += 4

        # Dangerous function names from decompiled source
        for fn_desc in report.dangerous_functions:
            if "drain" in fn_desc or "rug" in fn_desc or "withdrawAll" in fn_desc:
                score += 18
                red_flags.append(f"CRITICAL: {fn_desc}")
            elif "emergencyWithdraw" in fn_desc or "setOwner" in fn_desc:
                score += 12
                red_flags.append(f"HIGH: {fn_desc}")
            elif "mint" in fn_desc or "blacklist" in fn_desc:
                score += 8
                warnings.append(f"MEDIUM: {fn_desc}")
            else:
                score += 4

        # Solana Anchor risks
        for risk in report.solana_anchor_risks:
            score += 10
            warnings.append(f"HIGH: {risk}")

        score = min(100.0, score)
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

    # ── Main entry point ────────────────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> DecompilerReport:
        """Decompile and analyze contract for dangerous patterns.

        For EVM: extracts selectors from bytecode, optionally decompiles
        with Heimdall, scans for dangerous functions, and matches against
        known rug selector database.
        For Solana: checks Anchor IDL for dangerous instructions.
        """
        report = DecompilerReport(
            token_address=token_address,
            chain=chain,
        )

        if is_evm(chain):
            # 1. Check if contract source is verified
            report.is_verified = await self._check_verified(token_address, chain)

            # 2. Get bytecode and extract function selectors
            bytecode = await self._get_bytecode(token_address, chain)
            if bytecode:
                selectors = _extract_selectors_from_bytecode(bytecode)
                report.function_selectors = selectors

                # Match against known database
                sig_map, rug_matches = _match_selectors_to_known(selectors)
                report.function_signatures = sig_map
                report.known_rug_matches = rug_matches

            # 3. Decompile with Heimdall if not verified
            if not report.is_verified and self._heimdall_available:
                success, source, output_path = await self._heimdall_decompile(token_address, chain)
                report.heimdall_ran = success
                report.heimdall_output_path = output_path

                if success and source:
                    report.decompiled_source = source[:5000]  # Truncate for report

                    # Scan decompiled source for dangerous function names
                    report.dangerous_functions = _scan_decompiled_source(source)

                    # Extract function names from decompiled source
                    func_pattern = re.compile(r"function\s+(\w+)\s*\(", re.IGNORECASE)
                    report.decompiled_functions = sorted(set(func_pattern.findall(source)))

            elif report.is_verified:
                # Even verified contracts should be checked for dangerous functions
                # Fetch source and scan
                chain_cfg = CHAINS.get(chain)
                key = chain_cfg.get_explorer_api_key() if chain_cfg else self._etherscan_key
                if key and chain_cfg:
                    try:
                        url = (
                            f"{chain_cfg.explorer_api_url}"
                            f"?module=contract&action=getsourcecode&address={token_address}"
                            f"&apikey={key}"
                        )
                        resp = await self._http.get(url)
                        if resp.status_code == 200:
                            body = resp.json()
                            result = body.get("result", [])
                            if result and len(result) > 0:
                                source_code = result[0].get("SourceCode", "")
                                if source_code:
                                    report.dangerous_functions = _scan_decompiled_source(source_code)
                                    func_pattern = re.compile(r"function\s+(\w+)\s*\(", re.IGNORECASE)
                                    report.decompiled_functions = sorted(set(func_pattern.findall(source_code)))
                    except Exception as e:
                        logger.debug(f"Source scan failed for {token_address}: {e}")

        elif is_solana(chain):
            # Solana: try Anchor IDL extraction
            report.is_verified = True  # Solana programs are always "on-chain"

            idl_data = await self._extract_anchor_idl(token_address)
            if idl_data:
                report.solana_anchor_risks = self._check_anchor_risks(idl_data)
            else:
                report.warnings.append("Could not extract Anchor IDL - limited analysis for Solana program")

        # 4. Compute risk
        self._compute_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"decompiled contract dangerous function rug pull {chain}",
                chain=chain,
                address=token_address,
                scanner_type="decompiler",
            )
            report.citations = rag_cits
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except Exception:
            pass

        return report
