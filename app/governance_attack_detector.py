"""
Governance Attack & Concentration Risk Detector
================================================
Detects governance takeover vulnerabilities, voting concentration,
timelock bypass risks, and DAO governance attacks in crypto tokens.

Signals detected:
  - Top holder supply concentration (>50% = critical)
  - Top 10 holder cartel risk (>80%)
  - Missing or insufficient timelocks on admin functions
  - Dangerously low quorum thresholds (enabling flash-loan governance attacks)
  - Single-entity veto power (Solana mint authority not renounced)
  - Governance parameter manipulation vectors
  - Flash-loan governance attack feasibility assessment

Tier: Premium ($0.08)
Endpoint: POST /api/v1/x402-tools/governance_attack
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger("governance_attack_detector")

# ── Risk thresholds ──────────────────────────────────────────────
TOP_HOLDER_CRITICAL_PCT = 50.0
TOP_HOLDER_HIGH_PCT = 30.0
TOP_10_CRITICAL_PCT = 80.0
TOP_10_HIGH_PCT = 60.0
LOW_QUORUM_PCT = 1.0
MIN_TIMELOCK_HOURS = 24
CRITICAL_VOTE_DIFFERENTIAL_PCT = 5.0  # If votes swing >5% in block, suspicious

# ── Free API endpoints ──────────────────────────────────────────
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
SOLSCAN_TOKEN_HOLDERS = "https://api.solscan.io/v2/token/holders?token={}&limit=20"
BIRDEYE_HOLDER = "https://public-api.birdeye.so/defi/v3/token/holder?address={}"
HELIUS_WEBHOOK = "https://api.helius.xyz/v0/token-metadata?mintAddress={}"

# ── Dataclasses ──────────────────────────────────────────────────

@dataclass
class HolderStake:
    address: str = ""
    percentage: float = 0.0
    label: str = ""
    is_contract: bool = False
    is_exchange: bool = False

@dataclass
class GovernanceParams:
    timelock_address: str = ""
    timelock_delay_seconds: int = 0
    quorum_threshold_pct: float = 0.0
    proposal_threshold_pct: float = 0.0
    voting_period_blocks: int = 0
    has_timelock: bool = False
    is_governance_contract: bool = False

@dataclass
class GovernanceAttackResult:
    token_address: str
    chain: str
    timestamp: str = ""
    top_holder_pct: float = 0.0
    top_10_holder_pct: float = 0.0
    top_holders: list[dict] = field(default_factory=list)
    governance_params: dict | None = None
    is_governed: bool = False
    flash_loan_feasible: bool = False
    risk_score: int = 0
    risk_level: str = "LOW"
    flags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

# ─── HTTP helpers (minimal dependencies) ─────────────────────────

async def _fetch_json(
    url: str, headers: dict | None = None, params: dict | None = None, timeout: int = 10
) -> Any:
    """Single URL fetch returning parsed JSON or None."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(
                url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp,
        ):
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.debug(f"Fetch failed: {url} - {e}")
    return None

async def _eth_call(rpc_url: str, to: str, data: str) -> str | None:
    """Low-level eth_call to read contract state."""
    import aiohttp

    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": to, "data": data}, "latest"],
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status == 200:
                body = await resp.json()
                return body.get("result")
    except Exception as e:
        logger.debug(f"eth_call failed for {to}: {e}")
    return None

# ─── Data fetching ───────────────────────────────────────────────

async def _fetch_dexscreener(token_address: str) -> dict | None:
    """Fetch token pair data from DexScreener (free, no key)."""
    return await _fetch_json(DEXSCREENER_API.format(token_address))

async def _fetch_solscan_holders(token_address: str) -> list[dict]:
    """Fetch top holders from Solscan (Solana)."""
    data = await _fetch_json(
        "https://api-v2.solscan.io/v2/token/holders",
        params={"token": token_address, "limit": 20, "offset": 0},
        headers={"Accept": "application/json"},
    )
    if data and isinstance(data, dict):
        return data.get("data", [])
    return []

async def _fetch_birdeye_holders(token_address: str) -> list[dict]:
    """Fetch holders from Birdeye."""
    key = os.getenv("BIRDEYE_API_KEY", "")
    if not key:
        return []
    data = await _fetch_json(
        BIRDEYE_HOLDER.format(token_address),
        headers={"X-API-KEY": key, "accept": "application/json"},
    )
    if data and isinstance(data, dict):
        items = (
            data.get("data", {}).get("items", [])
            if isinstance(data.get("data"), dict)
            else data.get("items", [])
        )
        return items
    return []

