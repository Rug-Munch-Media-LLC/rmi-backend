"""AI rug probability predictor — 7-signal fusion over an advanced scan result.

Wires the ``rug_pull_predictor`` canonical tool (app/canonical_tools/
security_analysis.py) into the advanced/deepscan scan path. Unlike
:mod:`app.detectors.rug_pull_predictor` (which makes its own network calls),
this module fuses signals ALREADY computed by the scan engines — zero extra
API calls, zero added latency beyond the fusion math.

Fused signals (from the scan payload):
1. Deployer risk        — deployer_profile, dev_reputation, funding_analysis
2. Holder concentration — holder_concentration top-10 %, fresh-wallet %
3. Liquidity            — liquidity_usd, LP verification / lock status
4. Volume authenticity  — wash-trade / bot-farm risk flags
5. Bundle detection     — bundler_count, bundler_pct, bundle_analysis
6. Tokenomics           — mint/freeze authority, supply concentration
7. Market microstructure— price_metrics spread/depth/slippage proxies

Fail-open by contract: the caller wraps this in try/except so a fusion
failure never breaks the scan response.
"""

from __future__ import annotations

from typing import Any

# Signal weights (sum = 1.0). Mirrors the canonical predictor's emphasis on
# liquidity + holders, tuned for the advanced-scan engine payloads.
_WEIGHTS: dict[str, float] = {
    "deployer_risk": 0.18,
    "holder_concentration": 0.18,
    "liquidity": 0.20,
    "volume_authenticity": 0.12,
    "bundle_detection": 0.12,
    "tokenomics": 0.10,
    "market_microstructure": 0.10,
}

_TIME_HORIZONS: list[tuple[float, str]] = [
    (85.0, "imminent"),
    (65.0, "hours"),
    (40.0, "days"),
    (0.0, "weeks"),
]


def _num(value: Any) -> float | None:
    """Best-effort numeric coercion; None when not a usable number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _pct(value: Any) -> float | None:
    """Coerce a value that may be a 0-1 fraction or 0-100 percent to 0-100."""
    n = _num(value)
    if n is None:
        return None
    return n * 100.0 if 0.0 <= n <= 1.0 else n


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _signal_deployer_risk(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Deployer risk: fresh wallet, prior rugs, exchange-funded, dev sold."""
    score, findings = 0.0, []
    dp = payload.get("deployer_profile")
    if isinstance(dp, dict):
        age_days = _num(dp.get("wallet_age_days") or dp.get("age_days"))
        if age_days is not None and age_days < 7:
            score += 30
            findings.append(f"deployer wallet is {age_days:.1f} days old (fresh wallet)")
        prior = _num(dp.get("prior_rugs") or dp.get("rugs_created"))
        if prior is not None and prior > 0:
            score += 40
            findings.append(f"deployer has {int(prior)} prior rug association(s)")
    dev_rep = payload.get("dev_reputation")
    if isinstance(dev_rep, dict):
        rep_score = _num(dev_rep.get("risk_score") or dev_rep.get("score"))
        if rep_score is not None:
            score += _clamp(rep_score) * 0.3
            if rep_score >= 60:
                findings.append(f"dev reputation risk score {rep_score:.0f}")
    fa = payload.get("funding_analysis")
    if isinstance(fa, dict):
        cex = _pct(fa.get("cex_funded_pct"))
        if cex is not None and cex >= 50:
            score += 15
            findings.append(f"{cex:.0f}% of funding traced to exchanges")
    dev_sold = _pct(payload.get("dev_sold"))
    if dev_sold is not None and dev_sold >= 25:
        score += 25
        findings.append(f"dev has sold {dev_sold:.0f}% of holdings")
    return _clamp(score), findings


