"""
Degen Security Scanner — Phase Implementations
==================================================
Each phase is a standalone function that operates on a scanner instance and
report. Extracted from scanner.py to keep individual modules under 400 lines.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.domains.scanner.degen_security_scanner.models import (
        SecurityReport,
    )
    from app.domains.scanner.degen_security_scanner.scanner import (
        DegenSecurityScanner,
    )

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: TOKEN METADATA
# ═══════════════════════════════════════════════════════════════════════════


async def phase1_metadata(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Get basic token info from Solscan + Helius."""
    token_data = scanner.solscan.token_data(report.token_address)
    if token_data:
        report.token_name = token_data.get("name", "Unknown")
        report.token_symbol = token_data.get("symbol", "???")
        report.decimals = token_data.get("decimals", 0)
        supply_raw = token_data.get("supply", "0")
        try:
            report.total_supply = float(supply_raw) / (10**report.decimals) if report.decimals else float(supply_raw)
        except (ConnectionError, OSError, TimeoutError):
            report.total_supply = 0
        report.created_at = token_data.get("createdTime")
    else:
        helius_data = await _helius_token_metadata(scanner, report.token_address)
        if helius_data:
            report.token_name = helius_data.get("name", "Unknown")
            report.token_symbol = helius_data.get("symbol", "???")
            report.decimals = helius_data.get("decimals", 0)

    market_data = await _birdeye_token_data(scanner, report.token_address)
    if market_data:
        report.price_usd = market_data.get("price", 0)
        report.market_cap = market_data.get("mc", 0)
        report.volume_24h = market_data.get("v24hUSD", 0)
        report.price_change_24h = market_data.get("priceChange24h", 0)
        report.liquidity_usd = market_data.get("liquidity", 0)
        report.buy_sell_ratio = market_data.get("buySellRatio", 1.0)

    holders_total = scanner.solscan.token_holders_total(report.token_address)
    if holders_total:
        report.total_holders = holders_total


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: AUTHORITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════


