"""Wash-trading / fake-volume signal enrichment for the RugMunchBot scan.

Best-effort, fail-open companion to ``app/domains/scanners/wash_trading.py``.
Instead of the heavy Helius/Moralis transfer-graph cycle detection, this
module fuses three cheap, injectable signals so a scan can flag fake volume
without ever blocking on a slow provider:

  1. GMGN trade-data self-trades   — same wallet appears as BUY **and** SELL
                                     maker within a short window.
  2. Volume concentration          — a single maker dominating recent trade
                                     volume (fake-volume proxy).
  3. Legacy cycle detector         — ``WashTradingDetector.analyze()`` from
                                     ``app/domains/scanners/wash_trading.py``
                                     (best-effort; its wash_score feeds in).

Contract: every fetcher is best-effort and independently injectable. A failed
fetcher, an empty payload, or an exceeded time budget keeps the other signals
working; a total failure returns ``{"wash_trading": False}`` so the scan it
decorates is never blocked or failed.

API
---
``detect_wash_trading(address, chain, *, fetchers=None, limit=20) -> dict``
    Returns ``{wash_trading: bool, wash_volume_pct: float | None,
    self_trade_count: int | None, evidence: str | None}``. On any unhandled
    error the dict is exactly ``{"wash_trading": False}``.

``wash_signal(result) -> str | None``
    Pure renderer for the Telegram scan line:
    "wash trading: 40% self-trades" / "suspicious volume" / None.

Injectable fetchers (all optional; defaults hit GMGN / the legacy detector).
``fetchers`` is a plain dict keyed by signal name::

    "trades": async (address, chain, limit) -> list[dict]  # GMGN trade rows
    "volume": async (address, chain) -> float | None       # 24h USD volume
    "cycle":  async (address, chain) -> object | None      # legacy report

Trade rows are the normalized shape produced by
``app/databus/gmgn_provider.py`` ``get_token_trades`` (``maker``,
``timestamp`` in epoch ms, ``side`` in {"buy", "sell"}, ``amount_usd``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.databus.gmgn_provider import get_gmgn_provider

# Hard budget for the whole enrichment. GMGN's httpx client times out at 20s;
# we never let a wash-trading probe block the scan pipeline.
_BUDGET_S = 8.0

# A maker BUY+Sell within this window is a self-trade round-trip.
_SELF_TRADE_WINDOW_S = 300

# wash_volume_pct above this marks the token as wash-trading.
_WASH_PCT_THRESHOLD = 20.0

# A single maker's share of recent trade volume above this is "suspicious
# volume" even without explicit self-trades (concentration == fake volume).
_CONCENTRATION_THRESHOLD_PCT = 40.0

# Legacy cycle-detector wash_score above this (matches its HIGH risk band).
_CYCLE_SCORE_THRESHOLD = 25.0

_TRADE_LIMIT = 20

_NEUTRAL: dict[str, Any] = {"wash_trading": False}


def _to_float(value: Any) -> float | None:
    """Coerce a value to float, returning None for missing/unparseable."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _trade_side(trade: dict[str, Any]) -> str:
    """Normalize a trade row's side to "buy"/"sell" ("" when unknown)."""
    side = str(trade.get("side", "") or "").strip().lower()
    if side in ("buy", "b"):
        return "buy"
    if side in ("sell", "s"):
        return "sell"
    return ""


def _trade_ts(trade: dict[str, Any]) -> int | None:
    """Epoch-ms timestamp for a trade row (None when missing/invalid)."""
    ts = _to_float(trade.get("timestamp"))
    if ts is None or ts <= 0:
        return None
    return int(ts)


def _trade_usd(trade: dict[str, Any]) -> float:
    """USD notional of a trade row (0.0 when missing)."""
    usd = _to_float(trade.get("amount_usd"))
    return usd if usd is not None and usd > 0 else 0.0


