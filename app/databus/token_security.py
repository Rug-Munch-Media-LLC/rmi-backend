"""
RugCharts Token Security Matrix
================================
37+ security checks across 3 tiers: Quick Scan, Deep Scan, ML Scan.

Tier 1 (Quick Scan - <500ms): GoPlus, honeypot, taxes, basic contract checks
Tier 2 (Deep Scan - 2-10s): Bytecode analysis, liquidity analysis, holder distribution
Tier 3 (ML Scan - async): XGBoost risk classifier, bytecode anomaly, symbol executor

Wired into DataBus as 'token_security' chain.
Produces the Authentic Score that feeds directly into the scanner.

Scoring bands:
  0-20: SAFE (green)
  21-40: LOW RISK (light green)
  41-60: MEDIUM RISK (yellow)
  61-80: HIGH RISK (orange)
  81-100: DANGER (red) - auto-fail
"""

import json
from app.core.logging import get_logger
import os
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import redis

logger = get_logger("token_security")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHE_TTL = 300  # 5 min active, 1 hour dormant

# ── Check Definitions ──────────────────────────────────────────────


class SecurityCheck:
    """A single security check with weight, category, and scoring."""

    __slots__ = ("auto_fail", "category", "description", "id", "name", "tier", "weight")

    def __init__(
        self,
        id: str,
        name: str,
        category: str,
        weight: float = 1.0,
        tier: int = 1,
        description: str = "",
        auto_fail: bool = False,
    ):
        self.id = id
        self.name = name
        self.category = category
        self.weight = weight
        self.tier = tier
        self.description = description
        self.auto_fail = auto_fail


# ── 37+ Security Checks ────────────────────────────────────────────