async def _fetch_evm_holders(token_address: str, chain: str) -> list[dict]:
    """Fetch top EVM token holders from explorer API."""
    from app.chain_registry import CHAINS

    chain_cfg = CHAINS.get(chain)
    if not chain_cfg or not chain_cfg.explorer_api_url:
        return []
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    key = (
        chain_cfg.get_explorer_api_key()
        if hasattr(chain_cfg, "get_explorer_api_key")
        else etherscan_key
    )
    key = key or etherscan_key
    if not key:
        return []
    url = (
        f"{chain_cfg.explorer_api_url}"
        f"?module=token&action=tokenholderlist"
        f"&contractaddress={token_address}"
        f"&page=1&offset=20&apikey={key}"
    )
    data = await _fetch_json(url)
    if data and isinstance(data, dict):
        return data.get("result", [])
    return []

async def _fetch_contract_source(address: str, chain: str) -> str | None:
    """Fetch verified contract source code."""
    from app.chain_registry import CHAINS

    chain_cfg = CHAINS.get(chain)
    if not chain_cfg or not chain_cfg.explorer_api_url:
        return None
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    key = (
        chain_cfg.get_explorer_api_key()
        if hasattr(chain_cfg, "get_explorer_api_key")
        else etherscan_key
    )
    key = key or etherscan_key
    if not key:
        return None
    url = (
        f"{chain_cfg.explorer_api_url}"
        f"?module=contract&action=getsourcecode&address={address}"
        f"&apikey={key}"
    )
    data = await _fetch_json(url)
    if data and isinstance(data, dict):
        result = data.get("result", [])
        if result and len(result) > 0:
            return result[0].get("SourceCode", "")
    return None

def _get_rpc_url(chain: str) -> str | None:
    """Get first RPC URL for a chain."""
    from app.chain_registry import CHAINS

    chain_cfg = CHAINS.get(chain)
    if chain_cfg and chain_cfg.rpc_endpoints:
        return chain_cfg.rpc_endpoints[0]
    return None

# ─── Governance parameter extraction ─────────────────────────────

async def _extract_evm_governance_params(token_address: str, chain: str) -> GovernanceParams:
    """Extract governance parameters from EVM governor contract."""
    params = GovernanceParams()
    source = await _fetch_contract_source(token_address, chain)
    if not source:
        return params

    source_lower = source.lower()
    rpc_url = _get_rpc_url(chain)
    if not rpc_url:
        return params

    # Detect governance patterns in source code
    governance_kws = [
        "governor",
        "governance",
        "igovernor",
        "governorcompatibilitybravo",
        "governoralpha",
        "governorbeta",
    ]
    has_governor = any(kw in source_lower for kw in governance_kws)

    if not has_governor:
        return params

    params.is_governance_contract = True
    params.has_timelock = "timelock" in source_lower

    # Try to read quorum via eth_call (QUORUM selector: 0x3a221ab9)
    q_result = await _eth_call(rpc_url, token_address, "0x3a221ab9")
    if q_result and q_result != "0x" and len(q_result) > 2:
        try:
            q_raw = int(q_result, 16)
            # OZ stores quorum as numerator/1000 (e.g. 4 = 0.4%)
            params.quorum_threshold_pct = q_raw / 1000.0 if q_raw < 1000 else q_raw / 1e18 * 100
        except (ValueError, OverflowError):
            logger.debug(f"Could not parse quorum result: {q_result}")

    # Try proposal threshold (selector: 0xb12d4e26)
    p_result = await _eth_call(rpc_url, token_address, "0xb12d4e26")
    if p_result and p_result != "0x" and len(p_result) > 2:
        with contextlib.suppress(ValueError):
            p_raw = int(p_result, 16)
            params.proposal_threshold_pct = p_raw / 1e18 * 100

    # Try voting period (selector: 0x02f310d7)
    v_result = await _eth_call(rpc_url, token_address, "0x02f310d7")
    if v_result and v_result != "0x" and len(v_result) > 2:
        with contextlib.suppress(ValueError):
            params.voting_period_blocks = int(v_result, 16)

    # Check timelock delay if timelock detected (MIN_DELAY selector: 0xd2baa7d2)
    if params.has_timelock:
        td_result = await _eth_call(rpc_url, token_address, "0xd2baa7d2")
        if td_result and td_result != "0x" and len(td_result) > 2:
            with contextlib.suppress(ValueError):
                params.timelock_delay_seconds = int(td_result, 16)

    return params

