"""Assemble, score and rank smart-money moves from ClickHouse flows.

Definition of a *move*: one wallet's USD inflow (buy) or outflow (sell) on one
token inside the requested window, aggregated from on-chain transfers.

``pnl_usd`` is reported **only** for a closed round trip inside the window
(both inflow and outflow present) and is ``outflow_usd - inflow_usd``;
``pnl_pct`` divides by ``inflow_usd``. Open positions report ``null`` with a
reason instead of a fabricated mark. ``roi_30d`` needs a 30-day materialised
aggregate that does not exist yet, so it is ``null`` with a reason too.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime
from typing import Any

from app.domains.smart_money import sources
from app.domains.smart_money.models import WINDOWS, SmartMoneyMove
from app.domains.smart_money.scoring import SCORING_VERSION, score_components

logger = logging.getLogger(__name__)

DEFAULT_CHAINS = ["ethereum", "base", "arbitrum", "polygon", "optimism", "solana"]
MIN_CLOSED_POSITIONS = 3
CEX_MARKERS = ("cex", "exchange", "binance", "coinbase", "okx", "kraken", "bybit", "kucoin")


def _pnl(inflow: float, outflow: float) -> tuple[float | None, float | None, str | None]:
    """Closed-round-trip PnL, or ``(None, None, reason)``."""
    if inflow <= 0 or outflow <= 0:
        return None, None, "pnl:open_position_in_window"
    pnl = outflow - inflow
    return pnl, pnl / inflow, None


def _win_rate(pnls: list[float]) -> float | None:
    """Share of profitable closed positions, or ``None`` below the sample floor."""
    if len(pnls) < MIN_CLOSED_POSITIONS:
        return None
    return round(sum(1 for p in pnls if p > 0) / len(pnls), 4)


def _cex_related(labels: list[str]) -> bool | None:
    """True when a stored label marks the wallet as an exchange, else ``None``."""
    if not labels:
        return None
    return any(m in label.lower() for label in labels for m in CEX_MARKERS)


def build_moves(
    rows: list[dict[str, Any]],
    labels: dict[str, list[str]],
    clusters: dict[str, str],
    prices: dict[tuple[str, str], float],
    *,
    window_hours: int,
    limit: int,
    now: datetime | None = None,
) -> list[SmartMoneyMove]:
    """Turn flow rows into scored, ranked moves."""
    now = now or datetime.now(UTC)
    if not rows:
        return []

    median_amount = statistics.median(
        max(float(r["inflow_usd"]), float(r["outflow_usd"])) for r in rows
    ) or 1.0

    closed_pnls: dict[str, list[float]] = {}
    drafts: list[dict[str, Any]] = []
    for row in rows:
        inflow = float(row["inflow_usd"])
        outflow = float(row["outflow_usd"])
        pnl, pnl_pct, reason = _pnl(inflow, outflow)
        if pnl is not None:
            closed_pnls.setdefault(row["wallet"], []).append(pnl)
        last_ts = row["last_ts"]
        age_h = max(0.0, (now - last_ts.replace(tzinfo=UTC)).total_seconds() / 3600.0)
        drafts.append(
            {
                "row": row,
                "amount_usd": max(inflow, outflow),
                "netflow_usd": inflow - outflow,
                "pnl_usd": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "age_hours": age_h,
            }
        )

    scored: list[tuple[float, SmartMoneyMove]] = []
    for d in drafts:
        row = d["row"]
        wallet, chain, token = row["wallet"], row["chain"], row["token"]
        wallet_labels = labels.get(wallet, [])
        cluster_id = clusters.get(wallet)
        win_rate = _win_rate(closed_pnls.get(wallet, []))
        score, confidence, reasons = score_components(
            pnl_usd=d["pnl_usd"],
            pnl_pct=d["pnl_pct"],
            win_rate=win_rate,
            amount_usd=d["amount_usd"],
            median_amount_usd=median_amount,
            age_hours=d["age_hours"],
            window_hours=float(window_hours),
            cex_related=_cex_related(wallet_labels),
            cluster_id=cluster_id,
        )
        if d["reason"]:
            reasons.append(d["reason"])
        reasons.append("label:" + (",".join(sorted(wallet_labels)[:3]) or "none_matched"))
        if not row["symbol"]:
            reasons.append("symbol:no_metadata_row")
        if (chain, token) not in prices:
            reasons.append("price:no_price_row")
        reasons.append("roi_30d:30d_aggregate_not_materialised")
        reasons.append(f"score={score}")
        reasons.append("scoring=" + SCORING_VERSION)

        scored.append(
            (score, SmartMoneyMove(
                wallet=wallet,
                label=wallet_labels[0] if wallet_labels else None,
                chain=chain,
                token=token,
                symbol=row["symbol"] or None,
                action="buy" if float(row["inflow_usd"]) >= float(row["outflow_usd"]) else "sell",
                amount_usd=round(d["amount_usd"], 2),
                price=prices.get((chain, token)),
                pnl_usd=None if d["pnl_usd"] is None else round(d["pnl_usd"], 2),
                pnl_pct=None if d["pnl_pct"] is None else round(d["pnl_pct"], 6),
                win_rate=win_rate,
                roi_30d=None,
                netflow_usd=round(d["netflow_usd"], 2),
                cluster_id=cluster_id,
                labels=sorted(set(wallet_labels))[:8],
                confidence=confidence,
                reasons=reasons,
            ))
        )

    scored.sort(key=lambda pair: (pair[0], pair[1].netflow_usd), reverse=True)
    return [move for _, move in scored[:limit]]


def get_moves(*, chain: str | None, window: int, limit: int) -> list[SmartMoneyMove]:
    """Fetch and score the current move feed."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {WINDOWS}")
    chains = [chain] if chain else DEFAULT_CHAINS
    client = sources.get_client()
    rows = sources.fetch_token_flows(client, chains, window)
    wallets = sorted({r["wallet"] for r in rows})[:200]
    labels = sources.fetch_wallet_labels(client, wallets)
    clusters = sources.fetch_cluster_ids(client, wallets)
    prices = sources.fetch_prices(client, chains)
    logger.info("smart-money window=%sh chains=%s rows=%d", window, chains, len(rows))
    return build_moves(rows, labels, clusters, prices, window_hours=window, limit=limit)