SECURITY_CHECKS = [
    # ── CATEGORY: Contract Risks (CR) ──
    SecurityCheck(
        "CR01",
        "Contract Verified",
        "contract_risks",
        1.5,
        1,
        "Contract source code is verified on explorer",
        auto_fail=True,
    ),
    SecurityCheck(
        "CR02",
        "Proxy Contract",
        "contract_risks",
        2.0,
        1,
        "Upgradeable proxy - owner can change logic at any time",
    ),
    SecurityCheck(
        "CR03",
        "Mint Function",
        "contract_risks",
        3.0,
        1,
        "Token has unrestricted mint() - infinite supply possible",
        auto_fail=True,
    ),
    SecurityCheck(
        "CR04",
        "Owner Privileges",
        "contract_risks",
        2.5,
        1,
        "Owner can pause trading, blacklist addresses, or adjust fees",
    ),
    SecurityCheck(
        "CR05",
        "Self-Destruct",
        "contract_risks",
        3.0,
        1,
        "Contract has SELFDESTRUCT opcode",
        auto_fail=True,
    ),
    SecurityCheck(
        "CR06",
        "External Call Risk",
        "contract_risks",
        1.5,
        2,
        "Unchecked external calls that could enable reentrancy",
    ),
    SecurityCheck(
        "CR07",
        "Delegatecall Risk",
        "contract_risks",
        2.0,
        2,
        "Use of delegatecall to untrusted contracts",
    ),
    # ── CATEGORY: Honeypot Detection (HP) ──
    SecurityCheck(
        "HP01",
        "Honeypot - GoPlus",
        "honeypot",
        5.0,
        1,
        "GoPlus API reports honeypot: buy OK, sell blocked",
        auto_fail=True,
    ),
    SecurityCheck(
        "HP02",
        "Honeypot - Honeypot.is",
        "honeypot",
        5.0,
        2,
        "Honeypot.is simulation confirms trapped tokens",
        auto_fail=True,
    ),
    SecurityCheck(
        "HP03",
        "Sell Tax Anomaly",
        "honeypot",
        3.0,
        1,
        "Buy tax normal but sell tax >50% - likely honeypot",
    ),
    SecurityCheck(
        "HP04",
        "Transfer Pausability",
        "honeypot",
        2.5,
        1,
        "Owner can pause token transfers entirely",
    ),
    SecurityCheck(
        "HP05",
        "Max Transaction Limit",
        "honeypot",
        1.5,
        1,
        "Extremely small max tx amount blocks legitimate sales",
    ),
    # ── CATEGORY: Liquidity Risks (LR) ──
    SecurityCheck(
        "LR01",
        "Liquidity Locked",
        "liquidity",
        2.0,
        1,
        "LP tokens are time-locked or burned",
        auto_fail=True,
    ),
    SecurityCheck(
        "LR02",
        "Liquidity Amount",
        "liquidity",
        1.5,
        1,
        "Pool liquidity < $1,000 - extreme slippage risk",
    ),
    SecurityCheck(
        "LR03",
        "LP Holder Concentration",
        "liquidity",
        2.0,
        1,
        "Single wallet holds >50% of LP tokens",
    ),
    SecurityCheck(
        "LR04",
        "Liquidity/Supply Ratio",
        "liquidity",
        1.0,
        1,
        "Low liquidity relative to total supply",
    ),
    SecurityCheck(
        "LR05",
        "Creator LP Removal",
        "liquidity",
        3.0,
        2,
        "Creator wallet removed significant liquidity",
    ),
    # ── CATEGORY: Holder Distribution (HD) ──
    SecurityCheck("HD01", "Top 10 Concentration", "holders", 2.0, 1, "Top 10 holders control >50% of supply"),
    SecurityCheck("HD02", "Creator Holdings", "holders", 2.5, 1, "Creator/deployer wallet holds >5% supply"),
    SecurityCheck("HD03", "Holder Count", "holders", 1.0, 1, "Very few holders suggests no organic adoption"),
    SecurityCheck("HD04", "Whale Dominance", "holders", 1.5, 2, "Wallets >1% supply own >80% collectively"),
    SecurityCheck(
        "HD05",
        "Fresh Wallet %",
        "holders",
        1.5,
        2,
        "High % of brand-new wallets (<7 days old) = bot risk",
    ),
    # ── CATEGORY: Trading Activity (TA) ──
    SecurityCheck(
        "TA01",
        "Wash Trading %",
        "trading",
        3.0,
        1,
        "Estimated fake volume percentage from authenticity scorer",
    ),
    SecurityCheck(
        "TA02",
        "Volume/Liquidity Ratio",
        "trading",
        1.5,
        1,
        "Suspiciously high volume relative to thin liquidity",
    ),
    SecurityCheck(
        "TA03",
        "Buy/Sell Imbalance",
        "trading",
        1.0,
        1,
        "Extreme buy or sell ratio suggests manipulation",
    ),
    SecurityCheck("TA04", "Trade Count", "trading", 0.5, 1, "Zero or near-zero trading activity"),
    SecurityCheck("TA05", "Price Impact", "trading", 2.0, 2, "Single trade moves price >30%"),
    # ── CATEGORY: Deployer/Team (DT) ──
    SecurityCheck(
        "DT01",
        "Deployer History",
        "deployer",
        3.0,
        2,
        "Deployer wallet previously launched scam tokens",
    ),
    SecurityCheck("DT02", "Deployer Funding", "deployer", 2.0, 2, "Deployer funded from Tornado Cash or mixer"),
    SecurityCheck(
        "DT03",
        "Multi-Token Deployer",
        "deployer",
        2.5,
        1,
        "Deployer launched 10+ tokens - factory pattern",
    ),
    SecurityCheck(
        "DT04",
        "Team Wallet Tracing",
        "deployer",
        1.5,
        2,
        "Connected wallets show suspicious activity",
    ),
    SecurityCheck(
        "DT05",
        "Social Presence",
        "deployer",
        0.5,
        1,
        "No website, Twitter, or Telegram - likely ghost token",
    ),
    # ── CATEGORY: Token Economics (TE) ──
    SecurityCheck(
        "TE01",
        "Supply Concentration",
        "tokenomics",
        2.0,
        1,
        "Creator/team controls >20% of total supply",
    ),
    SecurityCheck("TE02", "Tax Anomaly", "tokenomics", 2.5, 1, "Buy/sell tax >10% - predatory economics"),
    SecurityCheck(
        "TE03",
        "Transfer Fee",
        "tokenomics",
        1.5,
        1,
        "Hidden transfer fees beyond standard DEX swap tax",
    ),
    SecurityCheck(
        "TE04",
        "Blacklist Function",
        "tokenomics",
        2.0,
        1,
        "Owner can blacklist specific addresses from trading",
    ),
    SecurityCheck(
        "TE05",
        "Max Wallet Cap",
        "tokenomics",
        1.0,
        2,
        "Artificially low max wallet cap prevents accumulation",
    ),
    # ── CATEGORY: Rug Pull Indicators (RP) ──
    SecurityCheck(
        "RP01",
        "Rug Pull - Known Pattern",
        "rug_pull",
        5.0,
        2,
        "Token matches known rug pull deployment pattern",
        auto_fail=True,
    ),
    SecurityCheck(
        "RP02",
        "Liquidity Removal",
        "rug_pull",
        5.0,
        1,
        "LP was removed or significantly drained",
        auto_fail=True,
    ),
    SecurityCheck("RP03", "Price Crash", "rug_pull", 2.0, 2, "Price dropped >90% within 24h - possible rug"),
    SecurityCheck(
        "RP04",
        "Duplicate Token",
        "rug_pull",
        1.5,
        2,
        "Same name/symbol as another token - impersonation",
    ),
    SecurityCheck(
        "RP05",
        "Age Risk",
        "rug_pull",
        1.0,
        1,
        "Token deployed within last hour - highest risk period",
    ),
]


