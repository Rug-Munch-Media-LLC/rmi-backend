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

from app.domains.scanner.engines.news_sentiment import HIGH_MEDIA_ATTENTION_THRESHOLD

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

# Blue-chip tokens (top-100 by market cap, or >100K holders) legitimately
# carry signals the concentration/lock heuristics treat as risk: high HHI
# (a single wrapping contract can dominate supply — WBTC, wstETH) and low
# locked-LP% (liquidity sits in deep AMM pools, not locked LP positions).
# Skipping those flags for bluechips removes false HIGH/critical labels on
# tokens like WETH (Bug 1).
BLUECHIP_MARKET_CAP_USD = 1_000_000_000  # ≈ top-100 by market cap
BLUECHIP_HOLDER_COUNT = 100_000

# Hardcoded allowlist of known blue-chip tokens (mainnet addresses).
# These bypass SENTINEL concentration/low-lock/wash/honeypot heuristics even
# when market-cap / holder data is unavailable (data fetch OOMs or is rate
# limited). Keys are checksummed mainnet addresses; matched case-insensitively.
BLUECHIP_ADDRESSES: dict[str, str] = {
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2": "WETH",
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599": "WBTC",
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "USDC",
    "0xdAC17F958D2ee523a2206206994597C13D831ec7": "USDT",
    "0x6B175474E89094C44Da98b954EedeAC495271d0F": "DAI",
    "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84": "stETH",
}

# Lowercased lookup set — built once at import; addresses matched case-insensitively.
_BLUECHIP_ADDRESSES_LOWER: frozenset[str] = frozenset(a.lower() for a in BLUECHIP_ADDRESSES)


