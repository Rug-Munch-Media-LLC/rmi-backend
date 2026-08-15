"""
OSINT suspicious DEX activity — wash-trading detector for DEX pairs.

Given a DEX pair address (or a token address in ``chain="auto"`` mode), pull
public pair data from DexScreener (no API key required) and score it for
wash-trading / fake-volume behaviour:

- **volume/liquidity divergence** — high 24h volume with little or no
  liquidity (volume:liquidity turnover far above organic market norms).
- **buy/sell symmetry** — near-equal buy and sell counts over 24h, the
  round-trip signature of self-dealing.
- **liquidity churn / volatility** — zero liquidity with nonzero volume, or
  extreme turnover on a very young pair. *Proxy:* the free DexScreener API
  does not expose historical LP add/remove events, so churn is inferred from
  liquidity state, turnover and pair age.
- **trader concentration** — few trades moving a lot of volume, i.e. a small
  set of actors dominating the pair. *Proxy:* unique-trader counts are not
  exposed by the free API, so concentration is inferred from average trade
  size and txn count.

Each signal becomes an :class:`~app.osint.OsintFinding`
(``source="dexscreener"``, ``category="wash_trading"``) with its own
confidence, and the module emits an aggregate ``wash_trade_risk``
(``low``/``medium``/``high``) computed from the per-signal scores.

Contract: ``lookup_pair_activity(pair_address, *, chain="auto", timeout=60)
-> OsintReport`` (see :mod:`app.osint`). API failures and empty results are
recorded in ``report.errors`` — this never raises.

Reuses the same public DexScreener endpoint patterns already used across the
codebase (``app.unified_provider``, ``app.token_watch``,
``app.routers.token_page`` …) rather than adding a client. Those are async
provider methods; OSINT modules are sync/blocking by contract (the engine
runs them in a ``ThreadPoolExecutor``), so this module calls the API directly
with sync ``httpx``, exactly like :mod:`app.osint.wallet` does.

Dependencies: stdlib + ``httpx`` (already pinned in pyproject).

Usage::

    from app.osint.dex_activity import lookup_pair_activity

    report = lookup_pair_activity("0xe6ff8b9A37B0fab776134636D9981Aa778c4e718", chain="ethereum", timeout=30)
    print(report.to_dict())
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Final

try:
    import httpx
except ImportError as exc:  # pragma: no cover — httpx is a pinned runtime dependency
    from app.osint import OsintNotAvailable

    raise OsintNotAvailable("dex activity detection requires httpx: pip install httpx") from exc

from app.osint import OsintFinding, OsintReport

logger = logging.getLogger(__name__)

# ─── Tunables ────────────────────────────────────────────────────────

#: DexScreener public API base (no key; see app.routers.token_page for the same base).
DEXSCREENER_API: Final = "https://api.dexscreener.com/latest/dex"

#: Chain aliases normalised to DexScreener's chainId values (pass-through otherwise).
CHAIN_ALIASES: Final[dict[str, str]] = {
    "eth": "ethereum",
    "sol": "solana",
    "bnb": "bsc",
    "matic": "polygon",
    "arb": "arbitrum",
    "op": "optimism",
}

EVM_PAIR_RE: Final = re.compile(r"^0x[0-9a-fA-F]{40}$")
BASE58_PAIR_RE: Final = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

#: Minimum 24h txn count before buy/sell symmetry is statistically meaningful.
MIN_TXNS_FOR_SYMMETRY: Final = 30
#: Volume:liquidity turnover thresholds (24h volume / USD liquidity).
VOLUME_RATIO_LOW: Final = 8.0
VOLUME_RATIO_MEDIUM: Final = 15.0
VOLUME_RATIO_HIGH: Final = 30.0
#: Buy/sell symmetry (min(buys, sells) / max(buys, sells)) thresholds.
SYMMETRY_LOW: Final = 0.88
SYMMETRY_MEDIUM: Final = 0.93
SYMMETRY_HIGH: Final = 0.97
#: Established-pair (>= 48h old) churn thresholds.
CHURN_RATIO_MEDIUM: Final = 30.0
CHURN_RATIO_HIGH: Final = 60.0
#: Young-pair churn thresholds (hours since creation and volume:liquidity ratio).
YOUNG_PAIR_HOURS: Final = 48.0
YOUNG_PAIR_CHURN_RATIO: Final = 10.0
YOUNG_PAIR_CHURN_HIGH: Final = 20.0
#: Average trade size (24h volume / 24h txns) concentration thresholds.
AVG_TRADE_MEDIUM: Final = 10_000.0
AVG_TRADE_HIGH: Final = 50_000.0
#: Few-trades-large-volume concentration thresholds.
FEW_TRADES_COUNT: Final = 20
FEW_TRADES_VOLUME: Final = 100_000.0
#: Max pairs analyzed per lookup in auto mode (ranked by liquidity desc).
MAX_PAIRS: Final = 8
#: Aggregate risk score cutoffs (high=2, medium=1, low=0.5 per finding).
RISK_HIGH_SCORE: Final = 3.0
RISK_MEDIUM_SCORE: Final = 1.0

_CONFIDENCE_POINTS: Final[dict[str, float]] = {"high": 2.0, "medium": 1.0, "low": 0.5}


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce a DexScreener numeric field (float, int or str) to ``float``."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _subdict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``mapping[key]`` when it is a dict, else an empty dict."""
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _token_symbol(token: Any) -> str:
    """Short symbol/name for a base/quote token object."""
    if isinstance(token, dict):
        return str(token.get("symbol") or token.get("name") or "?")
    return "?"


