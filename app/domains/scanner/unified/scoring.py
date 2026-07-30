"""Token safety scoring — standalone function extracted from UnifiedTokenScanner."""

from __future__ import annotations

from app.domains.scanner.unified.model import ScanResult


def compute_score(r: ScanResult) -> None:
    """Compute safety score (100 = safest, 0 = scam).

    Starts at 50 (neutral/unknown). Adjusted by security checks, holder data,
    liquidity, rug patterns, premium detections.

    Confidence calculation (0-100):
    - Base confidence from number of modules that attempted (even if no data):
      each module in modules_run gives +5 base confidence (max 40)
    - Bonus confidence for modules that returned actual results:
      each module with data adds +5 to +15 depending on data richness
    - This ensures confidence doesn't drop to zero just because a token
      has limited data (e.g., new token on a less-indexed chain).
    """
    score = 50.0
    confidence = 0.0
    flags: list[str] = []
    green: list[str] = []

    # ── Base confidence: modules that were attempted ──
    # Even if modules return no data, running them counts
    modules_attempted = len(r.modules_run)
    base_confidence = min(modules_attempted * 5, 40)
    confidence += base_confidence

    # ── Security checks (GoPlus) ──
    if r.security:
        checks = r.security.get("checks", {})
        if checks:
            confidence += min(len(checks) * 2, 30)
            for check_id, check in checks.items():
                status = check.get("status", "")
                if status == "fail":
                    score -= 5
                    flags.append(check_id)
                elif status == "pass":
                    score += 2
                    green.append(check_id)
        else:
            # Security module ran but had no checks
            confidence += 10

    # ── Holder concentration ──
    if r.holders:
        h = r.holders
        top10 = float(h.get("top_10_pct", 0) or 0)
        if top10 > 80:
            score -= 15
            flags.append("HOLDER_CONCENTRATION_HIGH")
        elif top10 < 30:
            score += 5
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
            score -= 10
            flags.append("HOLDER_GINI_HIGH")
        confidence += 5
    else:
        confidence += 3

    # ── Liquidity ──
    if r.liquidity:
        liq = r.liquidity
        locked = float(liq.get("locked_pct", 0) or 0)
        if locked < 50:
            score -= 20
            flags.append("LP_LOCK_LOW")
        elif locked > 90:
            score += 5
            green.append("LP_LOCKED")
        confidence += 5
    else:
        confidence += 3

    # ── Dev reputation ──
    if r.dev_reputation:
        dev = r.dev_reputation
        risk = float(dev.get("risk_score", 0) or 0)
        if risk > 70:
            score -= 25
            flags.append("DEV_HIGH_RISK")
        elif risk < 20:
            score += 5
            green.append("DEV_REPUTABLE")
        confidence += 5
    else:
        confidence += 3

    # ── Rug patterns ──
    if r.rug_patterns:
        rp = r.rug_patterns
        matches = int(rp.get("match_count", 0) or 0)
        if matches > 0:
            score -= matches * 10
            flags.append(f"RUG_PATTERN_MATCH:{matches}")
        confidence += 5
    else:
        confidence += 3

    # ── Volume authenticity ──
    if r.volume_auth:
        va = r.volume_auth
        fake_pct = float(va.get("fake_volume_pct", 0) or 0)
        if fake_pct > 50:
            score -= 20
            flags.append("VOLUME_FAKE")
        elif fake_pct < 10:
            score += 5
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
            score -= min(total_risk, 40)
            for f in findings:
                pattern = f.get("pattern", "unknown")
                if f.get("detected"):
                    flags.append(f"ONCHAIN_{pattern.upper()}")
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
                    score -= 10
                    flags.append(f"PREMIUM_{feature.upper()}")
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
