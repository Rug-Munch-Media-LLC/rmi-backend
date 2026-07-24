"""
SENTINEL - Governance Attack Detector
======================================
Detects governance concentration risk and potential attack vectors:

  - Fetch token holder distribution (reuse holder_analyzer patterns)
  - Check if top holder has >50% of supply
  - Check if contract has timelock on admin functions
  - Detect if governance quorum threshold is suspiciously low
  - For EVM: read governor contract params if available

Uses direct API calls: DexScreener, Solscan (holders), Etherscan (contracts),
Birdeye, Helius.
"""

import contextlib
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

logger = get_logger("governance_attack")


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class HolderStake:
    """Top holder info for governance analysis."""

    address: str
    percentage: float  # % of total supply
    label: str = ""
    is_contract: bool = False
    is_exchange: bool = False


@dataclass
class GovernanceParams:
    """Governance contract parameters (EVM)."""

    timelock_address: str = ""
    timelock_delay_seconds: int = 0
    quorum_threshold_pct: float = 0.0  # % of supply needed for quorum
    proposal_threshold_pct: float = 0.0  # % of supply needed to propose
    voting_period_blocks: int = 0
    has_timelock: bool = False


@dataclass
class GovernanceAttackReport:
    token_address: str
    chain: str
    top_holder_pct: float = 0.0
    top_10_holder_pct: float = 0.0
    top_holders: list[HolderStake] = field(default_factory=list)
    has_timelock: bool = False
    timelock_delay_seconds: int = 0
    quorum_threshold: float = 0.0
    governance_params: GovernanceParams | None = None
    governance_risk_flags: list[str] = field(default_factory=list)
    is_governed: bool = False
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Detector Class ──────────────────────────────────────────────────


