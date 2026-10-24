"""Human-readable renderers for rug-risk score results.

Pure renderers: take a result dict, return a string. The signal→group map
and the verdict bands are read from the resolved policy
(:mod:`.risk_policy`), so the display can never drift from the scoring that
actually ran, and this module carries no calibration of its own.
"""

from __future__ import annotations

from typing import Any

from .risk_policy import load_policy

_VERDICT_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

_FLAG_TEXT: dict[str, str] = {
    "honeypot": "honeypot",
    "known_scammer": "deployer is known scammer",
    "rug_history": "rug history",
    "mintable": "mintable",
    "lp_removed": "LP removed",
    "bad_funder": "bad funder",
    "mint_supply_risk": "mint supply risk",
    "bundle_suspect": "bundled launch",
    "repeat_deployer": "repeat rug deployer",
    "lp_unlocked": "LP unlocked",
    "wash_trading": "wash trading",
    "freezable": "freezable",
    "coordinated_shill": "coordinated shill",
    "proxy_upgradable": "proxy upgradable",
    "supply_concentrated": "supply concentrated",
    "whale_exit": "whale exit",
    "kol_pump": "KOL pump",
    "ownership_transferable": "ownership transferable",
    "sniper_cluster": "sniper cluster",
    "low_liquidity": "low liquidity",
    "very_young": "very young token",
    "smart_money_exit": "smart money exit",
    "burn_disabled": "burn disabled",
    "volume_concentrated": "volume concentrated",
    "fresh_wallet": "fresh wallet",
    "renounced": "ownership not renounced",
    "lp_burned_pct": "LP mostly unburned",
    "single_pool": "single pool",
    "kol_verified": "KOL verified",
}

#: Display order of analysis groups.
_GROUP_ORDER = ("CONTRACT", "SUPPLY", "LIQUIDITY", "TRADING", "DEPLOYER", "SOCIAL", "MOMENTUM", "MITIGATION")

#: signal -> group (derived from the rule table + the KOL mitigation).
_GROUP_OF: dict[str, str] = dict(load_policy().groups)


def _verdict_for(score: int) -> str:
    return load_policy().verdict_for(score)


def risk_verdict_text(result: dict[str, Any]) -> str:
    """Render a one-line verdict, e.g. ``🔴 CRITICAL (75/100) — honeypot · mintable``.

    Displays the top 3 risk flags (``kol_verified`` is shown only when it
    is the only flag). Clean results render ``no significant risk flags``.
    """
    if not isinstance(result, dict):
        result = {}
    score = int(result.get("score") or 0)
    verdict = str(result.get("verdict") or _verdict_for(score))
    flags = [str(flag) for flag in (result.get("flags") or [])]
    shown = [flag for flag in flags if flag != "kol_verified"] or flags
    shown = shown[:3]

    emoji = _VERDICT_EMOJI.get(verdict, "🔴")
    label = verdict.upper()
    if not shown:
        return f"{emoji} {label} ({score}/100) — no significant risk flags"
    parts = " · ".join(_FLAG_TEXT.get(flag, flag) for flag in shown)
    return f"{emoji} {label} ({score}/100) — {parts}"


def risk_analysis_text(result: dict[str, Any]) -> str:
    """Render the full multi-line "25-point" breakdown of a score result.

    Example::

        Risk Score: 92/100 — CRITICAL
        • CONTRACT: honeypot (+40) — contract is a honeypot (cannot sell)
        • DEPLOYER: known scammer (+40) — deployer is a known scammer

    Groups are rendered in a fixed order; the KOL mitigation appears with
    a negative weight. Clean results render ``no significant risk flags``.
    """
    if not isinstance(result, dict):
        result = {}
    score = int(result.get("score") or 0)
    verdict = str(result.get("verdict") or _verdict_for(score))
    analysis = result.get("analysis") or []

    lines = [f"Risk Score: {score}/100 — {verdict.upper()}"]
    if not analysis:
        lines.append("no significant risk flags")
        return "\n".join(lines)

    by_group: dict[str, list[tuple[str, int, str]]] = {}
    for item in analysis:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("signal") or "")
        try:
            weight = int(item.get("weight") or 0)
        except (TypeError, ValueError):
            weight = 0
        by_group.setdefault(_GROUP_OF.get(signal, "OTHER"), []).append((signal, weight, str(item.get("detail") or "")))

    for group in _GROUP_ORDER:
        for signal, weight, detail in by_group.get(group, []):
            label = _FLAG_TEXT.get(signal, signal)
            signed = f"(+{weight})" if weight >= 0 else f"({weight})"
            line = f"• {group}: {label} {signed}"
            if detail:
                line += f" — {detail}"
            lines.append(line)
    return "\n".join(lines)