def _check_solana_governance(dex_data: dict | None) -> GovernanceParams:
    """Infer governance risk for Solana SPL tokens (no EVM gov contracts)."""
    params = GovernanceParams()
    if dex_data:
        pairs = dex_data.get("pairs") or []
        for pair in pairs:
            info = (
                pair.get("info", {})
                if isinstance(pair.get("info"), dict)
                else pair.get("baseToken", {})
            )
            if isinstance(info, dict):
                # Check for mint authority in info
                mint_auth = info.get("mintAuthority", info.get("authority", ""))
                if mint_auth and mint_auth not in (
                    "",
                    "0x0000000000000000000000000000000000000000",
                    "11111111111111111111111111111111",
                ):
                    # Authority exists = not renounced = centralized
                    params.is_governance_contract = True
                    params.has_timelock = False
                else:
                    params.is_governance_contract = True
                    params.has_timelock = True  # Immutable = safe
    return params

# ─── Holders parsing ─────────────────────────────────────────────

def _parse_holders(raw_holders: list[dict]) -> list[HolderStake]:
    """Parse raw explorer holder data into structured objects."""
    holders = []
    for h in raw_holders:
        addr = h.get("address", h.get("owner", h.get("TokenHolderAddress", "")))
        raw_pct = h.get("pct", h.get("percentage", h.get("TokenHolderQuantity", "0")))
        try:
            pct = float(raw_pct) if raw_pct else 0.0
        except (ValueError, TypeError):
            pct = 0.0
        label = h.get("label", "")
        is_contract = bool(h.get("is_contract", h.get("isContract", False)))
        is_exchange = bool(
            label
            and any(
                ex in label.lower()
                for ex in ("binance", "coinbase", "okx", "kraken", "bybit", "gate")
            )
        )
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

# ─── Risk scoring ────────────────────────────────────────────────

def _score_governance_risk(
    top_holder_pct: float,
    top_10_holder_pct: float,
    params: GovernanceParams | None,
) -> tuple[int, str, list[str]]:
    """Calculate governance attack risk score (0-100)."""
    score = 0
    flags = []

    # Top holder concentration
    if top_holder_pct >= TOP_HOLDER_CRITICAL_PCT:
        score += 35
        flags.append(f"CRITICAL: Top holder controls {top_holder_pct:.1f}% of supply")
    elif top_holder_pct >= TOP_HOLDER_HIGH_PCT:
        score += 20
        flags.append(f"HIGH: Top holder controls {top_holder_pct:.1f}% of supply")
    elif top_holder_pct >= 15:
        score += 10
        flags.append(f"MEDIUM: Top holder controls {top_holder_pct:.1f}% of supply")

    # Top 10 concentration
    if top_10_holder_pct >= TOP_10_CRITICAL_PCT:
        score += 20
        flags.append(f"HIGH: Top 10 holders control {top_10_holder_pct:.1f}% - cartel risk")
    elif top_10_holder_pct >= TOP_10_HIGH_PCT:
        score += 10
        flags.append(f"MEDIUM: Top 10 holders control {top_10_holder_pct:.1f}%")

    # Governance parameter risks
    if params:
        # Timelock
        if params.is_governance_contract and not params.has_timelock:
            score += 25
            flags.append("CRITICAL: No timelock - single-transaction governance takeover")
        elif not params.has_timelock and not params.is_governance_contract:
            score += 10
            flags.append("MEDIUM: No governance timelock detected")

        # Low quorum
        if params.quorum_threshold_pct > 0:
            if params.quorum_threshold_pct < LOW_QUORUM_PCT:
                score += 20
                flags.append(
                    f"HIGH: Quorum only {params.quorum_threshold_pct:.2f}% - "
                    f"flash-loan governance attack feasible"
                )
            elif params.quorum_threshold_pct < 2.0:
                score += 10
                flags.append(
                    f"MEDIUM: Quorum {params.quorum_threshold_pct:.2f}% - below industry standard"
                )

        # Timelock too short
        if params.has_timelock and params.timelock_delay_seconds > 0:
            timelock_hours = params.timelock_delay_seconds / 3600
            if timelock_hours < MIN_TIMELOCK_HOURS:
                score += 10
                flags.append(
                    f"MEDIUM: Timelock only {timelock_hours:.1f}h - insufficient reaction window"
                )

        # Very short voting period
        if params.voting_period_blocks > 0:
            if params.voting_period_blocks < 200:  # ~40 min on ETH
                score += 15
                flags.append(
                    f"HIGH: Voting period only {params.voting_period_blocks} blocks - "
                    f"too short for community coordination"
                )
            elif params.voting_period_blocks < 1000:
                score += 5
                flags.append(f"MEDIUM: Voting period {params.voting_period_blocks} blocks")

        # Low proposal threshold
        if params.proposal_threshold_pct > 0 and params.proposal_threshold_pct < 0.1:
            score += 5
            flags.append(
                f"MEDIUM: Proposal threshold {params.proposal_threshold_pct:.3f}% - "
                f"anyone can propose"
            )

        # Flash loan governance attack feasibility
        flash_loan_feasible = (
            params.is_governance_contract
            and params.quorum_threshold_pct < LOW_QUORUM_PCT
            and params.quorum_threshold_pct > 0
        )
        if flash_loan_feasible:
            score += 15
            flags.append(
                "CRITICAL: Flash-loan governance attack feasible - "
                "borrow tokens, pass malicious proposal, repay in one tx"
            )

    score = min(100, score)

    if score >= 70:
        level = "CRITICAL"
    elif score >= 40:
        level = "HIGH"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"

    return score, level, flags