async def phase2_authorities(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Check mint/freeze authorities via Helius."""
    helius_data = await _helius_token_metadata(scanner, report.token_address)
    if not helius_data:
        return

    mint_auth = helius_data.get("mintAuthority")
    freeze_auth = helius_data.get("freezeAuthority")

    report.mint_authority = mint_auth
    report.freeze_authority = freeze_auth
    report.mint_authority_renounced = mint_auth is None or mint_auth == ""
    report.freeze_authority_renounced = freeze_auth is None or freeze_auth == ""

    risk_score = 0
    if not report.mint_authority_renounced:
        risk_score += 30
        scanner.critical_warnings.append(
            f"\U0001f6a8 MINT AUTHORITY ACTIVE: Dev can mint unlimited tokens! Authority: {mint_auth[:20]}..."
        )
        scanner.recommendations.append("Mint authority should be renounced for investor safety")

    if not report.freeze_authority_renounced:
        risk_score += 25
        scanner.critical_warnings.append("\u2744\ufe0f FREEZE AUTHORITY ACTIVE: Dev can freeze any wallet!")
        scanner.recommendations.append("Freeze authority should be renounced immediately")

    if risk_score >= 45:
        report.authorities_risk = "CRITICAL"
    elif risk_score >= 25:
        report.authorities_risk = "HIGH"
    elif risk_score >= 10:
        report.authorities_risk = "MEDIUM"
    else:
        report.authorities_risk = "LOW"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: HOLDER ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════


async def phase3_holders(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Analyze top holders for concentration and risk."""
    from app.domains.scanner.degen_security_scanner.models import HolderProfile
    from app.free_solscan_client import is_known_exchange

    holders = scanner.solscan.get_holder_wallets(report.token_address, top_n=50)
    if not holders:
        return

    profiles: list[HolderProfile] = []
    top_10_total = 0.0
    top_20_total = 0.0
    top_50_total = 0.0

    for i, h in enumerate(holders):
        addr = h.get("address", "")
        amount = h.get("amount", 0)
        pct = float(h.get("uiAmount", 0))

        profile = HolderProfile(
            address=addr,
            balance=float(amount),
            percentage=pct,
        )

        exchange = is_known_exchange(addr)
        if exchange:
            profile.exchange_tag = exchange
            profile.is_contract = True

        if len(addr) < 40:
            profile.is_contract = True

        if pct > 2.0:
            profile.is_known_whale = True

        profiles.append(profile)

        if i < 10:
            top_10_total += pct
        if i < 20:
            top_20_total += pct
        top_50_total += pct

    report.top_holders = profiles[:20]
    report.top_10_concentration = round(top_10_total, 2)
    report.top_20_concentration = round(top_20_total, 2)
    report.top_50_concentration = round(top_50_total, 2)

    known_smart = sum(1 for p in profiles if p.is_known_whale and not p.is_contract)
    report.smart_money_holders = known_smart
    report.dumb_money_holders = max(0, report.total_holders - known_smart)
    report.smart_money_ratio = round(known_smart / len(profiles), 2) if profiles else 0

    if top_10_total > 80:
        report.holder_health_score = 10
        scanner.critical_warnings.append(
            f"\u2620\ufe0f CATASTROPHIC CONCENTRATION: Top 10 holders own {top_10_total}%!"
        )
    elif top_10_total > 50:
        report.holder_health_score = 30
        scanner.critical_warnings.append(f"\u26a0\ufe0f DANGEROUS CONCENTRATION: Top 10 hold {top_10_total}%")
    elif top_10_total > 30:
        report.holder_health_score = 50
    elif top_10_total > 20:
        report.holder_health_score = 70
    else:
        report.holder_health_score = 90


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: FUNDING TRACE
# ═══════════════════════════════════════════════════════════════════════════


async def phase4_funding(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Trace deployer wallet funding sources."""
    from app.domains.scanner.degen_security_scanner.models import FundingTrace
    from app.free_solscan_client import is_known_exchange

    if not report.deployer:
        creation_tx = scanner.solscan.get_token_creation_tx(report.token_address)
        if creation_tx:
            signers = creation_tx.get("signer", [])
            if signers:
                report.deployer = signers[0]

    if not report.deployer:
        logger.warning("Could not determine deployer address")
        return

    funding_sources = scanner.solscan.get_wallet_funding_sources(report.deployer, days=30)
    scanner.solscan.top_address_transfers(report.deployer, range_days=30)

    exchange_count = 0
    organic_count = 0
    mixer_detected = False
    trace = FundingTrace(wallet=report.deployer, initial_funder="")

    for source in funding_sources[:20]:
        from_addr = source.get("from", "")
        exchange = is_known_exchange(from_addr)

        if exchange:
            exchange_count += 1
            if not trace.is_exchange_direct:
                trace.is_exchange_direct = True
                trace.exchange_name = exchange
                trace.initial_funder = from_addr
                trace.funder_tag = exchange
                trace.funding_amount_sol = source.get("amount", 0)
                trace.funding_time = source.get("time")
                trace.hops_from_exchange = 0
        else:
            organic_count += 1
            if not trace.initial_funder:
                trace.initial_funder = from_addr
                trace.funding_amount_sol = source.get("amount", 0)

        amount = source.get("amount", 0)
        if isinstance(amount, (int, float)) and amount in [0.1, 0.2, 0.5, 1.0]:
            trace.risk_flags.append(f"Suspicious round amount: {amount} SOL")

    total_sources = exchange_count + organic_count
    if total_sources > 0:
        report.exchange_funding_percentage = round(exchange_count / total_sources * 100, 1)
        report.organic_funding_percentage = round(organic_count / total_sources * 100, 1)

    report.deployer_funding = trace
    report.mixer_funding_detected = mixer_detected

    if trace.hops_from_exchange == 0 and exchange_count > 0:
        report.funding_risk = "LOW"
    elif organic_count > exchange_count * 2:
        report.funding_risk = "MEDIUM"
        scanner.critical_warnings.append(
            "\u26a0\ufe0f Deployer funded from non-exchange sources - possible obfuscation"
        )
    elif mixer_detected:
        report.funding_risk = "CRITICAL"
        scanner.critical_warnings.append("\U0001f6a8 MIXER FUNDING DETECTED: Deployer used a tumbler!")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5: BUNDLER & SNIPER DETECTION
# ═══════════════════════════════════════════════════════════════════════════


async def phase5_bundlers_snipers(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Detect launch bundlers and sniper wallets."""
    from app.domains.scanner.degen_security_scanner.models import SniperProfile

    activities = scanner.solscan.token_defi_activities(report.token_address, page_size=200)
    if not activities:
        return

    sorted_acts = sorted(activities, key=lambda x: x.get("block_time", 0))
    if not sorted_acts:
        return

    launch_time = sorted_acts[0].get("block_time", 0)
    if launch_time:
        report.created_at = datetime.fromtimestamp(launch_time).isoformat()

    sniper_window_ms = 30000
    sniper_candidates: list[SniperProfile] = []
    buyer_timestamps: dict[str, list[dict]] = {}

    for act in sorted_acts[:100]:
        act_type = act.get("activity_type", "").upper()
        if "BUY" not in act_type and "SWAP" not in act_type:
            continue

        buyer = act.get("from_address") or act.get("address")
        block_time = act.get("block_time", 0)
        time_after_launch_ms = (block_time - launch_time) * 1000 if launch_time else 0

        if buyer:
            if buyer not in buyer_timestamps:
                buyer_timestamps[buyer] = []
            buyer_timestamps[buyer].append(
                {
                    "time": block_time,
                    "amount_sol": act.get("value", 0),
                    "tx": act.get("trans_id"),
                }
            )

        if 0 <= time_after_launch_ms <= sniper_window_ms and buyer:
            sniper = SniperProfile(
                wallet=buyer,
                buy_time_ms=round(time_after_launch_ms, 0),
                buy_amount_sol=act.get("value", 0),
                token_amount=act.get("token_amount", 0),
                price_paid=act.get("value", 0) / max(act.get("token_amount", 1), 1),
            )
            sniper_candidates.append(sniper)

    bundler_clusters: list[str] = []
    for wallet, buys in buyer_timestamps.items():
        if len(buys) >= 2:
            timestamps = sorted(b["time"] for b in buys)
            for i in range(len(timestamps) - 1):
                if timestamps[i + 1] - timestamps[i] <= 2:
                    bundler_clusters.append(wallet)
                    break

    launch_window_buys: dict[int, list[str]] = defaultdict(list)
    for wallet, buys in buyer_timestamps.items():
        for b in buys:
            window_key = int(b["time"] / 3) * 3
            launch_window_buys[window_key].append(wallet)

    for _window, wallets in launch_window_buys.items():
        if len(wallets) >= 5:
            bundler_clusters.extend(wallets)

    report.bundler_wallets = list(set(bundler_clusters))
    report.bundler_detected = len(report.bundler_wallets) > 0

    if report.bundler_detected:
        bundler_supply_pct = len(report.bundler_wallets) * 0.5
        report.bundler_percentage_of_supply = min(bundler_supply_pct, 100)
        report.bundler_pattern = "synchronized_launch" if len(report.bundler_wallets) >= 5 else "clustered_buys"
        report.bundler_risk = "HIGH" if len(report.bundler_wallets) >= 10 else "MEDIUM"
        scanner.critical_warnings.append(
            f"\U0001f3af BUNDLER DETECTED: {len(report.bundler_wallets)} wallets bought in coordinated pattern!"
        )

    report.snipers = sniper_candidates[:20]
    report.sniper_count = len(sniper_candidates)
    if sniper_candidates:
        report.avg_sniper_entry_time_ms = round(statistics.mean(s.buy_time_ms for s in sniper_candidates), 0)
        report.sniper_volume_sol = round(sum(s.buy_amount_sol for s in sniper_candidates), 2)

    if report.sniper_count >= 15:
        report.sniper_risk = "HIGH"
    elif report.sniper_count >= 8:
        report.sniper_risk = "MEDIUM"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6: LIQUIDITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════


async def phase6_liquidity(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Analyze LP depth, lock status, and concentration."""
    from app.domains.scanner.degen_security_scanner.models import LPAnalysis

    lp_data = scanner.solscan.get_lp_token_accounts(report.token_address)

    total_liq = 0.0
    for lp in lp_data:
        tvl = lp.get("tvl_usd", 0) or 0
        total_liq += tvl

        analysis = LPAnalysis(
            pool_address=lp.get("market_id", ""),
            dex_name=lp.get("program", "Unknown"),
            token_a_amount=lp.get("liquidity_a", 0) or 0,
            token_b_amount=lp.get("liquidity_b", 0) or 0,
            tvl_usd=tvl,
            lp_tokens_total=0,
            lp_tokens_locked=0,
        )

        if lp.get("lp_locked"):
            analysis.lp_locked_percentage = 100.0
            analysis.is_burned = lp.get("lpLocked") == "burned"
        else:
            analysis.lp_locked_percentage = 0.0

        if tvl > 100000:
            analysis.depth_score = 90
            analysis.concentration_risk = "LOW"
        elif tvl > 50000:
            analysis.depth_score = 70
            analysis.concentration_risk = "MEDIUM"
        elif tvl > 10000:
            analysis.depth_score = 50
            analysis.concentration_risk = "HIGH"
        else:
            analysis.depth_score = 20
            analysis.concentration_risk = "CRITICAL"

        report.liquidity_pools.append(analysis)

    report.total_liquidity_usd = total_liq

    locked_pools = sum(1 for p in report.liquidity_pools if p.lp_locked_percentage > 80)
    total_pools = len(report.liquidity_pools)

    if total_pools == 0:
        report.lp_risk = "CRITICAL"
        scanner.critical_warnings.append("\U0001f6a8 NO LIQUIDITY POOLS FOUND!")
    elif locked_pools == 0:
        report.lp_risk = "CRITICAL"
        scanner.critical_warnings.append("\U0001f6a8 LP NOT LOCKED: Dev can rug liquidity at any moment!")
    elif total_liq < 10000:
        report.lp_risk = "HIGH"
        scanner.critical_warnings.append(f"\u26a0\ufe0f LOW LIQUIDITY: Only ${total_liq:,.0f} TVL")
    elif total_liq < 50000:
        report.lp_risk = "MEDIUM"
    else:
        report.lp_risk = "LOW"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7: ADVANCED DETECTION
# ═══════════════════════════════════════════════════════════════════════════


async def phase7_advanced(scanner: DegenSecurityScanner, report: SecurityReport, quick: bool = False) -> None:
    """Insider trading, wash trading, dev history, honeypot detection."""
    await _detect_insiders(scanner, report)
    if not quick:
        await _detect_wash_trading(scanner, report)
    await _analyze_dev_history(scanner, report)
    await _detect_tax_and_honeypot(scanner, report)


async def _detect_insiders(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Detect wallets that bought suspiciously early."""
    activities = scanner.solscan.token_defi_activities(report.token_address, page_size=100)
    if not activities:
        return

    sorted_acts = sorted(activities, key=lambda x: x.get("block_time", 0))
    if not sorted_acts:
        return

    launch_time = sorted_acts[0].get("block_time", 0)
    insider_window = 120

    insiders: list[str] = []
    for act in sorted_acts:
        act_type = str(act.get("activity_type", "")).upper()
        if "BUY" not in act_type and "SWAP" not in act_type:
            continue

        block_time = act.get("block_time", 0)
        elapsed = block_time - launch_time

        if 0 <= elapsed <= insider_window:
            buyer = act.get("from_address") or act.get("address")
            if buyer and buyer not in insiders:
                acct_info = scanner.solscan.account_info(buyer)
                if acct_info:
                    insiders.append(buyer)

    report.insiders_detected = insiders[:10]
    if len(insiders) >= 5:
        report.insider_risk = "HIGH"
        scanner.critical_warnings.append(
            f"\U0001f575\ufe0f INSIDER TRADING: {len(insiders)} wallets bought within 2min of launch"
        )
    elif len(insiders) >= 2:
        report.insider_risk = "MEDIUM"


async def _detect_wash_trading(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Detect circular trading patterns."""
    from app.domains.scanner.degen_security_scanner.models import WashTradeCluster

    _ = scanner.solscan.token_transfer_summary(report.token_address)
    recent = scanner.solscan.account_transfers(report.token_address, page_size=200)
    if not recent:
        return

    graph: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tx in recent[:200]:
        from_addr = tx.get("from_address", "")
        to_addr = tx.get("to_address", "")
        amount = float(tx.get("change_amount", 0))
        if from_addr and to_addr and amount > 0:
            graph[from_addr][to_addr] += 1

    clusters: list[WashTradeCluster] = []
    visited: set[str] = set()
    for wallet_a in list(graph.keys())[:50]:
        if wallet_a in visited:
            continue
        for wallet_b, count_ab in graph[wallet_a].items():
            if wallet_b in graph and wallet_a in graph[wallet_b]:
                count_ba = graph[wallet_b][wallet_a]
                if count_ab >= 2 and count_ba >= 2:
                    cluster = WashTradeCluster(
                        wallets=[wallet_a, wallet_b],
                        trade_count=count_ab + count_ba,
                        volume_usd=0,
                        pattern_type="back_forth",
                        confidence=min(0.95, (count_ab + count_ba) / 10),
                    )
                    clusters.append(cluster)
                    visited.add(wallet_a)
                    visited.add(wallet_b)

    report.wash_trade_clusters = clusters[:5]
    if clusters:
        report.wash_trading_volume_usd = sum(c.volume_usd for c in clusters)
        total_volume = report.volume_24h or 1
        wash_pct = report.wash_trading_volume_usd / total_volume * 100

        if wash_pct > 50:
            report.wash_trading_risk = "CRITICAL"
            scanner.critical_warnings.append(f"\U0001f504 WASH TRADING: {wash_pct:.0f}% of volume appears artificial!")
        elif wash_pct > 20:
            report.wash_trading_risk = "HIGH"
        elif wash_pct > 5:
            report.wash_trading_risk = "MEDIUM"


async def _analyze_dev_history(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Check deployer's history for other tokens and potential rugs."""
    if not report.deployer:
        return

    txs = scanner.solscan.get_deployer_transactions(report.deployer, pages=3)

    token_creations: list[str] = []
    for tx in txs:
        tx_hash = tx.get("txHash", "")
        if not tx_hash:
            continue

        detail = scanner.solscan.transaction_detail(tx_hash)
        if detail and "Tokenkeg" in str(detail):
            programs = detail.get("parsedInstructions", [])
            for inst in programs:
                if "initializeMint" in str(inst):
                    token_creations.append(tx_hash)

    report.dev_other_tokens = token_creations[:10]

    if len(token_creations) >= 5:
        report.dev_risk = "HIGH"
        report.dev_is_known_scammer = True
        report.dev_rug_history = len(token_creations) - 1
        scanner.critical_warnings.append(
            f"\U0001f47a DEV SERIAL LAUNCHER: Created {len(token_creations)} tokens - "
            f"likely a token factory / rug operation"
        )
    elif len(token_creations) >= 2:
        report.dev_risk = "MEDIUM"


async def _detect_tax_and_honeypot(scanner: DegenSecurityScanner, report: SecurityReport) -> None:
    """Attempt to detect taxes and honeypot behavior."""
    activities = scanner.solscan.token_defi_activities(report.token_address, page_size=100)
    if not activities:
        return

    buys: list[float] = []
    sells: list[float] = []
    for act in activities:
        act_type = str(act.get("activity_type", "")).upper()
        value = act.get("value", 0)
        if "BUY" in act_type or "IN" in act_type:
            buys.append(value)
        elif "SELL" in act_type or "OUT" in act_type:
            sells.append(value)

    if len(buys) > 10 and len(sells) < 2:
        report.honeypot_detected = True
        report.can_sell = False
        scanner.critical_warnings.append("\U0001f36f HONEYPOT DETECTED: Many buys but almost no sells!")
        report.tax_risk = "CRITICAL"
    elif len(buys) > 10 and len(sells) > 0:
        avg_buy = statistics.mean(buys) if buys else 0
        avg_sell = statistics.mean(sells) if sells else 0
        if avg_buy > 0 and avg_sell > 0:
            ratio = avg_sell / avg_buy
            if ratio < 0.5:
                estimated_tax = (1 - ratio) * 100
                report.sell_tax = round(estimated_tax, 1)
                if estimated_tax > 20:
                    report.tax_risk = "HIGH"
                    scanner.critical_warnings.append(f"\U0001f4b8 HEAVY SELL TAX ESTIMATED: ~{estimated_tax:.0f}%")


# ═══════════════════════════════════════════════════════════════════════════
# EXTERNAL API HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _helius_token_metadata(scanner: DegenSecurityScanner, token_address: str) -> dict | None:
    """Fetch token metadata from Helius."""
    if not scanner.helius_key:
        return None
    try:
        url = f"https://mainnet.helius-rpc.com/?api-key={scanner.helius_key}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAsset",
            "params": {"id": token_address},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            result = resp.json().get("result")
            if result:
                return {
                    "name": result.get("content", {}).get("metadata", {}).get("name"),
                    "symbol": result.get("content", {}).get("metadata", {}).get("symbol"),
                    "decimals": result.get("token_info", {}).get("decimals", 0),
                    "mintAuthority": result.get("mint_extensions", {}).get("mintAuthority"),
                    "freezeAuthority": result.get("mint_extensions", {}).get("freezeAuthority"),
                }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Helius metadata failed: {e}")
    return None


async def _birdeye_token_data(scanner: DegenSecurityScanner, token_address: str) -> dict | None:
    """Fetch market data from Birdeye."""
    if not scanner.birdeye_key:
        return None
    try:
        headers = {"X-API-KEY": scanner.birdeye_key}
        async with httpx.AsyncClient(timeout=10) as client:
            price_resp = await client.get(
                f"https://public-api.birdeye.so/defi/price?address={token_address}",
                headers=headers,
            )
            overview_resp = await client.get(
                f"https://public-api.birdeye.so/defi/token_overview?address={token_address}",
                headers=headers,
            )

        result: dict[str, float | int] = {}
        if price_resp.status_code == 200:
            p_data = price_resp.json().get("data", {})
            result["price"] = p_data.get("value", 0)
            result["priceChange24h"] = p_data.get("priceChange24h", 0)

        if overview_resp.status_code == 200:
            o_data = overview_resp.json().get("data", {})
            result["mc"] = o_data.get("mc", 0)
            result["v24hUSD"] = o_data.get("v24hUSD", 0)
            result["liquidity"] = o_data.get("liquidity", 0)
            result["buySellRatio"] = o_data.get("buySellRatio", 1.0)
            result["holders"] = o_data.get("holder", 0)

        return result if result else None
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Birdeye data failed: {e}")
    return None