def _signal_holder_concentration(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Top-10 holder % and fresh-wallet holder %."""
    score, findings = 0.0, []
    hc = payload.get("holder_concentration")
    if isinstance(hc, dict):
        top10 = _pct(hc.get("top10_pct") or hc.get("top_10_pct"))
        if top10 is None:
            holders = hc.get("top_holders")
            if isinstance(holders, list):
                supplies = [_pct(h.get("pct")) if isinstance(h, dict) else None for h in holders[:10]]
                vals = [v for v in supplies if v is not None]
                top10 = sum(vals) if vals else None
        if top10 is not None:
            if top10 >= 70:
                score += 45
                findings.append(f"top 10 holders control {top10:.0f}% of supply")
            elif top10 >= 50:
                score += 25
                findings.append(f"top 10 holders control {top10:.0f}% of supply")
            elif top10 >= 30:
                score += 10
                findings.append(f"top 10 holders control {top10:.0f}% of supply")
    wm = payload.get("wallet_metrics_summary")
    if isinstance(wm, dict):
        fresh = _pct(wm.get("fresh_wallet_pct"))
        if fresh is not None and fresh >= 40:
            score += 25
            findings.append(f"{fresh:.0f}% of holders are fresh wallets")
    return _clamp(score), findings


def _signal_liquidity(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Liquidity depth and lock status."""
    score, findings = 0.0, []
    liq = _num(payload.get("liquidity_usd"))
    mcap = _num(payload.get("market_cap_usd"))
    if liq is not None:
        if liq < 5_000:
            score += 40
            findings.append(f"liquidity only ${liq:,.0f} (trivially drainable)")
        elif liq < 25_000:
            score += 20
            findings.append(f"thin liquidity (${liq:,.0f})")
    if liq is not None and mcap is not None and mcap > 0:
        ratio = liq / mcap
        if ratio < 0.02:
            score += 25
            findings.append(f"liquidity/mcap ratio {ratio:.2%} — exit liquidity poor")
    sent = payload.get("sentinel")
    sent_free = sent.get("free") if isinstance(sent, dict) and isinstance(sent.get("free"), dict) else {}
    lp = sent_free.get("lp_verification")
    if isinstance(lp, dict):
        locked = lp.get("locked") or lp.get("is_locked") or lp.get("lp_locked")
        if locked is False:
            score += 30
            findings.append("LP tokens are unlocked (rug-ready)")
        elif locked is True:
            score -= 15
            findings.append("LP tokens locked")
    return _clamp(score), findings


def _signal_volume_authenticity(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Wash-trade / bot-farm volume signals."""
    score, findings = 0.0, []
    flags = payload.get("risk_flags")
    flag_list = flags if isinstance(flags, list) else []
    wash_terms = ("wash", "bot", "fake volume", "volume anomaly", "bot_farm", "wash_trade")
    for f in flag_list:
        text = f if isinstance(f, str) else str(f)
        if any(t in text.lower() for t in wash_terms):
            score += 35
            findings.append(f"volume authenticity flag: {text}")
            break
    vol = _num(payload.get("volume_24h_usd"))
    liq = _num(payload.get("liquidity_usd"))
    if vol is not None and liq is not None and liq > 0 and vol / liq > 50:
        score += 25
        findings.append(f"24h volume is {vol / liq:.0f}x liquidity — likely inauthentic")
    return _clamp(score), findings


def _signal_bundle_detection(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Bundle wallets / bundled first buyers."""
    score, findings = 0.0, []
    bundler_count = payload.get("bundler_count")
    bundler_pct = _pct(payload.get("bundler_pct"))
    if isinstance(bundler_count, (int, float)) and bundler_count >= 5:
        score += 30
        findings.append(f"{int(bundler_count)} bundle wallets detected")
    elif isinstance(bundler_count, (int, float)) and bundler_count > 0:
        score += 15
        findings.append(f"{int(bundler_count)} bundle wallet(s) detected")
    if bundler_pct is not None and bundler_pct >= 30:
        score += 25
        findings.append(f"{bundler_pct:.0f}% of first buyers were bundled")
    ba = payload.get("bundle_analysis")
    if isinstance(ba, dict):
        supply_pct = _pct(ba.get("supply_pct") or ba.get("bundled_supply_pct"))
        if supply_pct is not None and supply_pct >= 10:
            score += 20
            findings.append(f"bundled wallets hold {supply_pct:.0f}% of supply")
    return _clamp(score), findings


def _signal_tokenomics(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Mint / freeze authority and supply risk."""
    score, findings = 0.0, []
    ca = payload.get("contract_authority")
    if isinstance(ca, dict):
        if ca.get("mint_authority") not in (None, False, "revoked", "none", ""):
            score += 35
            findings.append("mint authority still active — supply can be inflated")
        if ca.get("freeze_authority") not in (None, False, "revoked", "none", ""):
            score += 30
            findings.append("freeze authority active — holdings can be frozen")
    flags = payload.get("risk_flags")
    flag_list = flags if isinstance(flags, list) else []
    for f in flag_list:
        text = f if isinstance(f, str) else str(f)
        low = text.lower()
        if "mint" in low or "freeze" in low or "infinite supply" in low:
            score += 20
            findings.append(f"tokenomics flag: {text}")
            break
    return _clamp(score), findings


def _signal_market_microstructure(payload: dict[str, Any]) -> tuple[float, list[str]]:
    """Spread / depth / slippage proxies from price metrics."""
    score, findings = 0.0, []
    pm = payload.get("price_metrics")
    if isinstance(pm, dict):
        spread = _pct(pm.get("spread_pct"))
        if spread is not None and spread >= 5:
            score += 30
            findings.append(f"wide spread ({spread:.1f}%) — thin book")
        slippage = _pct(pm.get("slippage_pct") or pm.get("slippage_1k_pct"))
        if slippage is not None and slippage >= 10:
            score += 25
            findings.append(f"high slippage ({slippage:.0f}%) on modest size")
        depth = _num(pm.get("depth_usd") or pm.get("book_depth_usd"))
        if depth is not None and depth < 10_000:
            score += 20
            findings.append(f"order-book depth only ${depth:,.0f}")
    price_1h = _num(
        payload.get("price_change_1h") or (pm or {}).get("price_change_1h") if isinstance(pm, dict) else None
    )
    if price_1h is not None and abs(price_1h) >= 50:
        score += 15
        findings.append(f"extreme 1h price move ({price_1h:+.0f}%) — pump/dump signature")
    return _clamp(score), findings


_SIGNAL_FNS = {
    "deployer_risk": _signal_deployer_risk,
    "holder_concentration": _signal_holder_concentration,
    "liquidity": _signal_liquidity,
    "volume_authenticity": _signal_volume_authenticity,
    "bundle_detection": _signal_bundle_detection,
    "tokenomics": _signal_tokenomics,
    "market_microstructure": _signal_market_microstructure,
}


def _time_horizon(probability: float) -> str:
    for threshold, label in _TIME_HORIZONS:
        if probability >= threshold:
            return label
    return "weeks"


def _similar_rugs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Similar confirmed rugs from the scan's RAG context (best-effort)."""
    rag = payload.get("rag_context")
    if not isinstance(rag, dict):
        return []
    out: list[dict[str, Any]] = []
    for h in rag.get("similar_scams") or []:
        if isinstance(h, dict):
            out.append(
                {
                    "content": h.get("content", "")[:200],
                    "score": h.get("score"),
                    "collection": h.get("collection"),
                    "doc_id": h.get("doc_id"),
                }
            )
    return out[:5]


async def predict_rug_probability(scan_result: dict[str, Any], chain: str, address: str) -> dict[str, Any]:
    """AI-powered rug probability prediction from an advanced scan result.

    Fuses 7 signals from the scan result (see module docstring) into a
    weighted rug probability with confidence, time horizon, contributing
    signals, natural-language reasoning, and similar confirmed rugs from RAG.

    Returns::

        {
            "rug_probability": 0-100,
            "confidence": 0-100,
            "time_horizon": "imminent" | "hours" | "days" | "weeks",
            "key_signals": [list of contributing signals],
            "reasoning": "natural language explanation",
            "similar_rugs": [list of similar confirmed rugs from RAG],
        }
    """
    payload = scan_result if isinstance(scan_result, dict) else {}

    signal_scores: dict[str, float] = {}
    signal_findings: dict[str, list[str]] = {}
    available = 0
    for name, fn in _SIGNAL_FNS.items():
        score, findings = fn(payload)
        signal_scores[name] = score
        signal_findings[name] = findings
        if score > 0 or findings:
            available += 1

    # Weighted fusion. When few signals have data, renormalize over the
    # available weights so a sparse scan doesn't silently under-score.
    active_weight = sum(w for n, w in _WEIGHTS.items() if signal_scores[n] > 0 or signal_findings[n])
    if active_weight <= 0:
        probability = 0.0
    else:
        raw = sum(signal_scores[n] * w for n, w in _WEIGHTS.items() if signal_scores[n] > 0 or signal_findings[n])
        probability = _clamp(raw / active_weight * sum(_WEIGHTS.values()))

    # Confidence: how much of the 7-signal surface actually had data.
    confidence = _clamp(available / len(_SIGNAL_FNS) * 100)

    # Key signals: contributing signals sorted by weighted contribution.
    contributions = sorted(
        ((n, signal_scores[n] * _WEIGHTS[n], signal_findings[n]) for n in _WEIGHTS),
        key=lambda t: t[1],
        reverse=True,
    )
    key_signals = [
        {"signal": n, "score": round(s, 1), "weight": _WEIGHTS[n], "findings": f}
        for n, s, f in contributions
        if s > 0 or f
    ]

    horizon = _time_horizon(probability)
    top = key_signals[:3]
    top_text = ", ".join(s["signal"].replace("_", " ") for s in top) if top else "no significant risk signals"
    reasoning = (
        f"Rug probability {probability:.0f}/100 ({horizon} horizon) for {chain}:{address}. "
        f"Primary drivers: {top_text}. "
        f"Confidence {confidence:.0f}% — {available}/7 signal families had data."
    )

    return {
        "rug_probability": round(probability, 1),
        "confidence": round(confidence, 1),
        "time_horizon": horizon,
        "key_signals": key_signals,
        "reasoning": reasoning,
        "similar_rugs": _similar_rugs(payload),
    }
