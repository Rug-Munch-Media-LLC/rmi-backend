"""
Wallet Behavioral Fingerprinting - Beyond Labels
================================================

Classifies wallets by HOW they trade, not just what labels they have.
Computes behavioral fingerprints from on-chain data and assigns
persona classifications.

Premium feature: "Smart Money Accumulator", "Meme Dumper", "Exit LP", etc.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("wallet.behavior")

# ──────────────────────────────────────────────────────────────
# Persona Definitions
# ──────────────────────────────────────────────────────────────

PERSONAS = {
    "smart_money_accumulator": {
        "name": "Smart Money Accumulator",
        "icon": "🧠",
        "description": "Buys early, holds long, sells at peaks. High win rate.",
        "signals": ["early_entry", "long_hold", "profit_taking_at_peak", "low_tx_frequency"],
        "risk_level": "low",
        "premium": True,
    },
    "meme_dumper": {
        "name": "Meme Dumper",
        "icon": "💩",
        "description": "Buys meme coin launches, dumps within hours. High velocity.",
        "signals": ["meme_only", "short_hold", "high_velocity", "sells_into_strength"],
        "risk_level": "high",
        "premium": True,
    },
    "exit_liquidity_provider": {
        "name": "Exit Liquidity Provider",
        "icon": "🚪",
        "description": "Provides exit liquidity for others. Buys tops, sells bottoms.",
        "signals": ["late_entry", "panic_sell", "buy_high_sell_low", "high_loss_rate"],
        "risk_level": "neutral",
        "premium": True,
    },
    "mev_extractor": {
        "name": "MEV Extractor",
        "icon": "🤖",
        "description": "Sandwich attacks, front-running, arbitrage. Bot-like patterns.",
        "signals": ["sandwich_pattern", "high_frequency", "arbitrage_loops", "zero_hold_time"],
        "risk_level": "neutral",
        "premium": True,
    },
    "insider_accumulator": {
        "name": "Insider Accumulator",
        "icon": "🔮",
        "description": "Buys tokens before public launch/announcement. Presale/sniper access.",
        "signals": [
            "pre_announcement_buy",
            "insider_wallet_links",
            "sniper_pattern",
            "multi_wallet",
        ],
        "risk_level": "high",
        "premium": True,
    },
    "whale_distributor": {
        "name": "Whale Distributor",
        "icon": "🐋",
        "description": "Large holder distributing to many wallets. Accumulation → distribution cycle.",
        "signals": ["large_balance", "distribution_pattern", "cex_deposits", "wallet_cluster"],
        "risk_level": "medium",
        "premium": True,
    },
    "bot_farm": {
        "name": "Bot Farm",
        "icon": "🤖",
        "description": "Automated trading across many wallets. Same patterns, different addresses.",
        "signals": [
            "identical_patterns",
            "multi_wallet_sync",
            "high_frequency",
            "no_human_variance",
        ],
        "risk_level": "high",
        "premium": True,
    },
    "retail_trader": {
        "name": "Retail Trader",
        "icon": "👤",
        "description": "Small balances, diverse tokens, irregular patterns. Normal behavior.",
        "signals": ["small_balance", "diverse_tokens", "irregular_timing", "human_variance"],
        "risk_level": "low",
        "premium": False,
    },
    "honeypot_victim": {
        "name": "Honeypot Victim",
        "icon": "🪤",
        "description": "Repeatedly buys scam/honeypot tokens. Pattern of trapped funds.",
        "signals": ["buys_honeypots", "trapped_funds", "no_sells_on_scam_tokens", "repeat_victim"],
        "risk_level": "high",
        "premium": True,
    },
}


@dataclass
class WalletFingerprint:
    """Complete behavioral fingerprint for a wallet."""

    address: str
    chain: str

    # Trading behavior
    avg_hold_hours: float = 0.0
    median_hold_hours: float = 0.0
    trade_frequency_per_day: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0  # % of trades profitable
    avg_profit_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0  # gross profit / gross loss

    # Timing
    first_trade_age_days: float = 0.0
    preferred_entry_timing: str = "unknown"  # "early" (first hour), "mid", "late"
    trades_by_hour: dict[int, int] = field(default_factory=dict)

    # Token preferences
    token_types: dict[str, int] = field(default_factory=dict)  # "meme"/"defi"/"stable"/"wrapped" → count
    preferred_chains: list[str] = field(default_factory=list)
    avg_token_age_at_entry_hours: float = 0.0  # How new are tokens when this wallet buys?

    # Risk indicators
    interacts_with_scams: bool = False
    honeypot_interactions: int = 0
    rug_interactions: int = 0
    sanction_exposure: bool = False

    # Network
    counterparty_count: int = 0
    cluster_size: int = 1
    funding_source_type: str = "unknown"  # "cex"/"dex"/"mixer"/"bridge"/"unknown"

    # Classification
    primary_persona: str = "retail_trader"
    persona_confidence: float = 0.0
    secondary_personas: list[dict] = field(default_factory=list)

    # Scores
    sophistication_score: float = 0.0  # 0-100, how sophisticated is this trader?
    risk_score: float = 0.0  # 0-100, how risky is interacting with this wallet?
    reliability_score: float = 0.0  # 0-100, how reliable/consistent is behavior?


def compute_fingerprint(wallet_data: dict[str, Any]) -> WalletFingerprint:
    """Compute behavioral fingerprint from wallet on-chain data."""
    address = wallet_data.get("address", "")
    chain = wallet_data.get("chain", "ethereum")

    fp = WalletFingerprint(address=address, chain=chain)

    # ── Extract transaction patterns ──
    txs = wallet_data.get("transactions", []) or []
    tokens_held = wallet_data.get("tokens", []) or []
    risk_factors = wallet_data.get("risk_factors", {}) or {}

    fp.total_trades = len(txs)

    if txs:
        # Hold time analysis
        hold_times = []
        profits = []
        losses = []

        for tx in txs:
            if isinstance(tx, dict):
                hold_s = tx.get("hold_seconds") or tx.get("hold_time_s", 0)
                if hold_s > 0:
                    hold_times.append(hold_s / 3600)

                pnl = tx.get("pnl_usd") or tx.get("realized_pnl", 0)
                if pnl > 0:
                    profits.append(pnl)
                elif pnl < 0:
                    losses.append(abs(pnl))

        if hold_times:
            fp.avg_hold_hours = sum(hold_times) / len(hold_times)
            hold_times.sort()
            fp.median_hold_hours = hold_times[len(hold_times) // 2]

        if profits or losses:
            total_profit = sum(profits)
            total_loss = sum(losses)
            fp.win_rate = len(profits) / len(profits + losses) if (profits or losses) else 0
            fp.avg_profit_pct = (sum(profits) / len(profits)) if profits else 0
            fp.avg_loss_pct = (sum(losses) / len(losses)) if losses else 0
            fp.profit_factor = total_profit / max(total_loss, 1)

        # Trade frequency
        if txs and len(txs) >= 2:
            timestamps = sorted([tx.get("timestamp", 0) for tx in txs if isinstance(tx, dict) and tx.get("timestamp")])
            if len(timestamps) >= 2:
                time_span_days = (max(timestamps) - min(timestamps)) / 86400
                if time_span_days > 0:
                    fp.trade_frequency_per_day = len(txs) / time_span_days

        # Hour distribution
        for tx in txs:
            if isinstance(tx, dict) and tx.get("timestamp"):
                hour = datetime.fromtimestamp(tx["timestamp"]).hour
                fp.trades_by_hour[hour] = fp.trades_by_hour.get(hour, 0) + 1

    # ── Token preferences ──
    for token in tokens_held:
        if isinstance(token, dict):
            ttype = token.get("type", token.get("category", "unknown"))
            fp.token_types[ttype] = fp.token_types.get(ttype, 0) + 1

    # ── Risk indicators from risk_factors ──
    fp.interacts_with_scams = risk_factors.get("interacts_with_scams", False)
    fp.honeypot_interactions = risk_factors.get("honeypot_interactions", 0)
    fp.rug_interactions = risk_factors.get("rug_interactions", 0)
    fp.sanction_exposure = risk_factors.get("sanction_exposure", False)
    fp.funding_source_type = risk_factors.get("funding_source", "unknown")
    fp.counterparty_count = risk_factors.get("counterparty_count", 0)
    fp.cluster_size = risk_factors.get("cluster_size", 1)

    # ── Entry timing preference ──
    entries = [tx for tx in txs if isinstance(tx, dict) and tx.get("type") == "buy"]
    if entries:
        token_ages = [tx.get("token_age_hours", 0) for tx in entries if tx.get("token_age_hours")]
        if token_ages:
            fp.avg_token_age_at_entry_hours = sum(token_ages) / len(token_ages)
            if fp.avg_token_age_at_entry_hours < 1:
                fp.preferred_entry_timing = "early"
            elif fp.avg_token_age_at_entry_hours < 24:
                fp.preferred_entry_timing = "mid"
            else:
                fp.preferred_entry_timing = "late"

    # ── Sophistication score (0-100) ──
    sophistication = 50.0
    if fp.profit_factor > 2:
        sophistication += 15
    if fp.win_rate > 0.6:
        sophistication += 10
    if fp.trade_frequency_per_day < 3:
        sophistication += 5  # Not overtrading
    if fp.avg_hold_hours > 24:
        sophistication += 5  # Not a flipper
    if fp.counterparty_count > 50:
        sophistication += 5
    if fp.interacts_with_scams:
        sophistication -= 20
    if fp.honeypot_interactions > 0:
        sophistication -= 15
    fp.sophistication_score = max(0, min(100, sophistication))

    # ── Risk score (0-100, higher = riskier to interact with) ──
    risk = 0.0
    if fp.interacts_with_scams:
        risk += 30
    if fp.honeypot_interactions > 3:
        risk += 25
    elif fp.honeypot_interactions > 0:
        risk += 10
    if fp.sanction_exposure:
        risk += 40
    if fp.funding_source_type == "mixer":
        risk += 25
    if fp.cluster_size > 10:
        risk += 15
    if fp.preferred_entry_timing == "early" and fp.avg_hold_hours < 1:
        risk += 10  # Sniper pattern
    fp.risk_score = max(0, min(100, risk))

    # ── Reliability score ──
    reliability = 50.0
    if fp.win_rate > 0.5:
        reliability += 10
    if fp.profit_factor > 1.5:
        reliability += 10
    if fp.trade_frequency_per_day > 0 and fp.trade_frequency_per_day < 10:
        reliability += 5
    if fp.sophistication_score > 60:
        reliability += 10
    if fp.risk_score > 50:
        reliability -= 30
    if fp.interacts_with_scams:
        reliability -= 20
    fp.reliability_score = max(0, min(100, reliability))

    # ── Persona classification ──
    classify_persona(fp)

    return fp


def classify_persona(fp: WalletFingerprint):
    """Assign persona based on behavioral fingerprint."""
    scores = {}

    # Smart Money Accumulator
    sma_score = 0
    if fp.win_rate > 0.6:
        sma_score += 3
    if fp.profit_factor > 2:
        sma_score += 3
    if fp.avg_hold_hours > 48:
        sma_score += 2
    if fp.trade_frequency_per_day < 2:
        sma_score += 1
    if fp.sophistication_score > 70:
        sma_score += 2
    scores["smart_money_accumulator"] = sma_score

    # Meme Dumper
    md_score = 0
    meme_count = fp.token_types.get("meme", 0)
    if meme_count > fp.total_trades * 0.7:
        md_score += 3
    if fp.avg_hold_hours < 4:
        md_score += 2
    if fp.trade_frequency_per_day > 5:
        md_score += 1
    if fp.win_rate < 0.3:
        md_score += 2
    scores["meme_dumper"] = md_score

    # MEV Extractor
    mev_score = 0
    if fp.trade_frequency_per_day > 20:
        mev_score += 3
    if fp.avg_hold_hours < 0.01:
        mev_score += 3
    if fp.counterparty_count > 200:
        mev_score += 2
    if fp.funding_source_type == "bot":
        mev_score += 2
    scores["mev_extractor"] = mev_score

    # Insider Accumulator
    ia_score = 0
    if fp.preferred_entry_timing == "early":
        ia_score += 3
    if fp.avg_token_age_at_entry_hours < 0.5:
        ia_score += 3
    if fp.win_rate > 0.7:
        ia_score += 2
    if fp.cluster_size > 3:
        ia_score += 1
    scores["insider_accumulator"] = ia_score

    # Whale Distributor
    wd_score = 0
    if fp.counterparty_count > 100:
        wd_score += 3
    if fp.cluster_size > 5:
        wd_score += 2
    if fp.trade_frequency_per_day > 3:
        wd_score += 1
    scores["whale_distributor"] = wd_score

    # Honeypot Victim
    hv_score = 0
    if fp.honeypot_interactions > 2:
        hv_score += 4
    if fp.interacts_with_scams:
        hv_score += 3
    if fp.rug_interactions > 1:
        hv_score += 2
    scores["honeypot_victim"] = hv_score

    # Retail (default, always scores)
    rt_score = 3
    if fp.total_trades < 100:
        rt_score += 1
    if fp.sophistication_score < 60:
        rt_score += 1
    scores["retail_trader"] = rt_score

    # Pick primary persona
    best = max(scores.items(), key=lambda x: x[1])
    fp.primary_persona = best[0]
    fp.persona_confidence = min(best[1] / 8, 1.0)  # Normalize

    # Secondary personas
    secondary = sorted([(k, v) for k, v in scores.items() if k != best[0] and v >= 2], key=lambda x: -x[1])[:2]
    fp.secondary_personas = [
        {"persona": k, "score": v, "name": PERSONAS[k]["name"], "icon": PERSONAS[k]["icon"]} for k, v in secondary
    ]

    return fp


def fingerprint_to_dict(fp: WalletFingerprint) -> dict[str, Any]:
    """Convert fingerprint to API response format."""
    persona = PERSONAS.get(fp.primary_persona, PERSONAS["retail_trader"])
    return {
        "address": fp.address,
        "chain": fp.chain,
        "persona": {
            "primary": fp.primary_persona,
            "name": persona["name"],
            "icon": persona["icon"],
            "description": persona["description"],
            "confidence": round(fp.persona_confidence, 2),
            "risk_level": persona["risk_level"],
            "secondaries": fp.secondary_personas,
        },
        "trading_behavior": {
            "total_trades": fp.total_trades,
            "win_rate": round(fp.win_rate, 3),
            "avg_hold_hours": round(fp.avg_hold_hours, 1),
            "median_hold_hours": round(fp.median_hold_hours, 1),
            "trade_frequency_per_day": round(fp.trade_frequency_per_day, 1),
            "profit_factor": round(fp.profit_factor, 2),
            "avg_profit_pct": round(fp.avg_profit_pct, 2),
            "avg_loss_pct": round(fp.avg_loss_pct, 2),
            "preferred_entry_timing": fp.preferred_entry_timing,
        },
        "risk_indicators": {
            "interacts_with_scams": fp.interacts_with_scams,
            "honeypot_interactions": fp.honeypot_interactions,
            "rug_interactions": fp.rug_interactions,
            "sanction_exposure": fp.sanction_exposure,
            "funding_source": fp.funding_source_type,
        },
        "token_preferences": {
            "types": fp.token_types,
            "avg_token_age_at_entry_hours": round(fp.avg_token_age_at_entry_hours, 1),
        },
        "network": {
            "counterparty_count": fp.counterparty_count,
            "cluster_size": fp.cluster_size,
            "preferred_chains": fp.preferred_chains[:5],
        },
        "scores": {
            "sophistication": round(fp.sophistication_score, 1),
            "risk": round(fp.risk_score, 1),
            "reliability": round(fp.reliability_score, 1),
        },
    }
