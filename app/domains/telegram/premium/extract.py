"""Scanner payload -> :class:`CardData`. Pure reads, no invented values.

The scanner emits two shapes and both must render:

* the deep report (``degen_security_scanner.models.SecurityReport.to_dict()``)
  with cohorts, bundlers, LP analysis, dev history;
* the core scan payload the legacy card already consumes (``risk_level``,
  ``risk_flags``, ``evidence.security.checks``).

Extraction tries the structured field first and the evidence string second.
A missing block stays ``None``; the renderer prints ``—``/``unknown`` and the
card is honest about what the scan did not measure.
"""

from __future__ import annotations

from app.domains.telegram.premium.model import CardData, Cohort
from app.domains.telegram.scan_card_fields import detail, field
from app.domains.telegram.scan_card_vocab import ACTION, REASONS

SEVERITY_OF_PROBABILITY = ((70, "CRITICAL"), (45, "HIGH"), (25, "MEDIUM"))


def _f(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _grade(result: dict) -> str:
    """Severity word: explicit level first, else derived from rug probability."""
    explicit = str(result.get("overall_risk") or result.get("risk_level") or "").upper()
    if explicit in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        return explicit
    prob = _f(result.get("rug_pull_probability"))
    if prob is None:
        return "UNKNOWN"
    return next((word for cut, word in SEVERITY_OF_PROBABILITY if prob >= cut), "LOW")


def _cohorts(result: dict) -> tuple[Cohort, ...]:
    """Team, insiders, snipers, bundle wallets — initial / sold / realised."""
    snipers = [s for s in result.get("snipers") or [] if isinstance(s, dict)]
    sniper_rows = [
        (
            float(s.get("buy_amount_sol") or 0),
            float(s.get("sold_percentage") or 0),
            float(s.get("pnl_percent") or 0),
        )
        for s in snipers
    ]
    sniper_sold = sum(buy * sold / 100 * (1 + pnl / 100) for buy, sold, pnl in sniper_rows)
    insiders = list(result.get("insiders_detected") or [])
    bundles = list(result.get("bundler_wallets") or [])
    return (
        Cohort("Team"),
        Cohort(
            "Insiders",
            wallets=len(insiders) or None,
            initial_sol=_f(result.get("insider_buy_volume")),
        ),
        Cohort(
            "Snipers",
            wallets=len(snipers) or None,
            initial_sol=sum(buy for buy, _, _ in sniper_rows) or None,
            sold_pct=(sum(s for _, s, _ in sniper_rows) / len(sniper_rows)) if snipers else None,
            realised_sol=sniper_sold or None,
            estimated=bool(snipers),
        ),
        Cohort(
            "Bundles",
            wallets=len(bundles) or None,
            initial_sol=_f(result.get("bundler_buy_volume_sol")),
        ),
    )


def _lp(result: dict) -> tuple[str, float | None]:
    """LP state word plus the locked share, from the deepest pool."""
    pools = [p for p in result.get("liquidity_pools") or [] if isinstance(p, dict)]
    pool = max(pools, key=lambda p: float(p.get("tvl_usd") or 0), default=None)
    if pool is None:
        locked = field(result, "liquidity", "lp_locked", default=field(result, "liquidity", "lp_burned"))
        if locked is None:
            return "unknown", None
        return ("burned" if locked else "not locked"), (100.0 if locked else 0.0)
    if pool.get("is_burned"):
        return "burned", 100.0
    share = _f(pool.get("lp_locked_percentage"))
    if pool.get("lock_expiry"):
        return f"locked to {pool['lock_expiry']}", share
    if share:
        return "locked", share
    return "not locked", 0.0


def _authority(value: object, renounced: object, known: bool) -> str | None:
    """'revoked', the authority address, or ``None`` when the scan had no key."""
    if renounced is True:
        return "revoked"
    if value:
        return str(value)
    return "revoked" if known else None


def _flags(result: dict) -> tuple[tuple[str, str], ...]:
    """Named, actionable flags — warnings first, then graded checks."""
    rows: list[tuple[str, str]] = [(str(w), "warning") for w in result.get("critical_warnings") or [] if str(w).strip()]
    checks = field(result, "evidence", "security", "checks", default={})
    graded = checks if isinstance(checks, dict) else {}
    for code in [str(c) for c in result.get("risk_flags") or []]:
        check = graded.get(code) or {}
        text = str(check.get("details") or "").strip()
        reason = REASONS.get(code) or text or f"Check {code}"
        rows.append((reason, str(check.get("status") or "flag")))
    for code, check in graded.items():
        if str(check.get("status")) in ("fail", "warning"):
            reason = REASONS.get(str(code)) or str(check.get("details") or f"Check {code}")
            rows.append((reason, str(check["status"])))
    seen: set[str] = set()
    unique = [(reason, status) for reason, status in rows if not (reason.lower() in seen or seen.add(reason.lower()))]
    return tuple(unique)


def extract(result: dict) -> CardData:
    """Normalise a scanner payload into :class:`CardData`."""
    address = str(result.get("token_address") or result.get("address") or "unknown")
    liquidity = _f(
        result.get("total_liquidity_usd")
        if result.get("total_liquidity_usd") is not None
        else field(result, "liquidity_usd", default=field(result, "liquidity", "liquidity_usd"))
    )
    top10 = _f(
        result.get("top_10_concentration")
        if result.get("top_10_concentration") is not None
        else field(
            result,
            "holder_concentration",
            "top10_pct",
            default=field(result, "top_holders", "top10_pct"),
        )
    )
    holders = _f(result.get("total_holders") or result.get("holder_count") or detail(result, "HD03", "Holders"))
    safety = _f(result.get("safety_score"))
    probability = _f(result.get("rug_pull_probability"))
    if safety is None and probability is not None:
        safety = max(0.0, 100.0 - probability)
    grade = _grade(result)
    lp_state, locked = _lp(result)
    mint = _authority(
        result.get("mint_authority"),
        result.get("mint_authority_renounced"),
        "mint_authority" in result,
    )
    freeze = _authority(
        result.get("freeze_authority"),
        result.get("freeze_authority_renounced"),
        "freeze_authority" in result,
    )
    depth = {f"{cut}%": (liquidity * cut / 200 if liquidity else None) for cut in (1, 5, 10)}
    warnings = [str(w) for w in result.get("critical_warnings") or [] if str(w).strip()]
    graded = [str(c) for c in result.get("risk_flags") or []]
    driver = warnings[0] if warnings else REASONS.get(graded[0], "") if graded else ""
    return CardData(
        address=address,
        symbol=str(result.get("token_symbol") or result.get("symbol") or "?").upper(),
        chain=str(result.get("chain") or "solana").upper(),
        severity=grade,
        safety=safety,
        determination=str(field(result, "risk_report", "determination", default="") or ""),
        action=ACTION.get(grade, "Insufficient data to grade."),
        driver=driver,
        confidence=_f(result.get("confidence")),
        liquidity_usd=liquidity,
        top10_pct=top10,
        volume_24h=_f(result.get("volume_24h") or result.get("volume_24h_usd")),
        holders=int(holders) if holders else None,
        price_usd=_f(result.get("price_usd")),
        change_24h=_f(result.get("price_change_24h")),
        cohorts=_cohorts(result),
        bundle_pct=_f(result.get("bundler_percentage_of_supply")),
        bundle_wallets=len(result.get("bundler_wallets") or []) or None,
        dev_holdings_pct=_f(result.get("dev_holdings_pct")),
        top10_addresses=tuple(
            (str(h.get("address")), float(h.get("percentage") or 0))
            for h in result.get("top_holders") or []
            if isinstance(h, dict)
        )[:10],
        lp_state=lp_state,
        lp_locked_pct=locked,
        impact_depth=depth,
        mint_authority=mint,
        freeze_authority=freeze,
        dev_tokens=len(result.get("dev_other_tokens") or []) or None,
        dev_rugs=int(result["dev_rug_history"]) if result.get("dev_rug_history") is not None else None,
        dev_scammer=result.get("dev_is_known_scammer"),
        kols=(),
        flags=_flags(result),
        link_label="DexScreener",
        link_url=str(
            result.get("dex_screener_url")
            or f"https://dexscreener.com/{str(result.get('chain') or 'solana').lower()}/{address}"
        ),
        scanned_at=str(result.get("scan_timestamp") or ""),
        duration_ms=int(result["scan_duration_ms"]) if result.get("scan_duration_ms") else None,
        source="degen" if result.get("overall_risk") else "core",
    )