def _normalize_trades(trades: Any) -> list[dict[str, Any]]:
    """Keep only dict rows (defensive against provider junk)."""
    if not isinstance(trades, list):
        return []
    return [t for t in trades if isinstance(t, dict)]


def _detect_self_trades(
    trades: list[dict[str, Any]],
    window_s: float = _SELF_TRADE_WINDOW_S,
) -> tuple[int, float, float]:
    """Count makers that round-trip (BUY + SELL) within a short window.

    Returns ``(self_trade_count, wash_volume_usd, total_volume_usd)``.
    ``wash_volume_usd`` is the smaller side summed per matched window per
    maker, so both sides of a round-trip are not double counted.
    """
    total_usd = 0.0
    by_maker: dict[str, list[tuple[int, str, float]]] = {}
    for trade in trades:
        maker = str(trade.get("maker") or "").strip()
        usd = _trade_usd(trade)
        if usd > 0:
            total_usd += usd
        ts = _trade_ts(trade)
        side = _trade_side(trade)
        if not maker or ts is None or not side or usd <= 0:
            continue
        by_maker.setdefault(maker, []).append((ts, side, usd))

    window_ms = window_s * 1000
    self_makers = 0
    wash_usd = 0.0
    for _maker, rows in by_maker.items():
        rows.sort(key=lambda r: r[0])
        i = 0
        matched = False
        matched_wash = 0.0
        while i < len(rows):
            j = i
            while j < len(rows) and rows[j][0] - rows[i][0] <= window_ms:
                j += 1
            window = rows[i:j]
            buys = [r for r in window if r[1] == "buy"]
            sells = [r for r in window if r[1] == "sell"]
            if buys and sells:
                matched = True
                matched_wash += min(sum(r[2] for r in buys), sum(r[2] for r in sells))
            i = j
        if matched:
            self_makers += 1
            wash_usd += matched_wash
    return self_makers, wash_usd, total_usd


def _volume_concentration(trades: list[dict[str, Any]]) -> tuple[float | None, str]:
    """Share of trade volume held by the single largest maker.

    Returns ``(top_maker_share_pct, evidence)``. ``None`` when there is no
    usable volume (no makers or no USD notional).
    """
    per_maker: dict[str, float] = {}
    for trade in trades:
        maker = str(trade.get("maker") or "").strip()
        usd = _trade_usd(trade)
        if not maker or usd <= 0:
            continue
        per_maker[maker] = per_maker.get(maker, 0.0) + usd
    total = sum(per_maker.values())
    if not per_maker or total <= 0:
        return None, ""
    top_maker, top_usd = max(per_maker.items(), key=lambda kv: kv[1])
    share = round(top_usd / total * 100, 1)
    return share, f"top maker {top_maker[:6]}... {share:.0f}% of recent trade volume"


def _cycle_wash_score(cycle: Any) -> float | None:
    """Extract wash_score from a legacy ``WashTradingReport`` (object or dict)."""
    if cycle is None:
        return None
    if isinstance(cycle, dict):
        return _to_float(cycle.get("wash_score"))
    score = _to_float(getattr(cycle, "wash_score", None))
    return score


# ── Default fetchers (production paths; replaced in tests) ────────────────


async def _default_trades_fetcher(address: str, chain: str, limit: int) -> list[dict[str, Any]]:
    provider = get_gmgn_provider()
    return await provider.get_token_trades(address, chain, limit)


async def _default_volume_fetcher(address: str, chain: str) -> float | None:
    """24h USD volume from GMGN token info (queryable; None when unavailable)."""
    provider = get_gmgn_provider()
    info = await provider.get_token_info(address, chain)
    if not isinstance(info, dict):
        return None
    return _to_float(info.get("volume_24h"))


async def _default_cycle_fetcher(address: str, chain: str) -> Any:
    """Legacy transfer-graph cycle detector (best-effort; closes its client)."""
    from app.domains.scanners.wash_trading import WashTradingDetector

    detector = WashTradingDetector()
    try:
        return await detector.analyze(address, chain)
    finally:
        await detector.client.aclose()


