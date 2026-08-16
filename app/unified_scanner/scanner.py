"""
Core wallet scan logic: scan_wallet() and risk scoring.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.core.http import (  # noqa: E402 — imported after logger init; kept below module header for readability
    get_http_client,
)
from app.unified_scanner.cache import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _cache_get,
    _cache_set,
)
from app.unified_scanner.helius import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _fetch_etherscan_wallet,
    _fetch_helius_wallet,
    _fetch_moralis_wallet,
)
from app.unified_scanner.models import WalletRiskFactors  # noqa: E402 — module docstring precedes import block
from app.unified_scanner.providers import (  # noqa: E402 — module docstring precedes import block
    _calculate_basic_pnl,
    _check_hapi_risk,
    _check_misttrack_light,
    _check_trm_sanctions_wallet,
    _check_wallet_labels_scam,
    _close_data_flywheel,
    _fetch_sentinel_labels,
    _fetch_wallet_memory,
    _resolve_entity_free,
    _scan_held_tokens,
    _trace_funding_source,
)


async def _fetch_wallet_chain_data(address: str, chain: str) -> dict[str, Any]:
    """Fetch on-chain wallet data (Helius/Moralis/Etherscan) and DexScreener metrics.

    Args:
        address: wallet address to scan.
        chain: chain name ("solana", "ethereum", ...).

    Returns:
        dict with:
          - onchain: raw chain data dict (empty dict if unavailable)
          - token_list: held tokens discovered in on-chain data
          - dexscreener: optional dict with total_volume_usd/dex_swaps/token_launches_participated
          - data_sources: names of data sources that returned data
    """
    onchain: dict[str, Any] = {}
    data_sources: list[str] = []
    token_list: list[Any] = []

    # ── On-chain data ──
    if chain == "solana":
        onchain = await _fetch_helius_wallet(address)
        if not onchain:
            try:
                client = await get_http_client()
                resp = await client.post(
                    "https://api.mainnet-beta.solana.com",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getBalance",
                        "params": [address],
                    },
                )
                if resp.status_code == 200:
                    bal = resp.json().get("result", {}).get("value", 0)
                    onchain = {
                        "sol_balance": bal / 1e9 if bal else 0,
                        "data_sources": ["solana_public"],
                    }
            except (httpx.HTTPError, TimeoutError, OSError):
                pass
    else:
        onchain = await _fetch_moralis_wallet(address, chain)
        etherscan = await _fetch_etherscan_wallet(address, chain)
        if etherscan and not onchain.get("wallet_age_days"):
            onchain["wallet_age_days"] = etherscan.get("wallet_age_days", 0)
            onchain["data_sources"] = onchain.get("data_sources", []) + etherscan.get("data_sources", [])

    if onchain:
        token_list = onchain.get("tokens", [])
        data_sources.extend(onchain.get("data_sources", []))

    # ── DexScreener volume (shared httpx client) ──
    dexscreener: dict[str, Any] | None = None
    try:
        client = await get_http_client()
        resp = await client.get("https://api.dexscreener.com/latest/dex/search", params={"q": address})
        if resp.status_code == 200:
            pairs = resp.json().get("pairs", [])
            if pairs:
                dexscreener = {
                    "total_volume_usd": sum(float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs),
                    "dex_swaps": len(pairs),
                    "token_launches_participated": sum(
                        1
                        for p in pairs
                        if p.get("pairCreatedAt")
                        and (
                            datetime.now(UTC) - datetime.fromtimestamp(p.get("pairCreatedAt", 0) / 1000, tz=UTC)
                        ).total_seconds()
                        / 3600
                        < 24
                    ),
                }
                data_sources.append("dexscreener")
    except (httpx.HTTPError, TimeoutError, OSError):
        pass

    return {
        "onchain": onchain,
        "token_list": token_list,
        "dexscreener": dexscreener,
        "data_sources": data_sources,
    }


async def _fetch_wallet_labels(address: str, chain: str) -> dict[str, Any]:
    """Fetch wallet threat-intelligence labels from SENTINEL, ClickHouse, HAPI, MistTrack, TRM.

    Args:
        address: wallet address to scan.
        chain: chain name used by the label providers.

    Returns:
        dict with optional payloads (None when the source returned nothing):
          sentinel / wallet_labels / hapi / misttrack / trm, plus a data_sources list.
    """
    data_sources: list[str] = []

    # ── SENTINEL address labels ──
    sentinel: dict[str, Any] | None = None
    sentinel_labels = await _fetch_sentinel_labels(address, chain)
    if sentinel_labels:
        sentinel = {
            "category": sentinel_labels.get("category", ""),
            "is_scammer": "scam" in str(sentinel_labels).lower(),
            "is_sanctioned": ("sanction" in str(sentinel_labels).lower() or "ofac" in str(sentinel_labels).lower()),
            "is_exchange": "exchange" in str(sentinel_labels).lower() or "cex" in str(sentinel_labels).lower(),
            "is_bot": "bot" in str(sentinel_labels).lower() or "mev" in str(sentinel_labels).lower(),
            "is_whale": "whale" in str(sentinel_labels).lower(),
            "is_insider": "insider" in str(sentinel_labels).lower(),
            "risk_score": sentinel_labels.get("risk_score", 50),
        }
        data_sources.append("sentinel_labels")

    # ── FREE: Wallet Labels (ClickHouse) ──
    wallet_labels: dict[str, Any] | None = None
    labels_data = await _check_wallet_labels_scam(address, chain)
    if labels_data:
        threats = labels_data.get("threats", [])
        wallet_labels = {
            "is_scammer": "known_scam" in threats,
            "is_mixer": "mixer" in threats,
            "is_sanctioned": "sanctioned" in threats,
            "label": labels_data.get("label", ""),
        }
        data_sources.append("wallet_labels")

    # ── FREE: External threat intelligence ──
    hapi: dict[str, Any] | None = None
    hapi_risk = await _check_hapi_risk(address, chain)
    if hapi_risk:
        hapi = {"is_malicious": bool(hapi_risk.get("is_malicious"))}
        data_sources.append("hapi")

    misttrack: dict[str, Any] | None = None
    misttrack_data = await _check_misttrack_light(address, chain)
    if misttrack_data:
        misttrack = {"is_scammer": misttrack_data.get("risk_score", 0) > 70}
        data_sources.append("misttrack")

    trm: dict[str, Any] | None = None
    trm_data = await _check_trm_sanctions_wallet(address)
    if trm_data:
        trm = {"is_sanctioned": bool(trm_data.get("is_sanctioned"))}
        data_sources.append("trm_labs")

    return {
        "sentinel": sentinel,
        "wallet_labels": wallet_labels,
        "hapi": hapi,
        "misttrack": misttrack,
        "trm": trm,
        "data_sources": data_sources,
    }


def _compute_wallet_risk(factors: WalletRiskFactors, data_sources: list[str]) -> list[str]:
    """Compute wallet risk score, flags, and confidence from accumulated scan factors.

    Args:
        factors: WalletRiskFactors populated by the scan pipeline.
        data_sources: names of data sources that returned data this scan.

    Returns:
        list of risk flags. Also updates factors.total_risk_score,
        factors.risk_category, factors.confidence, factors.data_sources.

    [REVIEW] Signature differs from the requested (chain_data, labels): risk scoring
    consumes the fully-populated factors object (tier intel, funding trace, token scan,
    PnL, cluster data), not just chain data + labels.
    """
    risk = 0
    flags: list[str] = []

    if factors.is_labeled_sanctioned:
        risk += 50
        flags.append("SANCTIONED")
    if factors.is_labeled_scammer:
        risk += 45
        flags.append("KNOWN_SCAMMER")
    if factors.is_labeled_insider:
        risk += 25
        flags.append("INSIDER_LABELED")
    if factors.is_labeled_bot:
        risk += 10
        flags.append("BOT_WALLET")
    if factors.is_labeled_exchange:
        risk -= 10

    if factors.funding_source_is_mixer:
        risk += 25
        flags.append("MIXER_FUNDING")
    if factors.funding_from_sanctioned:
        risk += 40
        flags.append("SANCTIONED_FUNDING")
    if factors.tornado_cash_exposure:
        risk += 35
        flags.append("TORNADO_CASH")

    if factors.wallet_age_days > 0:
        if factors.wallet_age_days < 1:
            risk += 15
            flags.append("FRESH_WALLET")
        elif factors.wallet_age_days < 7 and factors.total_volume_usd > 100000:
            risk += 20
            flags.append("NEW_WALLET_HIGH_VOLUME")
    elif factors.total_volume_usd > 50000:
        risk += 10
        flags.append("UNVERIFIED_HIGH_VOLUME")

    if factors.honeypot_tokens_owned > 0:
        risk += 25
        flags.append(f"HOLDS_SCAM_TOKENS_{factors.honeypot_tokens_owned}")
    if factors.avg_held_token_safety and factors.avg_held_token_safety < 40:
        risk += 15
        flags.append("LOW_QUALITY_PORTFOLIO")

    if factors.rug_pull_launcher_count > 0:
        risk += 50
        flags.append("RUG_PULL_LAUNCHER")
    if factors.honeypot_tokens_owned > 0:
        risk += 20
        flags.append("HONEYPOT_HOLDER")
    if factors.sniper_activity_score > 70:
        risk += 20
        flags.append("HIGH_SNIPER")
    if factors.sandwich_attacks_executed > 0:
        risk += 25
        flags.append("SANDWICH_ATTACKER")
    if factors.flash_loan_usage > 10:
        risk += 15
        flags.append("HEAVY_FLASH_LOAN")

    if factors.insider_trading_confidence > 70:
        risk += 30
        flags.append("INSIDER_TRADING")
    if factors.wash_trading_score > 60:
        risk += 20
        flags.append("WASH_TRADING")
    if factors.pump_and_dump_participant:
        risk += 20
        flags.append("PUMP_DUMP_PARTICIPANT")

    if factors.cluster_size > 5 and factors.cluster_shared_funding:
        risk += 20
        flags.append("LARGE_SHARED_CLUSTER")
    if factors.known_cluster_type in ("bot_farm", "sniper_ring"):
        risk += 15
        flags.append("SUSPICIOUS_CLUSTER")

    if factors.sybil_score > 70:
        risk += 15
        flags.append("SYBIL_DETECTED")

    if factors.highest_risk_token:
        ts = factors.highest_risk_token
        if ts.get("safety_score", 100) < 25:
            risk += 15
            flags.append(f"HOLDS_CRITICAL_TOKEN_{ts.get('symbol', '???')}")

    factors.total_risk_score = min(100, max(0, risk))
    factors.risk_category = (
        "critical"
        if risk >= 80
        else "high_risk"
        if risk >= 60
        else "medium"
        if risk >= 35
        else "low_risk"
        if risk >= 15
        else "safe"
    )

    confidence = 15
    if "helius" in data_sources:
        confidence += 20
    if "sentinel_labels" in data_sources:
        confidence += 20
    if "dexscreener" in data_sources:
        confidence += 10
    if "wallet_memory" in data_sources:
        confidence += 15
    if "gmgn" in data_sources:
        confidence += 15
    confidence += min(len(flags) * 2, 15)
    factors.confidence = min(100, confidence)
    factors.data_sources = data_sources

    return flags


async def scan_wallet(wallet_address: str, chain: str = "solana", tier: str = "free") -> dict[str, Any]:
    """Unified wallet scanner - SENTINEL + Helius/Moralis/Etherscan pool + Wallet Memory + Token Scanner."""
    import time

    cache_key = hashlib.sha256(f"{chain}:{wallet_address}:{tier}".encode()).hexdigest()[:16]
    cached = await _cache_get(cache_key)
    if cached:
        cached["cached"] = True
        logger.info(f"Wallet scan {wallet_address[:12]}... [{chain}] = CACHED")
        return cached

    factors = WalletRiskFactors()
    start = time.time()
    data_sources: list[str] = []
    token_list: list[Any] = []
    modules_run: list[dict[str, Any]] = []

    # ── On-chain data + DexScreener metrics ──
    chain_data = await _fetch_wallet_chain_data(wallet_address, chain)
    onchain = chain_data["onchain"]
    token_list = chain_data["token_list"]
    if onchain:
        factors.wallet_age_days = onchain.get("wallet_age_days", 0)
        factors.total_tx_count = onchain.get("tx_count", onchain.get("tx_count_sample", 0))
        factors.current_balance_usd = onchain.get("sol_balance", onchain.get("native_balance", 0))
        factors.unique_tokens_held = onchain.get("token_count", len(token_list))
        if onchain.get("last_tx"):
            last_ts = onchain["last_tx"]
            if isinstance(last_ts, (int, float)):
                factors.days_since_last_tx = int((datetime.now(UTC).timestamp() - last_ts) / 86400)
    dex = chain_data["dexscreener"]
    if dex:
        factors.total_volume_usd = dex["total_volume_usd"]
        factors.dex_swaps = dex["dex_swaps"]
        factors.token_launches_participated = dex["token_launches_participated"]
    data_sources.extend(chain_data["data_sources"])

    # ── Threat-intelligence labels (SENTINEL, ClickHouse, HAPI, MistTrack, TRM) ──
    label_data = await _fetch_wallet_labels(wallet_address, chain)

    sentinel = label_data["sentinel"]
    if sentinel:
        factors.entity_label = sentinel.get("category", "")
        factors.is_labeled_scammer = factors.is_labeled_scammer or sentinel["is_scammer"]
        factors.is_labeled_sanctioned = factors.is_labeled_sanctioned or sentinel["is_sanctioned"]
        factors.is_labeled_exchange = sentinel["is_exchange"]
        factors.is_labeled_bot = sentinel["is_bot"]
        factors.is_labeled_whale = sentinel["is_whale"]
        factors.is_labeled_insider = sentinel["is_insider"]
        factors.entity_confidence = max(factors.entity_confidence, sentinel.get("risk_score", 50))

    wallet_labels = label_data["wallet_labels"]
    if wallet_labels:
        if wallet_labels["is_scammer"]:
            factors.is_labeled_scammer = True
            factors.scam_token_exposure = max(factors.scam_token_exposure, 1)
            factors.entity_label = wallet_labels.get("label") or factors.entity_label
        if wallet_labels["is_mixer"]:
            factors.tornado_cash_exposure = True
        if wallet_labels["is_sanctioned"]:
            factors.is_labeled_sanctioned = True

    hapi = label_data["hapi"]
    if hapi and hapi["is_malicious"]:
        factors.is_labeled_scammer = True
        factors.scam_token_exposure = max(factors.scam_token_exposure, 1)

    misttrack = label_data["misttrack"]
    if misttrack and misttrack["is_scammer"]:
        factors.is_labeled_scammer = True

    trm = label_data["trm"]
    if trm and trm["is_sanctioned"]:
        factors.is_labeled_sanctioned = True
        factors.funding_from_sanctioned = True

    data_sources.extend(label_data["data_sources"])

    # ── PRO: Wallet Memory Bank ──
    if tier in ("pro", "elite"):
        wallet_memory = await _fetch_wallet_memory(wallet_address, chain)
        if wallet_memory:
            factors.linked_wallets_count = len(wallet_memory.get("linked_wallets", []))
            factors.cluster_size = wallet_memory.get("linked_wallets_count", factors.linked_wallets_count)
            factors.cluster_id = wallet_memory.get("entity_id", "")
            factors.cross_chain_funding = bool(wallet_memory.get("cross_chain_presence"))
            scam_assoc = wallet_memory.get("scam_associations", [])
            if scam_assoc:
                factors.is_labeled_scammer = True
                factors.scam_token_exposure = len(scam_assoc)
            hist = wallet_memory.get("deployer_history", {}) or {}
            factors.rug_pull_launcher_count = hist.get("rugs", 0)
            data_sources.append("wallet_memory")

        try:
            from app.databus.gmgn_provider import get_gmgn_provider

            gmgn = get_gmgn_provider()
            intel = await gmgn.get_wallet_intelligence(wallet_address, chain)
            if intel:
                factors.insider_trading_confidence = float(intel.get("insider_score", 0))
                factors.sniper_activity_score = float(intel.get("sniper_score", 0))
                factors.wash_trading_score = float(intel.get("wash_trading_score", 0))
                factors.pump_and_dump_participant = intel.get("pump_dump_participant", False)
                factors.new_token_flipper = intel.get("flipper", False)
                pnl = intel.get("pnl", {}) or {}
                factors.realized_pnl_usd = float(pnl.get("realized", 0))
                factors.unrealized_pnl_usd = float(pnl.get("unrealized", 0))
                factors.win_rate = float(intel.get("win_rate", 0))
                data_sources.append("gmgn")
        except (ImportError, AttributeError, httpx.HTTPError):
            pass

        # NOTE: the entity-cluster block below was removed 2026-08 —
        # ``EntityClusteringEngine`` has no ``find_cluster`` method, so the
        # guarded call was always skipped by AttributeError and cluster
        # factors never populated. Wallet cluster data comes from the
        # wallet_memory block above; add a real engine method first.

    # ── FREE: Token portfolio risk scan ──
    token_scan = await _scan_held_tokens(token_list, chain)
    if token_scan:
        factors.held_token_risks = token_scan.get("risks", [])
        factors.highest_risk_token = token_scan.get("highest_risk")
        high_risk_count = token_scan.get("high_risk_count", 0)
        factors.honeypot_tokens_owned = high_risk_count
        factors.avg_held_token_safety = token_scan.get("avg_safety", 0)

        deployers = token_scan.get("deployers_to_flag", [])
        if deployers:
            asyncio.create_task(_close_data_flywheel(deployers))

    # ── FREE: Funding source tracing ──
    funding = await _trace_funding_source(wallet_address, chain)
    if funding:
        factors.funding_source = funding.get("source", factors.funding_source)
        factors.funding_source_label = funding.get("source_label", factors.funding_source_label)
        factors.funding_source_is_cex = funding.get("is_cex", False)
        factors.funding_source_is_mixer = funding.get("is_mixer", False)
        factors.funding_hop_count = funding.get("hops", 0)
        data_sources.append("funding_trace")

    free_entity = await _resolve_entity_free(wallet_address, chain)
    if free_entity and free_entity.get("label") != "unknown":
        if not factors.entity_label:
            factors.entity_label = free_entity["label"]
        if not factors.entity_confidence:
            factors.entity_confidence = 60
        data_sources.append("free_entity")

    native_bal = onchain.get("sol_balance", onchain.get("native_balance", 0)) if onchain else 0
    basic_pnl = await _calculate_basic_pnl(wallet_address, chain, token_list, native_bal)
    if basic_pnl:
        factors.current_balance_usd = max(factors.current_balance_usd or 0, basic_pnl.get("estimated_value_usd", 0))
        factors.realized_pnl_usd = factors.realized_pnl_usd or 0
        data_sources.append("jupiter_pnl")

    # ── ELITE: ML anomaly + portfolio tracker ──
    if tier == "elite":
        # NOTE: the guarded ``detect_wallet_anomaly`` import was removed
        # 2026-08 — ``app.ml_anomaly`` exposes the ``WalletAnomalyDetector``
        # class (sync stub returning zeros, no ``sybil_score``), not this
        # function; the block never ran and would have fed fake scores.
        try:
            from app.portfolio_tracker import get_portfolio

            portfolio = get_portfolio(wallet_address, chain)  # sync by design
            if portfolio:
                factors.current_balance_usd = max(
                    factors.current_balance_usd, float(portfolio.get("total_value_usd", 0))
                )
                factors.portfolio_diversity_score = portfolio.get("diversity_score", 0)
                factors.stablecoin_ratio = portfolio.get("stablecoin_pct", 0)
                factors.bluechip_ratio = portfolio.get("bluechip_pct", 0)
                factors.memecoin_ratio = portfolio.get("memecoin_pct", 0)
        except (ImportError, AttributeError):
            pass

    # ═══════════════════════════════════════════
    # Risk scoring
    # ═══════════════════════════════════════════
    flags = _compute_wallet_risk(factors, data_sources)

    for src in data_sources:
        modules_run.append({"module": src, "status": "ok"})

    # ── Record own-data coverage metrics ──
    try:
        from app.domains.scanner.metrics import record_token_scan_coverage

        record_token_scan_coverage(data_sources, modules_run)
    except ImportError:
        pass
    except Exception:  # noqa: S110 — optional dependency
        pass

    attempted_modules = [
        "helius_wallet",
        "moralis_wallet",
        "etherscan_wallet",
        "dexscreener",
        "rugcheck",
        "goplus_security",
        "wallet_labels",
        "wallet_memory",
        "sentinel_risk",
        "gmgn",
        "funding_trace",
        "free_entity",
        "jupiter_pnl",
        "entity_clustering",
    ]
    succeeded_set = set(data_sources)
    for mod in attempted_modules:
        if mod not in succeeded_set:
            modules_run.append({"module": mod, "status": "degraded", "error": "no_data_returned"})

    elapsed = time.time() - start
    logger.info(
        f"Wallet scan {wallet_address[:12]}... [{chain}] = {factors.total_risk_score}/100 "
        f"({factors.risk_category}) conf={factors.confidence}% in {elapsed:.1f}s"
    )

    result = {
        "wallet": wallet_address,
        "chain": chain,
        "risk_score": factors.total_risk_score,
        "risk_category": factors.risk_category,
        "risk_flags": flags[:20],
        "total_flags": len(flags),
        "confidence": factors.confidence,
        "tier": tier,
        "data_sources": factors.data_sources,
        "factors": {
            "wallet_age_days": factors.wallet_age_days,
            "total_tx_count": factors.total_tx_count,
            "total_volume_usd": factors.total_volume_usd,
            "unique_tokens_held": factors.unique_tokens_held,
            "funding_source": factors.funding_source,
            "funding_source_label": factors.funding_source_label,
            "funding_source_is_cex": factors.funding_source_is_cex,
            "funding_source_is_mixer": factors.funding_source_is_mixer,
            "funding_from_sanctioned": factors.funding_from_sanctioned,
            "funding_from_scammer": factors.funding_from_scammer,
            "is_labeled_scammer": factors.is_labeled_scammer,
            "is_labeled_sanctioned": factors.is_labeled_sanctioned,
            "is_labeled_exchange": factors.is_labeled_exchange,
            "is_labeled_bot": factors.is_labeled_bot,
            "is_labeled_whale": factors.is_labeled_whale,
            "is_labeled_insider": factors.is_labeled_insider,
            "entity_label": factors.entity_label,
            "entity_confidence": factors.entity_confidence,
            "cluster_size": factors.cluster_size,
            "cluster_shared_funding": factors.cluster_shared_funding,
            "known_cluster_type": factors.known_cluster_type,
            "linked_wallets_count": factors.linked_wallets_count,
            "cross_chain_funding": factors.cross_chain_funding,
            "tornado_cash_exposure": factors.tornado_cash_exposure,
            "sniper_activity_score": factors.sniper_activity_score,
            "insider_trading_confidence": factors.insider_trading_confidence,
            "wash_trading_score": factors.wash_trading_score,
            "pump_and_dump_participant": factors.pump_and_dump_participant,
            "sybil_score": factors.sybil_score,
            "rug_pull_launcher_count": factors.rug_pull_launcher_count,
            "honeypot_tokens_owned": factors.honeypot_tokens_owned,
            "sandwich_attacks_executed": factors.sandwich_attacks_executed,
            "flash_loan_usage": factors.flash_loan_usage,
            "realized_pnl_usd": factors.realized_pnl_usd,
            "unrealized_pnl_usd": factors.unrealized_pnl_usd,
            "win_rate": factors.win_rate,
            "current_balance_usd": factors.current_balance_usd,
            "portfolio_diversity_score": factors.portfolio_diversity_score,
            "stablecoin_ratio": factors.stablecoin_ratio,
            "bluechip_ratio": factors.bluechip_ratio,
            "memecoin_ratio": factors.memecoin_ratio,
            "dex_swaps": factors.dex_swaps,
            "cex_deposits": factors.cex_deposits,
            "cex_withdrawals": factors.cex_withdrawals,
            "token_launches_participated": factors.token_launches_participated,
        },
        "held_tokens": {
            "scanned": len(factors.held_token_risks),
            "highest_risk": factors.highest_risk_token,
            "risks": factors.held_token_risks[:10],
        },
        "modules_run": modules_run,
        "scanned_at": datetime.now(UTC).isoformat(),
        "tool_fingerprints": None,
    }

    asyncio.create_task(_cache_set(cache_key, result, ttl=3600))
    return result
