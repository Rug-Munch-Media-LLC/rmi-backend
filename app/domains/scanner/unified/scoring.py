"""Token safety scoring — multi-dimensional, explainable risk scoring.

Extracted from ``UnifiedTokenScanner`` and evolved into the canonical 0-100
*safety* score (100 = safest, 0 = scam). Lower scores mean higher risk.

Model
-----
The unified engine is a **multi-dimensional signal aggregation** model:

1. Start at a neutral baseline of ``50`` (unknown/neutral).
2. Each signal contributes a flat penalty (risk finding) or bonus (green
   finding) that is attributed to one of five dimensions:
   ``security``, ``liquidity``, ``holders``, ``deployer``, ``market``.
3. The final safety score is ``clamp(0, 100, baseline + sum(deltas))``.
4. Every dimension is reported with its surviving points (``score`` out of
   ``max``), its net ``delta``, and the ``flags`` that caused it, so the
   final number can always be traced back to specific findings.

The five dimension weights (35/25/15/15/10) are the maximum points each
dimension can contribute toward safety and match the tiered product model
documented in ``docs/RISK_SCORING.md``::

    CORE     = security + liquidity          (60% of weight)
    DEEP     = CORE + holders + deployer     (90% of weight)
    PREMIUM  = DEEP + market                 (100% of weight)

Sibling engines
---------------
- ``app.domains.scanner.service.scoring._calculate_safety_score`` — the
  legacy module-results engine (baseline 50 + flat penalties/bonuses).
- ``app.domains.scanners.sentinel_pipeline.risk._compute_composite`` — the
  SENTINEL weighted composite, a 0-100 *risk* score (higher = riskier, the
  inverse of safety).

Confidence (0-100)
------------------
- Base confidence from modules that attempted (each module in
  ``modules_run`` gives +5, capped at 40).
- Bonus confidence for modules that returned real data (+5 to +15 depending
  on richness).
- Ensures confidence never drops to zero just because a token has limited
  data (e.g., a new token on a less-indexed chain).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

# Dimension weights — the maximum points each dimension can contribute
# toward the safety score. 35 + 25 + 15 + 15 + 10 = 100.
DIMENSION_WEIGHTS: dict[str, int] = {
    "security": 35,
    "liquidity": 25,
    "holders": 15,
    "deployer": 15,
    "market": 10,
}

# Neutral baseline: a token with no data at all scores exactly 50.
_BASELINE = 50.0


def _build_evidence(r: ScanResult) -> dict[str, Any]:
    """Extract the raw findings backing each dimension (for explainability).

    Defensive: every field is optional; missing data yields an empty dict.
    The result is the ``evidence`` payload attached to a scan so users can
    audit exactly why a token scored the way it did.
    """
    ev: dict[str, Any] = {}

    # ── Security: GoPlus / token_security checks + rug-pattern matches ──
    sec: dict[str, Any] = {}
    if r.security and isinstance(r.security, dict):
        checks = r.security.get("checks")
        if isinstance(checks, dict):
            sec["checks"] = {
                cid: {"status": c.get("status", ""), "details": c.get("details", "")} for cid, c in checks.items()
            }
        for key in ("score", "band", "category_breakdown"):
            if key in r.security:
                sec[key] = r.security[key]
    if r.rug_patterns and isinstance(r.rug_patterns, dict):
        sec["rug_patterns"] = {
            "match_count": r.rug_patterns.get("match_count", 0),
            "patterns": r.rug_patterns.get("patterns") or r.rug_patterns.get("matches", []),
        }
    if sec:
        ev["security"] = sec

    # ── Liquidity ──
    if r.liquidity and isinstance(r.liquidity, dict):
        ev["liquidity"] = {
            "locked_pct": r.liquidity.get("locked_pct"),
            "liquidity_usd": r.liquidity.get("liquidity_usd", r.liquidity.get("total_liquidity")),
        }

    # ── Holders ──
    if r.holders and isinstance(r.holders, dict):
        ev["holders"] = {
            "top_10_pct": r.holders.get("top_10_pct"),
            "holder_count": r.holders.get("holder_count"),
        }
    if r.holder_health and isinstance(r.holder_health, dict):
        ev["holder_health"] = {
            "gini": r.holder_health.get("gini"),
            "hhi": r.holder_health.get("hhi"),
        }
    if r.funding_analysis and isinstance(r.funding_analysis, dict):
        ev["funding_analysis"] = {
            "exchange_funded_pct": r.funding_analysis.get("exchange_funded_pct"),
            "fresh_wallet_pct": r.funding_analysis.get("fresh_wallet_pct"),
            "mixer_funded_pct": r.funding_analysis.get("mixer_funded_pct"),
            "risk_assessment": r.funding_analysis.get("risk_assessment"),
            "top_funders": r.funding_analysis.get("top_funders"),
        }
    if r.wallet_freshness and isinstance(r.wallet_freshness, dict):
        ev["wallet_freshness"] = {
            "fresh_wallet_pct": r.wallet_freshness.get("fresh_wallet_pct"),
            "avg_freshness_score": r.wallet_freshness.get("avg_freshness_score"),
            "analyzed": r.wallet_freshness.get("analyzed"),
        }
    if r.holder_overlap and isinstance(r.holder_overlap, dict):
        ev["holder_overlap"] = {
            "max_overlap_pct": r.holder_overlap.get("max_overlap_pct"),
            "worst_token": r.holder_overlap.get("worst_token"),
            "worst_token_is_scam": r.holder_overlap.get("worst_token_is_scam"),
            "risk_assessment": r.holder_overlap.get("risk_assessment"),
        }

    # ── Deployer ──
    if r.dev_reputation and isinstance(r.dev_reputation, dict):
        ev["deployer"] = {
            "deployer_address": r.dev_reputation.get("deployer_address") or r.dev_reputation.get("address"),
            "risk_score": r.dev_reputation.get("risk_score"),
            "risk_label": r.dev_reputation.get("risk_label") or r.dev_reputation.get("reputation_label"),
            "rug_count": r.dev_reputation.get("rug_count"),
            "total_deployments": r.dev_reputation.get("total_deployments") or r.dev_reputation.get("deployment_count"),
        }

    # ── Market ──
    mkt: dict[str, Any] = {}
    if r.volume_auth and isinstance(r.volume_auth, dict):
        mkt["fake_volume_pct"] = r.volume_auth.get("fake_volume_pct")
    if r.premium and isinstance(r.premium, dict):
        mkt["premium"] = {
            feature: {"risk": d.get("risk", d.get("risk_level", "")) if isinstance(d, dict) else d}
            for feature, d in r.premium.items()
            if isinstance(d, dict) and d
        }
    if r.chain_indexer_patterns and isinstance(r.chain_indexer_patterns, dict):
        findings = r.chain_indexer_patterns.get("findings")
        if findings is None:
            data = r.chain_indexer_patterns.get("data")
            findings = data.get("findings") if isinstance(data, dict) else None
        mkt["chain_indexer"] = findings or []
    if mkt:
        ev["market"] = mkt

    return ev


def compute_score(r: ScanResult) -> dict[str, Any]:
    """Compute the 0-100 safety score and return an explainable breakdown.

    Mutates ``r`` in place (``safety_score``, ``confidence``, ``risk_level``,
    ``risk_flags``, ``green_flags``, ``breakdown``, ``evidence``) and returns
    the breakdown payload::

        {
            "score": 42.0,
            "risk_level": "high",
            "breakdown": {
                "security": {"score": 15.0, "max": 35, "delta": -20.0, "flags": ["CR03"]},
                "liquidity": {"score": 5.0, "max": 25, "delta": -20.0, "flags": ["LP_LOCK_LOW"]},
                ...
            },
            "evidence": {...}
        }

    Each dimension reports ``score`` (surviving points out of ``max``),
    ``delta`` (net contribution to the final score) and the flags that
    caused it. ``score = clamp(0, max, max + delta)``.
    """
    score = _BASELINE
    confidence = 0.0
    flags: list[str] = []
    green: list[str] = []

    deltas: dict[str, float] = defaultdict(float)
    dim_flags: dict[str, list[str]] = defaultdict(list)

    def _apply(dim: str, delta: float, flag: str | None = None) -> None:
        """Apply a signed delta to a dimension; record the flag if any."""
        nonlocal score
        score += delta
        deltas[dim] += delta
        if flag:
            dim_flags[dim].append(flag)
            flags.append(flag)

    # ── Base confidence: modules that were attempted ──
    # Even if modules return no data, running them counts
    modules_attempted = len(r.modules_run)
    base_confidence = min(modules_attempted * 5, 40)
    confidence += base_confidence

    # ── Security checks (GoPlus / token_security) ──
    if r.security:
        checks = r.security.get("checks", {})
        if checks:
            confidence += min(len(checks) * 2, 30)
            for check_id, check in checks.items():
                status = check.get("status", "")
                if status == "fail":
                    _apply("security", -5, str(check_id))
                elif status == "pass":
                    _apply("security", 2)
                    green.append(str(check_id))
        else:
            # Security module ran but had no checks
            confidence += 10

    # ── Holder concentration ──
    if r.holders:
        h = r.holders
        top10 = float(h.get("top_10_pct", 0) or 0)
        if top10 > 80:
            _apply("holders", -15, "HOLDER_CONCENTRATION_HIGH")
        elif top10 < 30:
            _apply("holders", 5)
            green.append("HOLDER_DISTRIBUTED")
        confidence += 5
    else:
        # Module attempted but no data
        confidence += 3

    # ── Holder health ──
    if r.holder_health:
        hh = r.holder_health
        gini = float(hh.get("gini", 0) or 0)
        if gini > 0.8:
            _apply("holders", -10, "HOLDER_GINI_HIGH")
        confidence += 5
    else:
        confidence += 3

    # ── Clever wallet analysis (funding sources / freshness / overlap) ──
    if r.funding_analysis and isinstance(r.funding_analysis, dict):
        fa = r.funding_analysis
        exchange_pct = float(fa.get("exchange_funded_pct", 0) or 0)
        fresh_pct = float(fa.get("fresh_wallet_pct", 0) or 0)
        mixer_pct = float(fa.get("mixer_funded_pct", 0) or 0)
        if exchange_pct > 50:
            _apply("holders", -10, "HIGH_EXCHANGE_FUNDED")
        elif exchange_pct > 30:
            _apply("holders", -5, "EXCHANGE_FUNDED_ELEVATED")
        if fresh_pct > 40:
            _apply("holders", -8, "HIGH_FRESH_WALLET_PCT")
        elif fresh_pct > 20:
            _apply("holders", -4, "FRESH_WALLET_ELEVATED")
        if mixer_pct > 5:
            _apply("holders", -6, "MIXER_FUNDED_HOLDERS")
        if fa.get("holders_analyzed", 0) > 0:
            confidence += 5

    if r.wallet_freshness and isinstance(r.wallet_freshness, dict):
        wf = r.wallet_freshness
        fresh_wallet_pct = float(wf.get("fresh_wallet_pct", 0) or 0)
        if fresh_wallet_pct > 50:
            _apply("holders", -10, "HIGH_FRESH_WALLET_PCT")
        elif fresh_wallet_pct > 30:
            _apply("holders", -5, "FRESH_WALLET_ELEVATED")
        if wf.get("result") == "scanned":
            confidence += 5

    if r.holder_overlap and isinstance(r.holder_overlap, dict):
        ho = r.holder_overlap
        max_overlap = float(ho.get("max_overlap_pct", 0) or 0)
        worst_is_scam = bool(ho.get("worst_token_is_scam"))
        if worst_is_scam and max_overlap > 30:
            _apply("holders", -10, "HOLDER_OVERLAP_SCAM")
        elif max_overlap > 30:
            _apply("holders", -5, "HOLDER_OVERLAP_HIGH")
        if ho.get("result") == "scanned":
            confidence += 5

    # ── Liquidity ──
    if r.liquidity:
        liq = r.liquidity
        locked = float(liq.get("locked_pct", 0) or 0)
        if locked < 50:
            _apply("liquidity", -20, "LP_LOCK_LOW")
        elif locked > 90:
            _apply("liquidity", 5)
            green.append("LP_LOCKED")
        confidence += 5
    else:
        confidence += 3

    # ── Dev reputation ──
    if r.dev_reputation:
        dev = r.dev_reputation
        risk = float(dev.get("risk_score", 0) or 0)
        if risk > 70:
            _apply("deployer", -25, "DEV_HIGH_RISK")
        elif risk < 20:
            _apply("deployer", 5)
            green.append("DEV_REPUTABLE")
        confidence += 5
    else:
        confidence += 3

    # ── Rug patterns ──
    if r.rug_patterns:
        rp = r.rug_patterns
        matches = int(rp.get("match_count", 0) or 0)
        if matches > 0:
            _apply("security", -10 * matches, f"RUG_PATTERN_MATCH:{matches}")
        confidence += 5
    else:
        confidence += 3

    # ── Volume authenticity ──
    if r.volume_auth:
        va = r.volume_auth
        fake_pct = float(va.get("fake_volume_pct", 0) or 0)
        if fake_pct > 50:
            _apply("market", -20, "VOLUME_FAKE")
        elif fake_pct < 10:
            _apply("market", 5)
            green.append("VOLUME_AUTHENTIC")
        confidence += 5
    else:
        confidence += 3

    # ── Chain-indexer pattern detection ──
    if r.chain_indexer_patterns:
        cip = r.chain_indexer_patterns
        findings = cip.get("findings", cip.get("data", {}).get("findings", []))
        if findings:
            total_risk = sum(f.get("risk_score", 0) for f in findings)
            _apply("market", -min(total_risk, 40))
            for f in findings:
                pattern = f.get("pattern", "unknown")
                if f.get("detected"):
                    _apply("market", 0, f"ONCHAIN_{pattern.upper()}")
            confidence += min(len(findings) * 3, 10)
        else:
            confidence += 5

    # ── Premium detections ──
    if r.premium:
        premium_flags = 0
        for feature, data in r.premium.items():
            if data and isinstance(data, dict):
                risk = data.get("risk", data.get("risk_level", ""))
                if risk in ("HIGH", "CRITICAL"):
                    _apply("market", -10, f"PREMIUM_{feature.upper()}")
                    premium_flags += 1
        confidence += min(premium_flags * 5, 15)

    # ── Clamp ──
    r.safety_score = max(0.0, min(100.0, score))
    r.confidence = min(confidence, 100.0)

    # ── Risk level ──
    if r.safety_score >= 80:
        r.risk_level = "safe"
    elif r.safety_score >= 60:
        r.risk_level = "low"
    elif r.safety_score >= 40:
        r.risk_level = "medium"
    elif r.safety_score >= 20:
        r.risk_level = "high"
    else:
        r.risk_level = "critical"

    r.risk_flags = flags
    r.green_flags = green

    # ── Explainable per-dimension breakdown ──
    breakdown: dict[str, Any] = {}
    for dim, weight in DIMENSION_WEIGHTS.items():
        delta = deltas.get(dim, 0.0)
        breakdown[dim] = {
            "score": round(max(0.0, min(float(weight), float(weight) + delta)), 1),
            "max": weight,
            "delta": round(delta, 1),
            "flags": dim_flags.get(dim, []),
        }

    evidence = _build_evidence(r)
    r.breakdown = breakdown
    r.evidence = evidence

    return {
        "score": r.safety_score,
        "risk_level": r.risk_level,
        "breakdown": breakdown,
        "evidence": evidence,
    }