# ── Signal evaluation ─────────────────────────────────────────────────────


def _evaluate(trades: Any, volume: Any, cycle: Any) -> dict[str, Any]:
    """Fuse the three signals into the report dict (pure, no I/O)."""
    rows = _normalize_trades(trades)
    self_count, wash_usd, trade_total = _detect_self_trades(rows)

    wash_pct: float | None = None
    if trade_total > 0:
        wash_pct = round(wash_usd / trade_total * 100, 1) if wash_usd > 0 else 0.0

    flagged = bool(self_count > 0)
    parts: list[str] = []
    if self_count > 0:
        parts.append(f"{self_count} wallet(s) round-tripped BUY/SELL within {_SELF_TRADE_WINDOW_S}s")
    elif rows:
        parts.append(f"no self-trades in {len(rows)} recent trades")
    else:
        parts.append("no recent trade data")

    top_share, concentration_evidence = _volume_concentration(rows)
    if top_share is not None and top_share >= _CONCENTRATION_THRESHOLD_PCT:
        flagged = True
        parts.append(concentration_evidence)

    cycle_score = _cycle_wash_score(cycle)
    if cycle_score is not None and cycle_score > _CYCLE_SCORE_THRESHOLD:
        flagged = True
        parts.append(f"legacy cycle detector wash_score {cycle_score:.0f}/100")

    ext_vol = _to_float(volume)
    if ext_vol is not None:
        parts.append(f"24h volume ${ext_vol:,.0f}")
    else:
        parts.append("24h volume unavailable")

    return {
        "wash_trading": flagged,
        "wash_volume_pct": wash_pct,
        "self_trade_count": self_count,
        "evidence": "; ".join(parts) or None,
    }


async def detect_wash_trading(
    address: str,
    chain: str,
    *,
    fetchers: dict[str, Any] | None = None,
    limit: int = _TRADE_LIMIT,
) -> dict[str, Any]:
    """Detect wash-trading / fake-volume signals for ``address`` on ``chain``.

    Fail-open: any error or timeout returns ``{"wash_trading": False}``.
    ``fetchers`` overrides individual signal sources — inject in tests to keep
    this module network-free.
    """
    if not address:
        return dict(_NEUTRAL)

    trades_f: Any = _default_trades_fetcher
    volume_f: Any = _default_volume_fetcher
    cycle_f: Any = _default_cycle_fetcher
    if fetchers:
        trades_f = fetchers.get("trades", trades_f)
        volume_f = fetchers.get("volume", volume_f)
        cycle_f = fetchers.get("cycle", cycle_f)

    try:
        trades, volume, cycle = await asyncio.wait_for(
            asyncio.gather(
                trades_f(address, chain, limit),
                volume_f(address, chain),
                cycle_f(address, chain),
                return_exceptions=True,
            ),
            timeout=_BUDGET_S,
        )
    except Exception:  # fail-open contract — never raises
        return dict(_NEUTRAL)

    if isinstance(trades, BaseException):
        trades = None
    if isinstance(volume, BaseException):
        volume = None
    if isinstance(cycle, BaseException):
        cycle = None

    try:
        return _evaluate(trades, volume, cycle)
    except Exception:  # fail-open contract — never raises
        return dict(_NEUTRAL)


def wash_signal(result: dict[str, Any] | None) -> str | None:
    """Render a human scan-line for a ``detect_wash_trading`` result.

    Returns "wash trading: X% self-trades" when explicit self-trades were
    found, "suspicious volume" when the token was flagged without
    self-trades (concentration / cycle detector), else None.
    """
    if not isinstance(result, dict) or not result.get("wash_trading"):
        return None
    self_count = result.get("self_trade_count")
    if isinstance(self_count, int) and self_count > 0:
        pct = result.get("wash_volume_pct")
        if isinstance(pct, (int, float)):
            return f"wash trading: {round(pct)}% self-trades"
        return f"wash trading: {self_count} self-trades"
    return "suspicious volume"
