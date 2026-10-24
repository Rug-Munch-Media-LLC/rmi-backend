"""Consolidated rug-risk scorer — public orchestration.

Combines every enrichment signal (contract authority, supply, liquidity,
trading, deployer reputation, social cross-ref, whale flow, age) into a single
0-100 rug-risk score, one verdict, and a per-signal breakdown.

The *method* is not in this repository
---------------------------------------
This module is the public half of a seam (see ``risk_policy`` and
``standards/METHODOLOGY-PROTECTION.md`` § "The seam"):

* here — the contract, the engine, the orchestration, the typed result;
* private — the rule table (feature set + grouping), the per-signal weights,
  the firing thresholds, and the verdict bands.

A reader can therefore see *that* a token can be scored on a honeypot signal,
and call the scorer without credentials or a private dependency, but cannot
reproduce the calibration from this source tree. The engine is
``risk_policy.RulePolicy``; the numbers arrive through ``load_policy()``.

Design (unchanged by the extraction)
------------------------------------
* PURE — ``rug_risk_score(signals)`` only reads the ``signals`` dict: no I/O,
  no DB, no network, no sibling-module imports.
* DEFENSIVE — every signal is parsed tolerantly (bool, dict, str, number all
  accepted where sensible); missing/None values mean "absence of evidence",
  never a penalty. Malformed input never raises.
* KEY-TOLERANT — both flat values (``{"honeypot": True}``) and nested module
  output (``{"security": {"honeypot": True}}``) are accepted; the policy
  probes a list of key paths per signal.
* MONOTONIC — adding any risk signal can only raise the score; only the KOL
  mitigation subtracts.
* FAIL-OPEN — ``rug_risk_score(None)`` or a garbage dict returns a low-risk
  result (score 0, verdict "low", no flags, empty analysis).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._text import risk_analysis_text, risk_verdict_text
from .risk_policy import load_policy

if TYPE_CHECKING:
    from collections.abc import Mapping

from ._weights import (
    BAD_FUNDER_WEIGHT,
    BUNDLE_WEIGHT,
    BUNDLED_WEIGHT,
    BURN_DISABLED_WEIGHT,
    COORDINATED_SHILL_WEIGHT,
    CRITICAL_MIN,
    DEPLOYER_REPEAT_WEIGHT,
    FREEZABLE_WEIGHT,
    FRESH_WALLET_SECONDS,
    FRESH_WALLET_WEIGHT,
    HIGH_MIN,
    HONEYPOT_WEIGHT,
    KNOWN_SCAMMER_WEIGHT,
    KOL_MITIGATION,
    KOL_PUMP_WEIGHT,
    LOW_LIQUIDITY_USD,
    LOW_LIQUIDITY_WEIGHT,
    LP_BURNED_PCT_MIN,
    LP_BURNED_PCT_WEIGHT,
    LP_REMOVED_WEIGHT,
    LP_UNLOCKED_PCT_MAX,
    LP_UNLOCKED_WEIGHT,
    MEDIUM_MIN,
    MINT_SUPPLY_RISK_WEIGHT,
    MINTABLE_WEIGHT,
    OWNERSHIP_TRANSFERABLE_WEIGHT,
    PROXY_UPGRADABLE_WEIGHT,
    RENOUNCED_WEIGHT,
    REPEAT_DEPLOYER_MIN,
    REPEAT_DEPLOYER_WEIGHT,
    RUG_HISTORY_WEIGHT,
    SINGLE_POOL_MAX,
    SINGLE_POOL_WEIGHT,
    SMART_MONEY_EXIT_WEIGHT,
    SNIPER_CLUSTER_MIN,
    SNIPER_CLUSTER_WEIGHT,
    SUPPLY_CONCENTRATED_PCT,
    SUPPLY_CONCENTRATED_WEIGHT,
    VERY_YOUNG_SECONDS,
    VERY_YOUNG_WEIGHT,
    VOLUME_CONCENTRATED_PCT,
    VOLUME_CONCENTRATED_WEIGHT,
    WASH_TRADING_WEIGHT,
    WEIGHTS,
    WHALE_EXIT_WEIGHT,
)


# ── Public API ──────────────────────────────────────────────────────────
def _clean() -> dict[str, Any]:
    return {"score": 0, "verdict": load_policy().verdict_for(0), "flags": [], "reasons": [], "analysis": []}


def rug_risk_score(signals: Mapping[str, Any]) -> dict[str, Any]:
    """Score a token's rug risk from an already-computed signal dict.

    Args:
        signals: dict of enrichment signals — flat values (``honeypot``,
            ``mintable``, ``lp_locked``, ``bundled``, ``wash_trading``,
            ``deployer_repeat``, ``rug_history``, ``known_scammer``, ``kol``,
            ``whale_flow``, ``age_seconds``, ``liquidity``, ...) or nested
            module output (``security``, ``deployer``, ``authority``,
            ``holders``, ``gmgn``, ``smart_money``, ``shill``, ...). Values
            may be bools, dicts, strings, or numbers; anything missing is
            treated as "no signal". Never raises.

    Returns:
        ``{"score": int (0-100), "verdict": "critical"|"high"|"medium"|"low",
        "flags": [str], "reasons": [str], "analysis": [{"signal", "weight",
        "detail"}]}`` — each contributing signal yields one machine flag, one
        human-readable reason, and one analysis entry (the per-point
        breakdown). Weights and bands are the resolved policy's.
    """
    if not isinstance(signals, dict):
        return _clean()
    return load_policy().evaluate(signals)


__all__ = [
    "BAD_FUNDER_WEIGHT",
    "BUNDLED_WEIGHT",
    "BUNDLE_WEIGHT",
    "BURN_DISABLED_WEIGHT",
    "COORDINATED_SHILL_WEIGHT",
    "CRITICAL_MIN",
    "DEPLOYER_REPEAT_WEIGHT",
    "FREEZABLE_WEIGHT",
    "FRESH_WALLET_SECONDS",
    "FRESH_WALLET_WEIGHT",
    "HIGH_MIN",
    "HONEYPOT_WEIGHT",
    "KNOWN_SCAMMER_WEIGHT",
    "KOL_MITIGATION",
    "KOL_PUMP_WEIGHT",
    "LOW_LIQUIDITY_USD",
    "LOW_LIQUIDITY_WEIGHT",
    "LP_BURNED_PCT_MIN",
    "LP_BURNED_PCT_WEIGHT",
    "LP_REMOVED_WEIGHT",
    "LP_UNLOCKED_PCT_MAX",
    "LP_UNLOCKED_WEIGHT",
    "MEDIUM_MIN",
    "MINTABLE_WEIGHT",
    "MINT_SUPPLY_RISK_WEIGHT",
    "OWNERSHIP_TRANSFERABLE_WEIGHT",
    "PROXY_UPGRADABLE_WEIGHT",
    "RENOUNCED_WEIGHT",
    "REPEAT_DEPLOYER_MIN",
    "REPEAT_DEPLOYER_WEIGHT",
    "RUG_HISTORY_WEIGHT",
    "SINGLE_POOL_MAX",
    "SINGLE_POOL_WEIGHT",
    "SMART_MONEY_EXIT_WEIGHT",
    "SNIPER_CLUSTER_MIN",
    "SNIPER_CLUSTER_WEIGHT",
    "SUPPLY_CONCENTRATED_PCT",
    "SUPPLY_CONCENTRATED_WEIGHT",
    "VERY_YOUNG_SECONDS",
    "VERY_YOUNG_WEIGHT",
    "VOLUME_CONCENTRATED_PCT",
    "VOLUME_CONCENTRATED_WEIGHT",
    "WASH_TRADING_WEIGHT",
    "WEIGHTS",
    "WHALE_EXIT_WEIGHT",
    "risk_analysis_text",
    "risk_verdict_text",
    "rug_risk_score",
]