def _is_bluechip(r: ScanResult) -> bool:
    """True for known blue-chip / top-100 market-cap or >100K-holder tokens.

    The hardcoded ``BLUECHIP_ADDRESSES`` allowlist is checked FIRST so known
    blue-chips (WETH, WBTC, USDC, USDT, DAI, stETH) are recognized even when
    market-cap / holder enrichment data is unavailable (data fetch OOMs).
    Falls through to data-dependent checks (``market_cap_usd``, DataBus
    holder data, SENTINEL free-tier ``holder_analysis.total_holders``).
    Defensive: missing or malformed fields simply mean "not bluechip".
    """
    address = (r.token_address or "").strip().lower()
    if address in _BLUECHIP_ADDRESSES_LOWER:
        return True
    if float(r.market_cap_usd or 0) >= BLUECHIP_MARKET_CAP_USD:
        return True
    holders = r.holders if isinstance(r.holders, dict) else {}
    for key in ("holder_count", "total_holders"):
        try:
            if int(holders.get(key) or 0) > BLUECHIP_HOLDER_COUNT:
                return True
        except (TypeError, ValueError):
            continue
    sentinel_free = (r.sentinel or {}).get("free") or {}
    ha = sentinel_free.get("holder_analysis")
    if isinstance(ha, dict):
        try:
            if int(ha.get("total_holders") or 0) > BLUECHIP_HOLDER_COUNT:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _risk_level_from_score(score: float) -> str:
    """Map a 0-100 safety score to the canonical risk level."""
    if score >= 80:
        return "safe"
    if score >= 60:
        return "low"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "high"
    return "critical"


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

    # ── SENTINEL deep-forensic findings (Phase 5) ──
    if r.sentinel and isinstance(r.sentinel, dict):
        sfree = r.sentinel.get("free") or {}
        sentinel_ev: dict[str, Any] = {}

        ha = sfree.get("holder_analysis")
        if isinstance(ha, dict) and ha.get("hhi") is not None:
            sentinel_ev["holder_analysis"] = {
                "hhi": ha.get("hhi"),
                "risk_level": ha.get("risk_level"),
                "fake_diversification_score": ha.get("fake_diversification_score"),
                "total_holders": ha.get("total_holders"),
            }
        lv = sfree.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            sentinel_ev["liquidity_verification"] = {
                "total_locked_percentage": lv.get("total_locked_percentage"),
                "risk_level": lv.get("risk_level"),
                "fake_lock_detected": lv.get("fake_lock_detected"),
                "lp_burned": lv.get("lp_burned"),
            }
        dr = sfree.get("dev_reputation")
        if isinstance(dr, dict) and dr.get("reputation_score") is not None:
            sentinel_ev["dev_reputation"] = {
                "reputation_score": dr.get("reputation_score"),
                "reputation_level": dr.get("reputation_level"),
                "dev_threat_index": dr.get("dev_threat_index"),
                "rug_rate": dr.get("rug_rate"),
                "serial_patterns": dr.get("serial_patterns"),
            }
        ca = sfree.get("contract_authority")
        if isinstance(ca, dict) and ca.get("risk_level"):
            sentinel_ev["contract_authority"] = {
                "risk_score": ca.get("risk_score"),
                "risk_level": ca.get("risk_level"),
            }
        if sentinel_ev:
            ev["sentinel"] = sentinel_ev
        if r.sentinel_boost:
            sentinel_ev["sentinel_boost"] = r.sentinel_boost

    # ── Market context (Part 1 — market-aware scanning) ──
    if r.market_context is not None:
        ev["market_context"] = r.market_context.to_dict()

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

    # Blue-chip guard: wrapped/blue-chip tokens (top-100 market cap or
    # >100K holders) are not penalized for concentration/low-lock signals
    # that are structurally normal for them (Bug 1).
    bluechip = _is_bluechip(r)

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
        if top10 > 80 and not bluechip:
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
        if gini > 0.8 and not bluechip:
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
        if locked < 50 and not bluechip:
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

    # ── SENTINEL deep-forensic findings (Phase 5) ──
    # SENTINEL free-tier modules report alongside the DataBus chains; their
    # findings feed the SAME five dimensions so a critical deep-forensic
    # signal moves the unified score (not just the SENTINEL report). A
    # ``sentinel_boost`` is applied when two or more modules independently
    # flag critical/high risk — correlated deep-forensic signals reinforce
    # each other.
    if r.sentinel and isinstance(r.sentinel, dict):
        sentinel_free = r.sentinel.get("free") or {}
        sentinel_critical: list[str] = []

        # holder_analysis (HHIReport) → holders dimension
        ha = sentinel_free.get("holder_analysis")
        if isinstance(ha, dict) and ha.get("hhi") is not None:
            hhi = float(ha.get("hhi", 0) or 0)
            ha_risk = str(ha.get("risk_level", "")).lower()
            # HHI > 5000 is normal for wrapped/blue-chip tokens (one
            # wrapping contract dominates supply) — skip the SENTINEL HHI
            # flags for bluechips (Bug 1).
            if not bluechip and (hhi > 5000 or ha_risk == "critical"):
                _apply("holders", -10, "SENTINEL_HHI_CRITICAL")
                sentinel_critical.append("holder_analysis")
            elif not bluechip and (hhi > 3000 or ha_risk == "high"):
                _apply("holders", -5, "SENTINEL_HHI_HIGH")
            fake_div = float(ha.get("fake_diversification_score", 0) or 0)
            if fake_div > 40:
                _apply("holders", -5, "SENTINEL_FAKE_DIVERSIFICATION")
            if ha.get("total_holders") is not None:
                confidence += 5

        # liquidity_verification (LockReport) → liquidity dimension
        lv = sentinel_free.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            locked = float(lv.get("total_locked_percentage", 0) or 0)
            lv_risk = str(lv.get("risk_level", "")).upper()
            fake_lock = bool(lv.get("fake_lock_detected"))
            # LP lock < 50% is normal for wrapped/blue-chip tokens (deep
            # AMM pools instead of locked LP positions) — skip the lock
            # penalty for bluechips unless a real fake-lock/CRITICAL
            # finding exists (Bug 1).
            if (not bluechip and locked < 50) or lv_risk == "CRITICAL" or fake_lock:
                _apply("liquidity", -10, "SENTINEL_LP_LOCK")
                if lv_risk == "CRITICAL" or fake_lock:
                    sentinel_critical.append("liquidity_verification")
            elif locked > 90:
                _apply("liquidity", 3, "SENTINEL_LP_LOCKED")
            if lv.get("warnings"):
                confidence += 3

        # dev_reputation (DevReputationReport) → deployer dimension
        dr = sentinel_free.get("dev_reputation")
        if isinstance(dr, dict) and dr.get("reputation_score") is not None:
            rep_score = float(dr.get("reputation_score", 50) or 50)
            threat = float(dr.get("dev_threat_index", 0) or 0)
            if rep_score < 30 or threat > 0.5:
                _apply("deployer", -10, "SENTINEL_DEV_THREAT")
                sentinel_critical.append("dev_reputation")
            elif rep_score >= 80:
                _apply("deployer", 3, "SENTINEL_DEV_CLEAN")
            if dr.get("total_launches") is not None:
                confidence += 5

        # contract_authority / proxy_detection / metadata_fingerprint → security dimension
        ca = sentinel_free.get("contract_authority")
        if isinstance(ca, dict) and str(ca.get("risk_level", "")).upper() == "CRITICAL":
            _apply("security", -5, "SENTINEL_CONTRACT_AUTHORITY")
            sentinel_critical.append("contract_authority")
        px = sentinel_free.get("proxy_detection")
        if isinstance(px, dict) and (
            str(px.get("risk_level", "")).upper() == "CRITICAL" or (px.get("can_upgrade") and px.get("is_proxy"))
        ):
            _apply("security", -5, "SENTINEL_PROXY_RISK")
            sentinel_critical.append("proxy_detection")
        mf = sentinel_free.get("metadata_fingerprint")
        if isinstance(mf, dict) and len(mf.get("similar_tokens") or []) >= 3:
            _apply("security", -5, "SENTINEL_METADATA_CLONE")

        # sentinel_boost — correlated critical deep-forensic signals reinforce.
        if len(sentinel_critical) >= 2:
            boost = min(15.0, 5.0 * len(sentinel_critical))
            _apply("security", -boost, f"SENTINEL_CRITICAL_BOOST:{len(sentinel_critical)}")
            r.sentinel_boost = boost

    # ── Clamp ──
    r.safety_score = max(0.0, min(100.0, score))
    r.confidence = min(confidence, 100.0)

    # ── Risk level ──
    r.risk_level = _risk_level_from_score(r.safety_score)

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


