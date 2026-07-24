"""Pre-pump accumulation and lifecycle pattern detection signals."""
from collections import defaultdict

import httpx

from app.domains.detectors.pump_dump_detector_package.sources import fetch_holders, fetch_trades
from app.domains.detectors.pump_dump_detector_package.types import (
    FindingSeverity,
    ManipulationType,
    PrePumpAccumulation,
)


async def detect_pre_pump_accumulation(
    client: httpx.AsyncClient, token_address: str, chain: str,
) -> dict:
    """Detect wallets accumulating tokens before a pump event."""
    findings: list[dict] = []
    accumulations: list[PrePumpAccumulation] = []
    holders_data = await fetch_holders(client, token_address, chain)
    if not holders_data or holders_data.get("holders", 0) > 500:
        return {"accumulations": accumulations, "findings": findings}
    trades = await fetch_trades(client, token_address, chain, 200)
    if not trades or len(trades) < 20:
        return {"accumulations": accumulations, "findings": findings}
    trades.sort(key=lambda t: t.get("unixTime", 0) or 0)
    wb: dict[str, list[dict]] = defaultdict(list)
    ws: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        w = trade.get("owner", "") or trade.get("from", "") or trade.get("wallet", "")
        if not w:
            continue
        (wb if trade.get("type", "").lower() == "buy" else ws)[w].append(trade)
    ft = trades[0].get("unixTime", 0)
    lt = trades[-1].get("unixTime", 0)
    tr = lt - ft if lt > ft else 3600
    for wallet, buys in wb.items():
        if len(buys) < 2:
            continue
        if len(ws.get(wallet, [])) >= len(buys):
            continue
        eb = [b for b in buys if (b.get("unixTime", 0) - ft) < tr * 0.25]
        if eb and len(eb) >= 2:
            ta = sum(float(b.get("amountUsd", 0) or 0) for b in eb)
            if ta > 100:
                accumulations.append(PrePumpAccumulation(
                    wallets=[wallet], total_accumulated_usd=ta,
                    accumulation_period_hours=4, avg_entry_price=ta / max(len(eb), 1),
                    timing_gap_minutes=30,
                ))
    if accumulations:
        ta = sum(a.total_accumulated_usd for a in accumulations)
        findings.append({"finding_type": ManipulationType.PRE_PUMP_ACCUMULATION,
            "severity": FindingSeverity.HIGH if len(accumulations) >= 3 else FindingSeverity.MEDIUM,
            "description": f"Pre-pump accumulation detected: {len(accumulations)} wallets accumulated ${ta:,.0f} before pump window",
            "detail": "Multiple wallets bought early and retained positions" if len(accumulations) >= 3 else "",
            "evidence": {"accumulation_count": len(accumulations), "total_accumulated": ta}})
    return {"accumulations": accumulations, "findings": findings}


def check_lifecycle_patterns(pairs_data: dict) -> list[dict]:
    """Check for known pump-dump lifecycle patterns."""
    findings: list[dict] = []
    if not pairs_data.get("pairs"):
        return findings
    pair = pairs_data["pairs"][0]
    pair_age_hours = float(pair.get("pairCreatedAt", 0) or 0)
    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    if pair_age_hours < 24 and liquidity_usd > 0:
        vol_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        if vol_24h > liquidity_usd * 5:
            findings.append({"finding_type": ManipulationType.LIFECYCLE_MATCH, "severity": FindingSeverity.HIGH,
                "description": f"Pair less than 24h old with volume {vol_24h / liquidity_usd:.0f}x liquidity - pump pattern",
                "detail": f"Age: ~{pair_age_hours:.0f}h | LP: ${liquidity_usd:,.0f} | Volume: ${vol_24h:,.0f}",
                "evidence": {"pair_age_hours": pair_age_hours, "liquidity_usd": liquidity_usd, "volume_vs_liquidity": vol_24h / liquidity_usd}})
    txns = pair.get("txns", {})
    b1 = int(txns.get("h1", {}).get("buys", 0) or 0)
    s1 = int(txns.get("h1", {}).get("sells", 0) or 0)
    if b1 > 10 and s1 < b1 * 0.3:
        findings.append({"finding_type": ManipulationType.LIFECYCLE_MATCH, "severity": FindingSeverity.MEDIUM,
            "description": f"Unbalanced buy/sell ratio in 1h: {b1} buys vs {s1} sells",
            "detail": "More buyers than sellers can indicate coordinated buying phase",
            "evidence": {"buys_1h": b1, "sells_1h": s1}})
    return findings


def calculate_risk_score(findings) -> float:
    from app.domains.detectors.pump_dump_detector_package.types import FindingSeverity
    if not findings:
        return 0.0
    score = sum({FindingSeverity.CRITICAL: 35, FindingSeverity.HIGH: 20, FindingSeverity.MEDIUM: 10,
        FindingSeverity.LOW: 5, FindingSeverity.INFO: 1}.get(f.severity, 0) for f in findings)
    return min(score, 100.0)


def score_to_level(score: float) -> str:
    if score >= 70: return "critical"
    elif score >= 40: return "high"
    elif score >= 20: return "medium"
    else: return "low"


def estimate_traders(pairs_data: dict) -> int:
    if not pairs_data.get("pairs"):
        return 0
    txns = pairs_data["pairs"][0].get("txns", {})
    h24 = txns.get("h24", {})
    return int(h24.get("buys", 0) or 0) + int(h24.get("sells", 0) or 0)