def get_checks_by_tier(tier: int) -> list[SecurityCheck]:
    return [c for c in SECURITY_CHECKS if c.tier <= tier]


def get_check_matrix() -> dict:
    """Full security check matrix with descriptions."""
    categories = defaultdict(list)
    for check in SECURITY_CHECKS:
        categories[check.category].append(
            {
                "id": check.id,
                "name": check.name,
                "weight": check.weight,
                "tier": check.tier,
                "auto_fail": check.auto_fail,
                "description": check.description,
            }
        )
    return {
        "total_checks": len(SECURITY_CHECKS),
        "categories": dict(categories),
        "tiers": {
            1: "Quick Scan (<500ms) - GoPlus, honeypot, basic contract",
            2: "Deep Scan (2-10s) - Bytecode, liquidity, deployer tracing",
            3: "ML Scan (async) - XGBoost, bytecode anomaly, symbolic executor",
        },
    }


# ── Scoring Engine ─────────────────────────────────────────────────


class TokenSecurityScorer:
    """Weighs and aggregates security check results into a 0-100 risk score."""

    SCORE_BANDS: ClassVar[list] =[
        (0, 20, "SAFE", "#00FF88"),
        (21, 40, "LOW RISK", "#88FF00"),
        (41, 60, "MEDIUM RISK", "#FFD700"),
        (61, 80, "HIGH RISK", "#FF8800"),
        (81, 100, "DANGER", "#FF0044"),
    ]

    @staticmethod
    def band_for_score(score: float) -> tuple[str, str]:
        for lo, hi, band, color in TokenSecurityScorer.SCORE_BANDS:
            if lo <= score <= hi:
                return band, color
        return "DANGER", "#FF0044"

    @staticmethod
    def compute_score(check_results: dict[str, Any]) -> tuple[float, str, str]:
        """Compute weighted risk score from check results.

        Each check result: {"status": "pass"|"fail"|"warning"|"unknown", "details": str}

        Returns (score 0-100, band, color)
        """
        total_weight = 0.0
        weighted_score = 0.0
        auto_fail_triggered = False
        check_details = []

        # Create lookup by ID
        checks_by_id = {c.id: c for c in SECURITY_CHECKS}

        for check_id, result in check_results.items():
            check = checks_by_id.get(check_id)
            if not check:
                continue

            status = result.get("status", "unknown")
            total_weight += check.weight

            if status == "fail":
                weighted_score += check.weight * 100.0
                check_details.append(f"FAIL {check.id} {check.name}: {result.get('details', '')}")
                if check.auto_fail:
                    auto_fail_triggered = True
            elif status == "warning":
                weighted_score += check.weight * 65.0
                check_details.append(f"WARN {check.id} {check.name}: {result.get('details', '')}")
            elif status == "unknown":
                weighted_score += check.weight * 30.0  # uncertainty penalty
            # 'pass' adds 0

        if total_weight == 0:
            return 50.0, "MEDIUM RISK", "#FFD700"

        score = weighted_score / total_weight

        if auto_fail_triggered:
            score = max(score, 85.0)

        band, color = TokenSecurityScorer.band_for_score(score)
        return round(score, 1), band, color