# ── News-driven risk adjustment (post-scoring enrichment) ─────────────


def _apply_news_delta(r: ScanResult, delta: float, flag: str, *, risk: bool) -> None:
    """Apply a news delta to the safety score, flags, and market dimension."""
    r.safety_score = max(0.0, min(100.0, r.safety_score + delta))
    if risk:
        if flag not in r.risk_flags:
            r.risk_flags.append(flag)
    else:
        if flag not in r.green_flags:
            r.green_flags.append(flag)

    bd = r.breakdown
    if isinstance(bd, dict):
        market = bd.get("market")
        if isinstance(market, dict):
            market["delta"] = round(float(market.get("delta", 0.0) or 0.0) + delta, 1)
            max_points = float(market.get("max", DIMENSION_WEIGHTS["market"]))
            market["score"] = round(max(0.0, min(max_points, max_points + market["delta"])), 1)
            flags = market.setdefault("flags", [])
            if flag not in flags:
                flags.append(flag)


def apply_news_scoring(r: ScanResult) -> None:
    """Apply news-driven bonus/penalty after ``compute_score``.

    ``compute_score`` runs before the news engines populate
    ``news_sentiment``/``news_card``, so this post-pass adjusts the safety
    score once the news data exists. The scanner calls it immediately after
    the news engines, so the final score/level/flags are all consistent
    before persistence and Telegram rendering.

    Rules:
      - High media attention (trending > 70) + BEARISH news → additional
        risk (-5, ``NEWS_BEARISH_HIGH_ATTENTION`` red flag).
      - High media attention + BULLISH news → reduced risk (+5,
        ``NEWS_BULLISH_HIGH_ATTENTION`` green flag).
      - Corroborated news (3+ distinct sources) with on-chain evidence →
        increased confidence (+5).

    Mutates ``r`` in place; no-op when there is no scanned news data.
    """
    ns = r.news_sentiment
    if not isinstance(ns, dict) or ns.get("result") != "scanned":
        return

    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    trending = int(ns.get("trending_score", 0) or 0)
    if trending <= HIGH_MEDIA_ATTENTION_THRESHOLD:
        return

    if sentiment == "BEARISH":
        _apply_news_delta(r, -5.0, "NEWS_BEARISH_HIGH_ATTENTION", risk=True)
    elif sentiment == "BULLISH":
        _apply_news_delta(r, 5.0, "NEWS_BULLISH_HIGH_ATTENTION", risk=False)

    card = r.news_card
    if isinstance(card, dict):
        corr = card.get("corroboration") or {}
        total_sources = int(corr.get("total_sources", 0) or 0)
        if total_sources >= 3 and corr.get("on_chain_evidence"):
            r.confidence = min(100.0, r.confidence + 5)

    r.risk_level = _risk_level_from_score(r.safety_score)


