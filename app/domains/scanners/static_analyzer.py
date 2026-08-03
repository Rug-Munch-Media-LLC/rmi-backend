"""
SENTINEL - Static Analyzer (Slither + Forta)
=============================================
Runs Slither static analysis on EVM contracts and queries Forta public alerts
to detect vulnerabilities, bad patterns, and known exploit indicators.

For EVM tokens:
  - If contract source is verified on Etherscan, fetch source and run slither
  - If source not available, fall back to Heimdall decompilation then run slither
  - Query Forta public alerts API for any bot alerts on the address
  - Try SmartCheck API if accessible

For Solana / non-Solidity: return gracefully with low-risk defaults.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_registry import CHAINS, is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

logger = get_logger("static_analyzer")

# ── Paths ────────────────────────────────────────────────────────────

SLITHER_BIN = os.getenv("SLITHER_BIN", "/root/.local/bin/slither")
HEIMDALL_BIN = os.getenv("HEIMDALL_BIN", "/root/.bifrost/bin/heimdall")

# ── Dangerous Slither detectors (most relevant for rug pulls) ────────

RUG_RELEVANT_DETECTORS = [
    "backdoor",
    "suicidal",
    "unrestricted-state-write",
    "reentrancy-eth",
    "reentrancy-no-eth",
    "reentrancy-unlimited-gas",
    "arbitrary-send-eth",
    "controlled-delegatecall",
    "delegatecall-loop",
    "unchecked-lowlevel",
    "unchecked-return",
    "assembly",
    "write-after-write",
    "shadowing-state",
    "shadows-own-state-variable",
    "name-reused",
    "public-mappings-nested",
    "boolean-cst",
    "incorrect-erc20-interface",
    "incorrect-erc721-interface",
    "missing-zero-validation",
    "uninitialized-local",
    "uninitialized-state",
    "call-forward-to-proxy",
    "erc20-interface-violation",
    "storage-layout",
    "external-function",
]

# ── Known rug-pull function names (checked in findings) ───────────────

DANGEROUS_FUNCTION_PATTERNS = [
    "withdrawAll",
    "drain",
    "setOwner",
    "emergencyWithdraw",
    "mint",
    "rug",
    "pullFunds",
    "takeFunds",
    "sweepFunds",
    "rescueTokens",
    "takeOwnership",
    "forceWithdraw",
    "dump",
    "manualWithdraw",
    "adminWithdraw",
    "ownerWithdraw",
]

# ── Report dataclass ─────────────────────────────────────────────────


@dataclass
class StaticAnalysisReport:
    """Result of static analysis (Slither + Forta + SmartCheck)."""

    token_address: str
    chain: str

    # Slither findings
    slither_available: bool = False
    slither_findings: list[dict[str, Any]] = field(default_factory=list)
    slither_rug_relevant: list[dict[str, Any]] = field(default_factory=list)
    dangerous_functions_found: list[str] = field(default_factory=list)
    source_verified: bool = False
    used_heimdall_fallback: bool = False

    # Forta alerts
    forta_alerts: list[dict[str, Any]] = field(default_factory=list)
    forta_alert_count: int = 0

    # SmartCheck
    smartcheck_findings: list[dict[str, Any]] = field(default_factory=list)

    # Risk
    risk_score: float = 0.0
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Analyzer class ────────────────────────────────────────────────────


class StaticAnalyzer:
    """Run Slither static analysis + Forta alerts + SmartCheck for EVM contracts."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=20.0)
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._slither_available = shutil.which(SLITHER_BIN) is not None
        self._heimdall_available = shutil.which(HEIMDALL_BIN) is not None

    async def _fetch_contract_source(self, address: str, chain: str) -> dict | None:
        """Fetch verified contract source from Etherscan-style explorer."""
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
                    entry = result[0]
                    source_code = entry.get("SourceCode", "")
                    if source_code and source_code not in ("", "0x", None):
                        return entry
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_contract_source_error", exc_info=True)
            logger.warning(f"Etherscan source fetch failed for {address}: {e}")
        return None

    async def _fetch_rpc_url(self, chain: str) -> str:
        """Get a working RPC URL for the chain."""
        chain_cfg = CHAINS.get(chain)
        if chain_cfg and chain_cfg.rpc_endpoints:
            return chain_cfg.rpc_endpoints[0]
        return "https://eth.llamarpc.com"

    async def _heimdall_decompile(self, address: str, chain: str) -> str | None:
        """Run Heimdall-rs decompiler to get Solidity output for unverified contracts.

        Returns path to the decompiled .sol file, or None on failure.
        """
        if not self._heimdall_available:
            return None

        rpc_url = await self._fetch_rpc_url(chain)
        tmp_dir = tempfile.mkdtemp(prefix="heimdall_")

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
            logger.info(f"Running Heimdall decompile: {address} on {chain}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode != 0:
                logger.warning(f"Heimdall decompile failed for {address}: {stderr.decode()[:300]}")
                return None

            # Find the decompiled .sol file in the output directory
            for fname in os.listdir(tmp_dir):
                if fname.endswith(".sol"):
                    return os.path.join(tmp_dir, fname)

            # Heimdall may output as .abi + .sol in a subdirectory
            for root, _dirs, files in os.walk(tmp_dir):
                for fname in files:
                    if fname.endswith(".sol"):
                        return os.path.join(root, fname)

            logger.warning(f"No .sol output from Heimdall for {address}")
            return None

        except TimeoutError:
            logger.warning(f"Heimdall decompile timed out for {address}")
            return None
        except httpx.HTTPError as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Heimdall decompile error for {address}: {e}")
            return None

    def _run_slither_sync(self, sol_path: str) -> list[dict[str, Any]]:
        """Run Slither synchronously on a .sol file. Returns list of finding dicts."""
        if not self._slither_available:
            return []

        try:
            cmd = [
                SLITHER_BIN,
                sol_path,
                "--json",
                "-",
                "--disable-color",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Slither outputs JSON to stdout when --json - is used
            output = result.stdout
            if not output:
                # Try stderr (sometimes Slither writes JSON there on failure)
                output = result.stderr

            if not output:
                return []

            # Slither JSON format: { "results": { "detectors": [...] } }
            data = json.loads(output)
            detectors = data.get("results", {}).get("detectors", [])
            findings = []
            for det in detectors:
                finding = {
                    "check": det.get("check", ""),
                    "impact": det.get("impact", ""),
                    "confidence": det.get("confidence", ""),
                    "description": det.get("first_markdown_element", det.get("description", "")),
                    "elements": det.get("elements", []),
                }
                findings.append(finding)
            return findings

        except subprocess.TimeoutExpired:
            logger.warning(f"Slither timed out on {sol_path}")
            return []
        except json.JSONDecodeError:
            logger.warning(f"Slither output not valid JSON for {sol_path}")
            return []
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Slither failed on {sol_path}: {e}")
            return []

    async def _run_slither(self, sol_path: str) -> list[dict[str, Any]]:
        """Run Slither on a .sol file asynchronously (wraps sync call)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_slither_sync, sol_path)

    async def _fetch_forta_alerts(self, address: str) -> list[dict[str, Any]]:
        """Query Forta public alerts API for alerts on this address."""
        alerts = []
        try:
            url = f"https://api.forta.network/alerts?filter={address}"
            resp = await self._http.get(url, timeout=15.0)
            if resp.status_code == 200:
                body = resp.json()
                for alert in body.get("alerts", body.get("results", [])):
                    alerts.append(
                        {
                            "id": alert.get("id", ""),
                            "bot_id": alert.get("botId", alert.get("source", {}).get("botId", "")),
                            "alert_name": alert.get("alertId", alert.get("name", "")),
                            "severity": alert.get("severity", ""),
                            "description": alert.get("description", ""),
                            "timestamp": alert.get("timestamp", ""),
                        }
                    )
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_forta_alerts_error", exc_info=True)
            logger.debug(f"Forta alerts query failed for {address}: {e}")
        return alerts

    async def _fetch_smartcheck(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Try SmartCheck API for additional static analysis findings."""
        chain_cfg = CHAINS.get(chain)
        key = chain_cfg.get_explorer_api_key() if chain_cfg else ""
        if not key:
            return []

        findings = []
        try:
            # SmartCheck cloud API (if available)
            url = "https://smartcheck.mythril.ai/check"
            # Need source code - skip if we can't get it
            source_data = await self._fetch_contract_source(address, chain)
            if not source_data:
                return []

            source_code = source_data.get("SourceCode", "")
            if not source_code:
                return []

            resp = await self._http.post(
                url,
                json={"source": source_code},
                timeout=30.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                for issue in body.get("issues", body.get("findings", [])):
                    findings.append(
                        {
                            "swc_id": issue.get("swc-id", issue.get("swc_id", "")),
                            "severity": issue.get("severity", ""),
                            "description": issue.get("description", ""),
                        }
                    )
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_smartcheck_error", exc_info=True)
            logger.debug(f"SmartCheck query failed for {address}: {e}")
        return findings

    def _check_dangerous_functions(self, findings: list[dict]) -> list[str]:
        """Check Slither findings for dangerous function names."""
        found = set()
        for f in findings:
            desc = f.get("description", "").lower()
            elements = f.get("elements", [])
            for el in elements:
                name = el.get("name", "").lower()
                for pattern in DANGEROUS_FUNCTION_PATTERNS:
                    if pattern.lower() in name or pattern.lower() in desc:
                        found.add(pattern)
        return sorted(found)

    def _compute_risk(
        self,
        report: StaticAnalysisReport,
    ) -> None:
        """Compute risk score and level from findings."""
        score = 0.0
        warnings = []
        red_flags = []

        # Slither findings contribute to risk
        rug_relevant = []
        for finding in report.slither_findings:
            check = finding.get("check", "").lower()
            impact = finding.get("impact", "").lower()

            # Check if this detector is rug-relevant
            is_rug_relevant = any(d in check for d in RUG_RELEVANT_DETECTORS)

            if is_rug_relevant:
                rug_relevant.append(finding)
                if impact == "high":
                    score += 15
                elif impact == "medium":
                    score += 8
                else:
                    score += 3
            else:
                # Non-rug-relevant findings still add some risk
                if impact == "high":
                    score += 5
                elif impact == "medium":
                    score += 2

        report.slither_rug_relevant = rug_relevant

        # Dangerous functions found
        for fn in report.dangerous_functions_found:
            fn_lower = fn.lower()
            if fn_lower in ("drain", "rug", "withdrawall"):
                score += 20
                red_flags.append(f"CRITICAL: Dangerous function '{fn}' found in contract")
            elif fn_lower in ("emergencywithdraw", "setowner", "takeownership"):
                score += 12
                red_flags.append(f"HIGH: Suspicious function '{fn}' found")
            elif fn_lower in ("mint", "rescuetokens", "sweepfunds"):
                score += 8
                warnings.append(f"MEDIUM: Function '{fn}' found - could be misused")
            else:
                score += 4

        # Heimdall fallback means source wasn't verified - more opaque
        if report.used_heimdall_fallback:
            score += 10
            warnings.append("MEDIUM: Contract source unverified - analyzed via decompilation")

        # Forta alerts
        for alert in report.forta_alerts:
            severity = alert.get("severity", "").lower()
            if severity in ("high", "critical"):
                score += 15
                red_flags.append(f"FORTA {severity.upper()}: {alert.get('alert_name', '')}")
            elif severity == "medium":
                score += 8
                warnings.append(f"FORTA MEDIUM: {alert.get('alert_name', '')}")
            elif severity == "low":
                score += 3

        # SmartCheck findings
        for sc_finding in report.smartcheck_findings:
            severity = sc_finding.get("severity", "").lower()
            if severity in ("high", "critical"):
                score += 10
            elif severity == "medium":
                score += 5

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

    async def analyze(self, token_address: str, chain: str) -> StaticAnalysisReport:
        """Full static analysis: Slither + Forta + SmartCheck.

        For EVM: fetches source (or decompiles with Heimdall), runs Slither,
        queries Forta alerts, and optionally SmartCheck.
        For Solana: provides limited analysis via Forta only.
        """
        report = StaticAnalysisReport(
            token_address=token_address,
            chain=chain,
            slither_available=self._slither_available,
        )

        if is_evm(chain):
            # 1. Try to fetch verified source code
            source_data = await self._fetch_contract_source(token_address, chain)
            sol_path = None

            if source_data:
                report.source_verified = True
                # Write source to temp file for Slither
                tmp_dir = tempfile.mkdtemp(prefix="slither_")
                source_code = source_data.get("SourceCode", "")
                contract_name = source_data.get("ContractName", "Contract")

                # Handle flattened vs multi-file source
                if source_code.startswith("{{") and source_code.endswith("}}"):
                    # Multi-file format: {{ "file1.sol": "...", "file2.sol": "..." }}
                    try:
                        files_dict = json.loads(source_code[1:-1])
                        for fname, content in files_dict.items():
                            fpath = os.path.join(tmp_dir, fname)
                            os.makedirs(os.path.dirname(fpath), exist_ok=True)
                            with open(fpath, "w") as f:
                                f.write(content)
                        sol_path = os.path.join(tmp_dir, contract_name + ".sol")
                        if not os.path.exists(sol_path):
                            # Use the first file
                            sol_path = os.path.join(tmp_dir, next(iter(files_dict.keys())))
                    except json.JSONDecodeError:
                        # Treat as single file
                        sol_path = os.path.join(tmp_dir, contract_name + ".sol")
                        with open(sol_path, "w") as f:
                            f.write(source_code)
                else:
                    sol_path = os.path.join(tmp_dir, contract_name + ".sol")
                    with open(sol_path, "w") as f:
                        f.write(source_code)

            else:
                # 2. Source not verified - try Heimdall decompilation
                if self._heimdall_available:
                    report.used_heimdall_fallback = True
                    sol_path = await self._heimdall_decompile(token_address, chain)

            # 3. Run Slither if we have a .sol file
            if sol_path and os.path.exists(sol_path):
                findings = await self._run_slither(sol_path)
                report.slither_findings = findings
                report.dangerous_functions_found = self._check_dangerous_functions(findings)

                # Cleanup temp dir
                try:
                    tmp_parent = os.path.dirname(sol_path)
                    if tmp_parent.startswith("/tmp/"):
                        shutil.rmtree(tmp_parent, ignore_errors=True)
                except (ConnectionError, OSError, TimeoutError):
                    pass

            elif not report.used_heimdall_fallback:
                warnings_msg = "Contract source not available and Heimdall not installed"
                report.warnings.append(warnings_msg)

        elif is_solana(chain):
            # Solana: no Slither/Heimdall - just run Forta + lightweight checks
            report.slither_available = False
            report.warnings.append("Slither static analysis not applicable for Solana")

        # 4. Query Forta alerts (all chains)
        forta_alerts = await self._fetch_forta_alerts(token_address)
        report.forta_alerts = forta_alerts
        report.forta_alert_count = len(forta_alerts)

        # 5. Try SmartCheck (EVM only)
        if is_evm(chain):
            smartcheck = await self._fetch_smartcheck(token_address, chain)
            report.smartcheck_findings = smartcheck

        # 6. Compute risk
        self._compute_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"smart contract vulnerability static analysis {chain}",
                chain=chain,
                address=token_address,
                scanner_type="static_analysis",
            )
            report.citations = rag_cits
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except (ConnectionError, OSError, TimeoutError):
            pass

        return report