# ─── Main detection function ─────────────────────────────────────

async def detect_governance_attack(
    token_address: str,
    chain: str,
) -> dict[str, Any]:
    """Run full governance attack risk analysis.

    Args:
        token_address: The token/contract address to analyze
        chain: Blockchain name (e.g. 'ethereum', 'solana', 'bsc')

    Returns:
        Dict with risk_score, risk_level, flags, warnings, and full data.
    """
    from app.chain_registry import is_evm, is_solana

    result = GovernanceAttackResult(
        token_address=token_address,
        chain=chain,
        timestamp=datetime.now(UTC).isoformat(),
    )

    # 1. Fetch DEX data + holders
    dex_data = await _fetch_dexscreener(token_address)

    raw_holders: list[dict] = []
    if is_solana(chain):
        raw_holders = await _fetch_solscan_holders(token_address)
    elif is_evm(chain):
        raw_holders = await _fetch_evm_holders(token_address, chain)

    # Fallback to Birdeye if primary holder source returns nothing
    if not raw_holders:
        raw_holders = await _fetch_birdeye_holders(token_address)

    # 2. Parse holders
    holders = _parse_holders(raw_holders)
    result.top_holders = [asdict(h) for h in holders]

    if holders:
        result.top_holder_pct = holders[0].percentage
        result.top_10_holder_pct = sum(h.percentage for h in holders[:10])

    # 3. Extract governance parameters
    gov_params: GovernanceParams | None = None

    if is_evm(chain):
        gov_params = await _extract_evm_governance_params(token_address, chain)
    elif is_solana(chain):
        gov_params = _check_solana_governance(dex_data)

    if gov_params:
        result.governance_params = {
            "has_timelock": gov_params.has_timelock,
            "timelock_delay_seconds": gov_params.timelock_delay_seconds,
            "quorum_threshold_pct": round(gov_params.quorum_threshold_pct, 4),
            "proposal_threshold_pct": round(gov_params.proposal_threshold_pct, 4),
            "voting_period_blocks": gov_params.voting_period_blocks,
            "is_governance_contract": gov_params.is_governance_contract,
        }
        result.is_governed = gov_params.is_governance_contract

        # Check flash loan feasibility
        result.flash_loan_feasible = (
            gov_params.is_governance_contract
            and gov_params.quorum_threshold_pct > 0
            and gov_params.quorum_threshold_pct < LOW_QUORUM_PCT
        )

    # 4. Calculate risk score
    result.risk_score, result.risk_level, result.flags = _score_governance_risk(
        result.top_holder_pct, result.top_10_holder_pct, gov_params
    )

    # 5. Build warnings summary
    warnings = []
    if result.risk_level in ("CRITICAL", "HIGH"):
        if result.flash_loan_feasible:
            warnings.append(
                "FLASH-LOAN GOVERNANCE ATTACK: Quorum threshold is low enough "
                "that an attacker can borrow enough tokens via flash loan, "
                "pass any proposal, and repay - all in one transaction."
            )
        if result.top_holder_pct > 50:
            warnings.append(
                f"A single wallet controls {result.top_holder_pct:.1f}% - "
                f"they can unilaterally pass any governance action."
            )
    if not result.is_governed and not is_solana(chain):
        warnings.append(
            "Token does not appear to have on-chain governance - "
            "no governor contract detected in verified source."
        )
    if result.is_governed and result.governance_params:
        if not result.governance_params.get("has_timelock"):
            warnings.append(
                "Governance actions can execute instantly - no timelock delay protects holders."
            )
    result.warnings = warnings

    # 6. Build details
    result.details = {
        "holder_count": len(holders),
        "top_holder_pct": round(result.top_holder_pct, 2),
        "top_10_holder_pct": round(result.top_10_holder_pct, 2),
        "is_governed": result.is_governed,
        "flash_loan_attack_feasible": result.flash_loan_feasible,
        "governance_params": result.governance_params,
    }

    return asdict(result)