# ── Market-aware risk adjustment (post-scoring enrichment) ────────────
# Market context is fetched BEFORE the scan phases, so the penalty/bonus is
# applied in a second pass (after compute_score) that keeps score/flags/
# breakdown in sync — the same pattern as apply_news_scoring.


def _apply_market_delta(r: ScanResult, delta: float, flag: str, *, risk: bool) -> None:
    """Apply a market delta to the safety score, flags, and market dimension."""
    r.safety_score = max(0.0, min(100.0, r.safety_score + delta))
    if risk:
        if flag not in r.risk_flags:
            r.risk_flags.append(flag)
    else:
        if flag not in r.green_flags:
            r.green_flags.append(flag)

    bd = r.breakdown
    if isinstance(bd, dict):
        market = bd.get("market")
        if isinstance(market, dict):
            market["delta"] = round(float(market.get("delta", 0.0) or 0.0) + delta, 1)
            max_points = float(market.get("max", DIMENSION_WEIGHTS["market"]))
            market["score"] = round(max(0.0, min(max_points, max_points + market["delta"])), 1)
            flags = market.setdefault("flags", [])
            if flag not in flags:
                flags.append(flag)


def apply_market_scoring(r: ScanResult) -> None:
    """Apply market-based risk adjustments after ``compute_score``.

    Uses ``r.market_context`` (fetched from RugBrain before the scan):

      - Fearful market (fear_index < 40) + new token (deployer freshness
        "new" or age < 48h) → higher risk (``MARKET_FEAR_NEW_TOKEN`` -5).
      - Recent exploits in the same category (recent_exploits > 0) →
        higher risk (``MARKET_RECENT_EXPLOITS`` -5, capped -10).
      - Rising chain volume (TVL change > +5%) is mildly constructive
        (``MARKET_CHAIN_GROWTH`` +3); falling volume (< -5%) is a mild
        risk (``MARKET_CHAIN_CONTRACTION`` -3).
      - High social velocity (>= 60) on a *new* token is a mild red flag
        (possible coordinated shill) (``MARKET_SOCIAL_PUMP`` -3).

    Mutates ``r`` in place; no-op when there is no market context.
    """
    mc = r.market_context
    if mc is None:
        return

    fear = float(mc.fear_index or 50)
    exploits = int(mc.recent_exploits or 0)
    chain_vol = float(mc.chain_volume_24h or 0)
    social = float(mc.social_velocity or 0)
    freshness = str(mc.deployer_freshness or "unknown")
    age_hours = float(getattr(r, "age_hours", 0) or 0)
    is_new = freshness == "new" or (0 < age_hours < 48)

    # Fearful market + new token → higher risk.
    if fear < 40 and is_new:
        _apply_market_delta(r, -5.0, "MARKET_FEAR_NEW_TOKEN", risk=True)

    # Recent exploits in the same category → higher risk (bounded).
    if exploits > 0:
        penalty = min(-5.0 * min(exploits, 2), -10.0)
        _apply_market_delta(r, penalty, "MARKET_RECENT_EXPLOITS", risk=True)

    # Chain volume (TVL) direction.
    if chain_vol > 5:
        _apply_market_delta(r, 3.0, "MARKET_CHAIN_GROWTH", risk=False)
    elif chain_vol < -5:
        _apply_market_delta(r, -3.0, "MARKET_CHAIN_CONTRACTION", risk=True)

    # High social velocity on a new token — possible coordinated shill.
    if social >= 60 and is_new:
        _apply_market_delta(r, -3.0, "MARKET_SOCIAL_PUMP", risk=True)

    r.risk_level = _risk_level_from_score(r.safety_score)