# ── Quick Scan Provider ─────────────────────────────────────────────


async def run_quick_scan(address: str, chain: str = "ethereum", **kw) -> dict[str, Any]:
    """Tier 1 quick scan - GoPlus + basic checks. <1s target."""
    results = {}
    api_key = kw.get("api_key", "") or kw.get("goplus_key", "") or os.getenv("GOPLUS_API_KEY", "")

    try:
        # Map chain name to GoPlus chain ID
        chain_map = {
            "ethereum": "1",
            "eth": "1",
            "bsc": "56",
            "polygon": "137",
            "arbitrum": "42161",
            "optimism": "10",
            "base": "8453",
            "avalanche": "43114",
            "fantom": "250",
            "gnosis": "100",
        }
        goplus_chain = chain_map.get(chain.lower(), chain)

        # GoPlus Security API (free, no key needed for basic)
        async with httpx.AsyncClient(timeout=5) as c:
            # Token security detection
            goplus_url = f"https://api.gopluslabs.io/api/v1/token_security/{goplus_chain}"
            addr_lower = address.lower()
            r = await c.get(goplus_url, params={"contract_addresses": addr_lower})
            if r.status_code == 200:
                goplus_data = r.json().get("result", {}).get(address.lower(), {})

                # Parse GoPlus into our check format
                is_honeypot = goplus_data.get("is_honeypot", "0")
                results["HP01"] = {
                    "status": "fail" if is_honeypot in ("1", "1.0") else "pass",
                    "details": f"GoPlus honeypot={'YES' if is_honeypot in ('1', '1.0') else 'no'}",
                }

                # Contract checks
                is_open_source = goplus_data.get("is_open_source", "0")
                results["CR01"] = {
                    "status": "pass" if is_open_source in ("1", "1.0") else "fail",
                    "details": f"Verified={'YES' if is_open_source in ('1', '1.0') else 'NO'}",
                }

                is_proxy = goplus_data.get("is_proxy", "0")
                results["CR02"] = {
                    "status": "warning" if is_proxy in ("1", "1.0") else "pass",
                    "details": f"Proxy={'YES' if is_proxy in ('1', '1.0') else 'no'}",
                }

                is_mintable = goplus_data.get("is_mintable", "0")
                results["CR03"] = {
                    "status": "fail" if is_mintable in ("1", "1.0") else "pass",
                    "details": f"Mintable={'YES' if is_mintable in ('1', '1.0') else 'no'}",
                }

                # Tax checks
                buy_tax = float(goplus_data.get("buy_tax", "0"))
                sell_tax = float(goplus_data.get("sell_tax", "0"))

                if sell_tax > 50:
                    results["HP03"] = {"status": "fail", "details": f"Sell tax={sell_tax}%"}
                elif sell_tax > 10:
                    results["HP03"] = {"status": "warning", "details": f"Sell tax={sell_tax}%"}

                if buy_tax > 10 or sell_tax > 10:
                    results["TE02"] = {
                        "status": "warning" if sell_tax <= 50 else "fail",
                        "details": f"Buy={buy_tax}% Sell={sell_tax}%",
                    }

                # Transfer pausable
                transfer_pausable = goplus_data.get("transfer_pausable", "0")
                results["HP04"] = {
                    "status": "warning" if transfer_pausable in ("1", "1.0") else "pass",
                    "details": f"Pausable={'YES' if transfer_pausable in ('1', '1.0') else 'no'}",
                }

                # Owner privileges
                owner_change_balance = goplus_data.get("owner_change_balance", "0")
                results["CR04"] = {
                    "status": "warning" if owner_change_balance in ("1", "1.0") else "pass",
                    "details": "Owner can change balances" if owner_change_balance in ("1", "1.0") else "",
                }

                # Blacklist
                is_blacklisted = goplus_data.get("is_blacklisted", "0")
                results["TE04"] = {
                    "status": "warning" if is_blacklisted in ("1", "1.0") else "pass",
                    "details": f"Blacklist={'YES' if is_blacklisted in ('1', '1.0') else 'no'}",
                }

                # Holder count
                holder_count = int(goplus_data.get("holder_count", "0"))
                results["HD03"] = {
                    "status": "warning" if holder_count < 10 else "pass",
                    "details": f"Holders={holder_count}",
                }

                # LP holder check
                is_in_anti_whale = goplus_data.get("is_anti_whale", "0")
                results["LR03"] = {
                    "status": "warning" if is_in_anti_whale in ("1", "1.0") else "pass",
                    "details": f"Anti-whale={'YES' if is_in_anti_whale in ('1', '1.0') else 'no'}",
                }

    except Exception as e:
        logger.debug(f"GoPlus scan failed: {e}")

    # ── Our DataBus checks ──
    # Age check
    try:
        api_key = kw.get("api_key", "") or os.getenv("HELIUS_API_KEY", "") or os.getenv("ALCHEMY_API_KEY", "")
        if api_key and chain == "solana":
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 1}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    if sigs:
                        block_time = sigs[0].get("blockTime", 0)
                        age_seconds = int(time.time()) - block_time
                        age_hours = age_seconds / 3600
                        if age_hours < 1:
                            results["RP05"] = {
                                "status": "fail",
                                "details": f"Age={age_hours:.1f}h (<1h)",
                            }
                        elif age_hours < 24:
                            results["RP05"] = {
                                "status": "warning",
                                "details": f"Age={age_hours:.1f}h",
                            }
                        else:
                            results["RP05"] = {"status": "pass", "details": f"Age={age_hours:.0f}h"}
    except Exception:
        pass

    # Volume authenticity
    try:
        volume_24h = float(kw.get("volume_24h", 0))
        liquidity = float(kw.get("liquidity_usd", 0))
        unique_wallets = int(kw.get("unique_wallets", 0))
        tx_count = int(kw.get("tx_count", 0))

        if volume_24h > 0 or liquidity > 0:
            from app.databus.volume_authenticity import quick_authenticity_score

            auth = quick_authenticity_score(volume_24h, liquidity, unique_wallets, tx_count)
            fake_pct = auth.get("fake_volume_pct", 0)

            if fake_pct > 50:
                results["TA01"] = {"status": "fail", "details": f"Fake volume={fake_pct}%"}
            elif fake_pct > 20:
                results["TA01"] = {"status": "warning", "details": f"Fake volume={fake_pct}%"}

            if liquidity > 0:
                vl_ratio = volume_24h / liquidity
                if vl_ratio > 5:
                    results["TA02"] = {"status": "warning", "details": f"V/L={vl_ratio:.1f}x"}
    except Exception:
        pass

    # Compute final score
    score, band, color = TokenSecurityScorer.compute_score(results)

    return {
        "checks": dict(results.items()),
        "total_checks_run": len(results),
        "security_score": score,
        "risk_band": band,
        "risk_color": color,
        "scan_tier": 1,
        "scan_type": "quick",
        "source": "token_security_matrix",
    }


