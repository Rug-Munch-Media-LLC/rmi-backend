"""Coordinated buy and wash trading detection signals."""
from __future__ import annotations

from collections import defaultdict

import httpx

from app.domains.detectors.pump_dump_detector_package.sources import fetch_trades
from app.domains.detectors.pump_dump_detector_package.types import (
    PUMP_DUMP_THRESHOLDS,
    CoordinatedBuyGroup,
    FindingSeverity,
    ManipulationType,
    WashTradeCluster,
)


async def detect_coordinated_buys(
    client: httpx.AsyncClient, token_address: str, chain: str, depth: int = 100,
) -> dict:
    """Detect groups of wallets buying in coordinated windows."""
    findings: list[dict] = []
    groups: list[CoordinatedBuyGroup] = []
    trades = await fetch_trades(client, token_address, chain, depth)
    if not trades:
        return {"groups": groups, "findings": findings}
    buy_trades = [t for t in trades if t.get("type", "").lower() == "buy"]
    if len(buy_trades) < PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
        return {"groups": groups, "findings": findings}
    buy_trades.sort(key=lambda t: t.get("unixTime", 0) or 0)
    clusters = []
    current_cluster: list[dict] = [buy_trades[0]]
    for trade in buy_trades[1:]:
        pt = current_cluster[-1].get("unixTime", 0)
        ct = trade.get("unixTime", 0)
        if ct - pt <= PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"]:
            current_cluster.append(trade)
        else:
            if len(current_cluster) >= PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
                clusters.append(current_cluster)
            current_cluster = [trade]
    if len(current_cluster) >= PUMP_DUMP_THRESHOLDS["coordinated_min_wallets"]:
        clusters.append(current_cluster)
    for cluster in clusters:
        wallets = []
        total_usd = 0.0
        for trade in cluster:
            w = trade.get("owner", "") or trade.get("wallet", "") or trade.get("trader", "")
            if w:
                wallets.append(w)
            total_usd += float(trade.get("amountUsd", 0) or 0)
        if not wallets:
            continue
        groups.append(CoordinatedBuyGroup(
            wallets=wallets[:20], window_seconds=PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"],
            total_buy_usd=total_usd, fresh_wallet_count=0,
            block_number=cluster[0].get("slot", None) or cluster[0].get("blockNumber", None),
            timestamp=cluster[0].get("unixTime", None), chain=chain,
        ))
        sev = FindingSeverity.HIGH if len(wallets) >= 5 else FindingSeverity.MEDIUM
        findings.append({"finding_type": ManipulationType.COORDINATED_PUMP, "severity": sev,
            "description": f"Coordinated buy group: {len(wallets)} wallets bought ${total_usd:,.0f} in {PUMP_DUMP_THRESHOLDS['coordinated_buy_window_s']}s window",
            "detail": f"Fresh wallets: 0/{len(wallets)}",
            "evidence": {"wallet_count": len(wallets), "total_usd": total_usd, "window_s": PUMP_DUMP_THRESHOLDS["coordinated_buy_window_s"]}})
    return {"groups": groups, "findings": findings}

async def detect_wash_trading(
    client: httpx.AsyncClient, token_address: str, chain: str,
) -> dict:
    """Detect wash trading patterns (circular trades between controlled wallets)."""
    findings: list[dict] = []
    clusters: list[WashTradeCluster] = []
    trades = await fetch_trades(client, token_address, chain, 200)
    if not trades or len(trades) < 10:
        return {"clusters": clusters, "findings": findings}
    transfers: dict[str, list[str]] = defaultdict(list)
    for trade in trades:
        fw = trade.get("owner", "") or trade.get("from", "")
        tw = trade.get("to", "") or "market"
        if fw and fw != tw:
            transfers[fw].append(tw)
    if not transfers:
        return {"clusters": clusters, "findings": findings}
    ws = list(transfers.keys())[:50]
    cs: list[frozenset[str]] = []
    for w1 in ws:
        for w2 in transfers.get(w1, []):
            if w2 not in transfers:
                continue
            for w3 in transfers.get(w2, []):
                if w3 in transfers and w1 in transfers.get(w3, []):
                    c: frozenset[str] = frozenset([w1, w2, w3])
                    if not any(c.issubset(e) for e in cs):
                        cs.append(c)
    tv = sum(float(t.get("amountUsd", 0) or 0) for t in trades) or 1
    for circle in cs:
        ctrades = [t for t in trades if (t.get("owner", "") or t.get("from", "")) in circle]
        vol = sum(float(t.get("amountUsd", 0) or 0) for t in ctrades)
        vpct = (vol / tv * 100) if tv > 0 else 0
        if vpct >= PUMP_DUMP_THRESHOLDS["wash_trade_min_volume_pct"]:
            clusters.append(WashTradeCluster(wallets=list(circle), volume_created_usd=vol,
                trade_count=len(ctrades), circular_trades=len(ctrades), volume_pct_of_total=vpct))
            sev = FindingSeverity.CRITICAL if vpct > 25 else FindingSeverity.HIGH
            findings.append({"finding_type": ManipulationType.WASH_TRADING, "severity": sev,
                "description": f"Wash trading ring: {len(circle)} wallets created ${vol:,.0f} ({vpct:.0f}% of total volume)",
                "detail": f"Circular flow: {', '.join(list(circle)[:3])}... → circular pattern",
                "evidence": {"wallets": list(circle), "volume": vol, "volume_pct": vpct}})
    return {"clusters": clusters, "findings": findings}