def _fmt_usd(value: float) -> str:
    """Compact USD formatting for summaries (e.g. ``$1.2M``, ``$50.3K``)."""
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    if value >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:.0f}"


def _pair_label(pair: dict[str, Any]) -> str:
    """Short human label for a pair: ``chain/dex/addr-prefix``."""
    chain = str(pair.get("chainId") or "?")
    dex = str(pair.get("dexId") or "?")
    addr = str(pair.get("pairAddress") or "?")
    return f"{chain}/{dex}/{addr[:10]}"


def _pair_age_hours(pair: dict[str, Any]) -> float | None:
    """Age of the pair in hours from ``pairCreatedAt`` (ms), else ``None``."""
    created_ms = _num(pair.get("pairCreatedAt"))
    if created_ms <= 0:
        return None
    return max(0.0, (time.time() * 1000.0 - created_ms) / 3_600_000.0)


def _add_finding(
    report: OsintReport,
    source: str,
    category: str,
    summary: str,
    *,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Append a finding with an explicit confidence (same helper as wallet.py)."""
    report.findings.append(
        OsintFinding(
            source=source,
            category=category,
            summary=summary,
            confidence=confidence,
            data=data,
        )
    )


# ─── Signals (one per wash-trading heuristic) ────────────────────────


def _signal_volume_divergence(pair: dict[str, Any]) -> list[OsintFinding]:
    """Volume/liquidity divergence — 24h volume with little or no backing liquidity."""
    volume = _num(_subdict(pair, "volume").get("h24"))
    liquidity = _num(_subdict(pair, "liquidity").get("usd"))
    findings: list[OsintFinding] = []

    if liquidity <= 0:
        if volume > 0:
            findings.append(
                OsintFinding(
                    source="dexscreener",
                    category="wash_trading",
                    summary=f"nonzero 24h volume {_fmt_usd(volume)} with zero liquidity",
                    confidence="high",
                    data={"signal": "volume_divergence", "volume_h24_usd": volume, "liquidity_usd": 0.0},
                )
            )
        return findings

    ratio = volume / liquidity
    if ratio >= VOLUME_RATIO_HIGH:
        summary = (
            f"extreme volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "high"
    elif ratio >= VOLUME_RATIO_MEDIUM:
        summary = (
            f"high volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "medium"
    elif ratio >= VOLUME_RATIO_LOW:
        summary = (
            f"elevated volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "low"
    else:
        return findings

    findings.append(
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "volume_divergence",
                "volume_h24_usd": volume,
                "liquidity_usd": liquidity,
                "ratio": round(ratio, 2),
            },
        )
    )
    return findings


def _signal_buy_sell_symmetry(pair: dict[str, Any]) -> list[OsintFinding]:
    """Buy/sell symmetry — near-equal buy/sell counts (round-trip pattern)."""
    h24 = _subdict(_subdict(pair, "txns"), "h24")
    buys = _num(h24.get("buys"))
    sells = _num(h24.get("sells"))
    total = buys + sells
    if total < MIN_TXNS_FOR_SYMMETRY:
        return []

    symmetry = min(buys, sells) / max(buys, sells)
    if symmetry >= SYMMETRY_HIGH:
        summary = f"near-perfect buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h) — round-trip signature"
        confidence = "high"
    elif symmetry >= SYMMETRY_MEDIUM:
        summary = f"high buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h)"
        confidence = "medium"
    elif symmetry >= SYMMETRY_LOW:
        summary = f"elevated buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h)"
        confidence = "low"
    else:
        return []

    return [
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={"signal": "buy_sell_symmetry", "buys_h24": buys, "sells_h24": sells, "symmetry": round(symmetry, 3)},
        )
    ]


def _signal_liquidity_churn(pair: dict[str, Any]) -> list[OsintFinding]:
    """Liquidity churn/volatility — LP add/remove proxy from liquidity state + turnover + age.

    The free DexScreener API does not expose historical LP events; churn is
    inferred from: zero liquidity with volume (liquidity pulled or never
    added), extreme turnover on a young pair, and extreme sustained turnover
    on an established pair.
    """
    volume = _num(_subdict(pair, "volume").get("h24"))
    liquidity = _num(_subdict(pair, "liquidity").get("usd"))
    age_hours = _pair_age_hours(pair)
    findings: list[OsintFinding] = []

    if liquidity <= 0:
        if volume > 0:
            findings.append(
                OsintFinding(
                    source="dexscreener",
                    category="wash_trading",
                    summary="no liquidity backing the pair while volume trades — LP removed or never added",
                    confidence="high",
                    data={
                        "signal": "liquidity_churn",
                        "proxy": "no LP history in public API",
                        "liquidity_usd": 0.0,
                        "volume_h24_usd": volume,
                    },
                )
            )
        return findings

    ratio = volume / liquidity
    if age_hours is not None and age_hours < YOUNG_PAIR_HOURS:
        if ratio >= YOUNG_PAIR_CHURN_HIGH:
            summary = (
                f"extreme turnover {ratio:.1f}x on a {age_hours:.0f}h-old pair "
                f"({_fmt_usd(volume)} volume vs {_fmt_usd(liquidity)} liquidity) — consistent with LP churn/wash"
            )
            confidence = "high"
        elif ratio >= YOUNG_PAIR_CHURN_RATIO:
            summary = (
                f"high turnover {ratio:.1f}x on a {age_hours:.0f}h-old pair "
                f"({_fmt_usd(volume)} volume vs {_fmt_usd(liquidity)} liquidity)"
            )
            confidence = "medium"
        else:
            return findings
    elif ratio >= CHURN_RATIO_HIGH:
        summary = f"extreme sustained turnover {ratio:.1f}x — liquidity churned repeatedly (LP add/remove proxy)"
        confidence = "high"
    elif ratio >= CHURN_RATIO_MEDIUM:
        summary = f"sustained high turnover {ratio:.1f}x — liquidity churned repeatedly (LP add/remove proxy)"
        confidence = "medium"
    else:
        return findings

    findings.append(
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "liquidity_churn",
                "proxy": "no LP history in public API",
                "volume_h24_usd": volume,
                "liquidity_usd": liquidity,
                "ratio": round(ratio, 2),
                "pair_age_hours": round(age_hours, 1) if age_hours is not None else None,
            },
        )
    )
    return findings


def _signal_trader_concentration(pair: dict[str, Any]) -> list[OsintFinding]:
    """Trader concentration — few trades moving a lot of volume.

    The free DexScreener API does not expose unique-trader counts; this is a
    proxy: a large average trade size (24h volume / 24h txn count), or a
    handful of trades moving significant volume, implies activity concentrated
    in a small set of actors.
    """
    volume = _num(_subdict(pair, "volume").get("h24"))
    h24 = _subdict(_subdict(pair, "txns"), "h24")
    total_txns = _num(h24.get("buys")) + _num(h24.get("sells"))
    if volume <= 0 or total_txns <= 0:
        return []

    avg_trade = volume / total_txns
    if avg_trade >= AVG_TRADE_HIGH:
        summary = (
            f"average trade size {_fmt_usd(avg_trade)} across {total_txns:.0f} trades in 24h — "
            "activity concentrated in very few wallets"
        )
        confidence = "high"
    elif avg_trade >= AVG_TRADE_MEDIUM:
        summary = (
            f"average trade size {_fmt_usd(avg_trade)} across {total_txns:.0f} trades in 24h — "
            "activity concentrated in few wallets"
        )
        confidence = "medium"
    elif total_txns < FEW_TRADES_COUNT and volume >= FEW_TRADES_VOLUME:
        summary = (
            f"only {total_txns:.0f} trades moved {_fmt_usd(volume)} in 24h — "
            "activity concentrated in a small set of wallets"
        )
        confidence = "low"
    else:
        return []

    return [
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "trader_concentration",
                "proxy": "no unique-trader counts in public API",
                "volume_h24_usd": volume,
                "txns_h24": total_txns,
                "avg_trade_usd": round(avg_trade, 2),
            },
        )
    ]


# ─── Analysis ────────────────────────────────────────────────────────


def _analyze_pair_signals(pair: dict[str, Any]) -> list[OsintFinding]:
    """Run every wash-trading signal against one pair."""
    findings: list[OsintFinding] = []
    for signal in (
        _signal_volume_divergence,
        _signal_buy_sell_symmetry,
        _signal_liquidity_churn,
        _signal_trader_concentration,
    ):
        findings.extend(signal(pair))
    return findings


def _signal_score(findings: list[OsintFinding]) -> float:
    """Aggregate risk score from signal confidence levels (high=2, medium=1, low=0.5)."""
    return sum(_CONFIDENCE_POINTS.get(f.confidence, 0.0) for f in findings)


def _risk_level(score: float) -> str:
    """Map an aggregate score to ``high``/``medium``/``low``."""
    if score >= RISK_HIGH_SCORE:
        return "high"
    if score >= RISK_MEDIUM_SCORE:
        return "medium"
    return "low"


def _context_finding(pair: dict[str, Any]) -> OsintFinding:
    """Context finding describing the pair (not scored; mirrors wallet.py's chain finding)."""
    base = _token_symbol(pair.get("baseToken"))
    quote = _token_symbol(pair.get("quoteToken"))
    chain = str(pair.get("chainId") or "?")
    dex = str(pair.get("dexId") or "?")
    age_hours = _pair_age_hours(pair)
    return OsintFinding(
        source="dexscreener",
        category="pair_context",
        summary=f"{base}/{quote} on {dex} ({chain})",
        confidence="high",
        data={
            "pair_address": pair.get("pairAddress") or "",
            "chain_id": chain,
            "dex_id": dex,
            "base_token": base,
            "quote_token": quote,
            "price_usd": _num(pair.get("priceUsd")),
            "liquidity_usd": _num(_subdict(pair, "liquidity").get("usd")),
            "volume_h24_usd": _num(_subdict(pair, "volume").get("h24")),
            "buys_h24": _num(_subdict(_subdict(pair, "txns"), "h24").get("buys")),
            "sells_h24": _num(_subdict(_subdict(pair, "txns"), "h24").get("sells")),
            "pair_age_hours": round(age_hours, 1) if age_hours is not None else None,
        },
    )


# ─── Data fetch ──────────────────────────────────────────────────────


def _parse_pairs(payload: Any) -> list[dict[str, Any]]:
    """Extract the ``pairs`` list from a DexScreener payload, tolerating garbage."""
    if not isinstance(payload, dict):
        return []
    pairs = payload.get("pairs")
    if not isinstance(pairs, list):
        return []
    return [p for p in pairs if isinstance(p, dict)]


def _fetch_pairs(address: str, chain: str, timeout: int) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch candidate pairs from DexScreener; returns ``(pairs, error)``.

    With an explicit ``chain``, try the exact pair endpoint first
    (``/pairs/{chain}/{address}``), then fall back to the token endpoint
    filtered by chain — so both a pair address and a token address work.
    With ``chain="auto"``, use the token endpoint and analyze every pair.
    Never raises: transport failures surface as the error string.
    """
    try:
        if chain and chain != "auto":
            resp = httpx.get(f"{DEXSCREENER_API}/pairs/{chain}/{address}", timeout=timeout)
            if resp.status_code == 200:
                pairs = _parse_pairs(resp.json())
                if pairs:
                    return pairs, None
            resp = httpx.get(f"{DEXSCREENER_API}/tokens/{address}", timeout=timeout)
            if resp.status_code == 200:
                pairs = [p for p in _parse_pairs(resp.json()) if str(p.get("chainId", "")).lower() == chain]
                if pairs:
                    return pairs, None
            return [], f"no pairs for chain={chain!r} (HTTP {resp.status_code})"

        resp = httpx.get(f"{DEXSCREENER_API}/tokens/{address}", timeout=timeout)
        if resp.status_code == 200:
            pairs = _parse_pairs(resp.json())
            if pairs:
                return pairs, None
            return [], "no pairs found (HTTP 200, empty payload)"
        return [], f"HTTP {resp.status_code}"
    except (httpx.HTTPError, OSError) as exc:
        return [], str(exc)


# ─── Public API ──────────────────────────────────────────────────────


def lookup_pair_activity(pair_address: str, *, chain: str = "auto", timeout: int = 60) -> OsintReport:
    """Detect wash-trading / suspicious DEX activity for a pair or token address.

    Args:
        pair_address: DEX pair address (EVM ``0x…`` or Solana base58) — or a
            token address when ``chain="auto"``, in which case every pair
            listed for the token is analyzed.
        chain: ``"auto"`` (default) resolves via the token endpoint; otherwise
            a DexScreener chainId (``"ethereum"``, ``"bsc"``, ``"solana"``,
            ``"base"``, ``"arbitrum"`` …; common aliases like ``"eth"``/
            ``"sol"`` are normalised).
        timeout: per-HTTP-request timeout in seconds.

    Returns:
        Aggregated :class:`OsintReport`; each signal is an ``OsintFinding``
        with ``source="dexscreener"`` and ``category="wash_trading"``, and the
        final finding carries ``wash_trade_risk`` (low/medium/high). API
        failures and empty results land in ``report.errors`` — never raises.
    """
    report = OsintReport(target=pair_address, target_type="pair")

    address = pair_address.strip()
    if not address:
        report.errors.append("normalization: empty pair address")
        return report
    if not (EVM_PAIR_RE.match(address) or BASE58_PAIR_RE.match(address)):
        report.errors.append(f"normalization: unrecognized address format: {address[:24]!r}")
        return report

    chain_key = CHAIN_ALIASES.get(chain.strip().lower(), chain.strip().lower())
    pairs, fetch_err = _fetch_pairs(address, chain_key, timeout)
    if fetch_err is not None:
        report.errors.append(f"dexscreener: {fetch_err}")
        return report
    if not pairs:
        report.errors.append(f"dexscreener: no pair data for {address}")
        return report

    ranked = sorted(
        pairs,
        key=lambda p: _num(_subdict(p, "liquidity").get("usd")),
        reverse=True,
    )[:MAX_PAIRS]

    per_pair_risk: dict[str, str] = {}
    worst_score = 0.0
    worst_level = "low"
    for pair in ranked:
        report.findings.append(_context_finding(pair))
        signals = _analyze_pair_signals(pair)
        report.findings.extend(signals)
        score = _signal_score(signals)
        level = _risk_level(score)
        per_pair_risk[_pair_label(pair)] = level
        if score > worst_score:
            worst_score = score
            worst_level = level

    _add_finding(
        report,
        source="dexscreener",
        category="wash_trading",
        summary=(f"aggregate wash-trade risk: {worst_level} (score {worst_score:.1f}, {len(ranked)} pair(s) analyzed)"),
        confidence="high" if worst_level == "high" else "medium",
        wash_trade_risk=worst_level,
        score=round(worst_score, 1),
        pairs_analyzed=len(ranked),
        per_pair_risk=per_pair_risk,
    )
    return report


__all__ = ["lookup_pair_activity"]