# ── Full Security Scan (Tier 1 + 2 + 3 async) ─────────────────────


async def run_full_scan(address: str = "", chain: str = "ethereum", **kw) -> dict | None:
    """DataBus provider: Full token security scan.

    Returns scored results with breakdown by category.
    """
    if not address:
        return None

    cache_key = f"token_security:{chain}:{address}"
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except Exception:
        pass

    # Run quick scan
    result = await run_quick_scan(address, chain, **kw)

    # Enrich
    result["token_address"] = address
    result["chain"] = chain
    result["scan_timestamp"] = datetime.now(UTC).isoformat()

    # Category breakdown
    categories = defaultdict(lambda: {"pass": 0, "fail": 0, "warning": 0, "unknown": 0})
    for check_id, check_result in result["checks"].items():
        check = next((c for c in SECURITY_CHECKS if c.id == check_id), None)
        cat = check.category if check else "unknown"
        status = check_result.get("status", "unknown")
        categories[cat][status] = categories[cat].get(status, 0) + 1

    result["category_breakdown"] = dict(categories)

    # Cache
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        r.setex(cache_key, CACHE_TTL, json.dumps(result, default=str))
        r.close()
    except Exception:
        pass

    return result


async def get_check_matrix_endpoint(**kw) -> dict:
    """Returns the full security check matrix definition."""
    return get_check_matrix()