class GovernanceAttackDetector:
    """Detects governance concentration and attack risks.

    Fetches holder distribution data and (for EVM) governance contract
    parameters, then assesses whether the token's governance is
    vulnerable to takeover.
    """

    TOP_HOLDER_CRITICAL_PCT = 50.0  # >50% = critical
    TOP_HOLDER_HIGH_PCT = 30.0  # >30% = high risk
    TOP_10_CRITICAL_PCT = 80.0  # >80% in top 10 = critical
    LOW_QUORUM_PCT = 1.0  # <1% quorum = suspicious
    MIN_TIMELOCK_HOURS = 24  # Timelock should be ≥24h

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._birdeye_key = os.getenv("BIRDEYE_API_KEY", "")

    # ── Direct API fetchers ─────────────────────────────────────────

    async def _fetch_dexscreener_token(self, token_address: str) -> dict | None:
        """Fetch token pair data from DexScreener (free, no key)."""
        try:
            resp = await self._http.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs[0] if pairs else None
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_fetch_dexscreener_token_error", exc_info=True)
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    async def _fetch_solscan_holders(self, token_address: str, top_n: int = 20) -> list[dict]:
        """Fetch top holders from Solscan (Solana only)."""
        try:
            from app.free_solscan_client import FreeSolscanClient

            solscan = FreeSolscanClient()
            holders = solscan.get_holder_wallets(token_address, top_n=top_n)
            return holders or []
        except (httpx.HTTPError) as e :
            logger.debug("_fetch_solscan_holders_error", exc_info=True)
            logger.warning(f"Solscan holders failed for {token_address}: {e}")
            return []

    async def _fetch_birdeye_overview(self, token_address: str) -> dict | None:
        """Fetch token overview from Birdeye."""
        if not self._birdeye_key:
            return None
        try:
            resp = await self._http.get(
                "https://public-api.birdeye.so/defi/token_overview",
                params={"address": token_address},
                headers={"X-API-KEY": self._birdeye_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("data", body)
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_fetch_birdeye_overview_error", exc_info=True)
            logger.warning(f"Birdeye overview failed for {token_address}: {e}")
        return None

    async def _fetch_evm_holders(self, token_address: str, chain: str) -> list[dict]:
        """Fetch top EVM token holders from Etherscan-style explorer."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return []
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return []
        try:
            url = (
                f"{chain_cfg.explorer_api_url}"
                f"?module=token&action=tokenholderlist&contractaddress={token_address}"
                f"&page=1&offset=20&apikey={key}"
            )
            resp = await self._http.get(url)
            if resp.status_code == 200:
                body = resp.json()
                return body.get("result", []) or []
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_fetch_evm_holders_error", exc_info=True)
            logger.warning(f"Etherscan holders failed for {token_address}: {e}")
        return []

    async def _fetch_contract_source(self, address: str, chain: str) -> str | None:
        """Fetch contract source code from Etherscan-style explorer."""
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
                    return result[0].get("SourceCode", "")
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_fetch_contract_source_error", exc_info=True)
            logger.warning(f"Etherscan source fetch failed for {address}: {e}")
        return None

    async def _eth_call(self, to: str, data: str, chain: str) -> str | None:
        """Make an eth_call to read contract state."""
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
                return body.get("result")
        except (httpx.HTTPError, requests.RequestException, ConnectionError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, TypeError) as e :
            logger.debug("_eth_call_error", exc_info=True)
            logger.warning(f"eth_call failed for {to}: {e}")
        return None

    # ── Analysis helpers ────────────────────────────────────────────

    def _parse_holders(self, raw_holders: list[dict]) -> list[HolderStake]:
        """Parse raw holder data into HolderStake objects."""
        holders = []
        for h in raw_holders:
            addr = h.get("address", h.get("owner", h.get("TokenHolderAddress", "")))
            pct = float(h.get("pct", h.get("percentage", h.get("TokenHolderQuantity", 0))))
            label = h.get("label", "")
            is_contract = h.get("is_contract", False)
            is_exchange = bool(label and label.lower() in ("binance", "coinbase", "okx", "kraken"))
            holders.append(
                HolderStake(
                    address=addr,
                    percentage=pct,
                    label=label,
                    is_contract=is_contract,
                    is_exchange=is_exchange,
                )
            )
        return sorted(holders, key=lambda h: h.percentage, reverse=True)

    async def _check_evm_governance(
        self, token_address: str, chain: str
    ) -> GovernanceParams | None:
        """Try to read governance parameters from EVM governor contract.

        Checks for OpenZeppelin Governor patterns by reading common
        function selectors from the token contract.
        """
        params = GovernanceParams()

        source = await self._fetch_contract_source(token_address, chain)
        if not source:
            return params

        source_lower = source.lower()

        # Detect governance presence
        has_governor = any(
            kw in source_lower
            for kw in ["governor", "governance", "igovernor", "governorcompatibilitybravo"]
        )

        if not has_governor:
            return params

        params.has_timelock = "timelock" in source_lower

        # Try to read quorum from contract (QUORUM_RATIO selector: 0x3a221ab9)
        quorum_result = await self._eth_call(token_address, "0x3a221ab9", chain)
        if quorum_result and quorum_result != "0x" and len(quorum_result) > 2:
            try:
                quorum_raw = int(quorum_result, 16)
                # OpenZeppelin stores quorum as numerator / 1000 (e.g. 4 = 0.4%)
                params.quorum_threshold_pct = (
                    quorum_raw / 1000.0 if quorum_raw < 1000 else quorum_raw / 1e18 * 100
                )
            except (ValueError, ZeroDivisionError):
                pass

        # Try to read proposal threshold (PROPOSAL_THRESHOLD selector: 0xb12d4e26)
        threshold_result = await self._eth_call(token_address, "0xb12d4e26", chain)
        if threshold_result and threshold_result != "0x" and len(threshold_result) > 2:
            try:
                threshold_raw = int(threshold_result, 16)
                params.proposal_threshold_pct = threshold_raw / 1e18 * 100
            except (ValueError, ZeroDivisionError):
                pass

        # Try to read voting period (VOTING_PERIOD selector: 0x02f310d7)
        voting_result = await self._eth_call(token_address, "0x02f310d7", chain)
        if voting_result and voting_result != "0x" and len(voting_result) > 2:
            with contextlib.suppress(ValueError):
                params.voting_period_blocks = int(voting_result, 16)

        # Check timelock delay
        if params.has_timelock:
            # Try MIN_DELAY selector on timelock controller: 0xd2baa7d2
            delay_result = await self._eth_call(token_address, "0xd2baa7d2", chain)
            if delay_result and delay_result != "0x" and len(delay_result) > 2:
                with contextlib.suppress(ValueError):
                    params.timelock_delay_seconds = int(delay_result, 16)

        return params

    def _check_solana_governance(
        self, dex_data: dict | None, birdeye_data: dict | None
    ) -> GovernanceParams:
        """Infer governance risk for Solana SPL tokens.

        Solana doesn't have governor contracts in the EVM sense.
        Governance risk is mainly from supply concentration and
        whether the mint authority is renounced.
        """
        params = GovernanceParams()
        # Solana tokens: if authority is not renounced, governance is fully centralized
        if dex_data:
            info = dex_data.get("info", {})
            if isinstance(info, dict):
                mint_authority = info.get("mintAuthority", "")
                if mint_authority:
                    # Authority exists - can mint more tokens
                    params.has_timelock = False
                else:
                    # No mint authority = renounced
                    params.has_timelock = True  # treat as immutable
        return params

    # ── Risk calculation ─────────────────────────────────────────────

    def _calculate_risk(self, report: GovernanceAttackReport) -> tuple[int, str, list[str]]:
        """Calculate risk score from governance attack indicators."""
        score = 0
        warnings = []

        # Top holder concentration
        if report.top_holder_pct >= self.TOP_HOLDER_CRITICAL_PCT:
            score += 35
            warnings.append(f"CRITICAL: Top holder controls {report.top_holder_pct:.1f}% of supply")
        elif report.top_holder_pct >= self.TOP_HOLDER_HIGH_PCT:
            score += 20
            warnings.append(f"HIGH: Top holder controls {report.top_holder_pct:.1f}% of supply")
        elif report.top_holder_pct >= 15:
            score += 10
            warnings.append(f"MEDIUM: Top holder controls {report.top_holder_pct:.1f}% of supply")

        # Top 10 concentration
        if report.top_10_holder_pct >= self.TOP_10_CRITICAL_PCT:
            score += 20
            warnings.append(
                f"HIGH: Top 10 holders control {report.top_10_holder_pct:.1f}% - cartel risk"
            )
        elif report.top_10_holder_pct >= 60:
            score += 10
            warnings.append(f"MEDIUM: Top 10 holders control {report.top_10_holder_pct:.1f}%")

        # No timelock
        if report.is_governed and not report.has_timelock:
            score += 20
            warnings.append(
                "HIGH: Governance changes have no timelock - instant execution possible"
            )
        elif not report.has_timelock:
            score += 10
            warnings.append("MEDIUM: No timelock detected on admin functions")

        # Low quorum
        if report.quorum_threshold > 0 and report.quorum_threshold < self.LOW_QUORUM_PCT:
            score += 15
            warnings.append(
                f"HIGH: Governance quorum only {report.quorum_threshold:.2f}% - easily gamed"
            )

        # Timelock too short
        if report.has_timelock and report.timelock_delay_seconds > 0:
            timelock_hours = report.timelock_delay_seconds / 3600
            if timelock_hours < self.MIN_TIMELOCK_HOURS:
                score += 10
                warnings.append(
                    f"MEDIUM: Timelock only {timelock_hours:.1f}h - insufficient for reaction"
                )

        # Governance risk flags from analysis
        for flag in report.governance_risk_flags:
            if "critical" in flag.lower():
                score += 10
            elif "high" in flag.lower():
                score += 5

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

    async def analyze(self, token_address: str, chain: str) -> GovernanceAttackReport:
        """Full governance attack risk analysis for a token.

        Steps:
        1. Fetch holder distribution data
        2. Check top holder concentration
        3. For EVM: read governance contract parameters
        4. For Solana: check mint authority status
        5. Analyze governance risk flags
        6. Calculate risk score
        """
        report = GovernanceAttackReport(token_address=token_address, chain=chain)

        # 1. Fetch DEX + holder data
        dex_data = await self._fetch_dexscreener_token(token_address)

        raw_holders: list[dict] = []
        if is_solana(chain):
            raw_holders = await self._fetch_solscan_holders(token_address, top_n=20)
        elif is_evm(chain):
            raw_holders = await self._fetch_evm_holders(token_address, chain)

        # If no holders from primary source, try Birdeye
        if not raw_holders:
            birdeye_data = await self._fetch_birdeye_overview(token_address)
            if birdeye_data and isinstance(birdeye_data, dict):
                dist = birdeye_data.get("holderDistribution") or []
                raw_holders = dist

        # 2. Parse holders
        holders = self._parse_holders(raw_holders)
        report.top_holders = holders

        if holders:
            report.top_holder_pct = holders[0].percentage
            report.top_10_holder_pct = sum(h.percentage for h in holders[:10])

        # 3. Governance parameters
        gov_params: GovernanceParams | None = None
        if is_evm(chain):
            gov_params = await self._check_evm_governance(token_address, chain)
            if gov_params:
                report.governance_params = gov_params
                report.has_timelock = gov_params.has_timelock
                report.timelock_delay_seconds = gov_params.timelock_delay_seconds
                report.quorum_threshold = gov_params.quorum_threshold_pct
                report.is_governed = bool(
                    gov_params.quorum_threshold_pct > 0
                    or gov_params.proposal_threshold_pct > 0
                    or gov_params.voting_period_blocks > 0
                )

        elif is_solana(chain):
            gov_params = self._check_solana_governance(dex_data, None)
            report.governance_params = gov_params
            report.has_timelock = gov_params.has_timelock

        # 4. Generate governance risk flags
        risk_flags = []
        if report.top_holder_pct > 50:
            risk_flags.append(f"CRITICAL: Single entity majority ({report.top_holder_pct:.1f}%)")
        if report.is_governed and report.quorum_threshold < 1.0 and report.quorum_threshold > 0:
            risk_flags.append(f"HIGH: Quorum too low ({report.quorum_threshold:.2f}%)")
        if report.has_timelock and report.timelock_delay_seconds > 0:
            hours = report.timelock_delay_seconds / 3600
            if hours < 24:
                risk_flags.append(f"MEDIUM: Short timelock ({hours:.1f}h)")
        if not report.has_timelock:
            risk_flags.append("MEDIUM: No timelock on admin actions")
        report.governance_risk_flags = risk_flags

        # 5. Calculate risk
        report.risk_score, report.risk_level, report.warnings = self._calculate_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"governance attack concentration {chain}",
                chain=chain,
                address=token_address,
                scanner_type="governance_attack",
            )
            report.citations = rag_cits
            # Enhance warnings with citation references
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except (ConnectionError, OSError, TimeoutError):
            pass

        return report
