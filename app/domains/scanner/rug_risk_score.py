"""Consolidated rug-risk scorer — the "25-point analysis".

Combines every enrichment signal (contract authority, supply, liquidity,
trading, deployer reputation, social cross-ref, whale flow, age) into a
single 0-100 rug-risk score, one verdict, and a per-signal breakdown.

Design
------
* PURE — ``rug_risk_score(signals)`` only reads the ``signals`` dict: no
  I/O, no DB, no network, no sibling-module imports. The caller (scan
  pipeline / bot lead) is responsible for fetching each signal and passing
  it in as a dict. Parallel enrichment modules that have not landed yet
  (deployer_reputation, wash_trading_enrich, whale_flow_enrich,
  authority_check, social_crossref, gmgn_enrich, token_launch_detector,
  shill_detector) are simply NOT required here — the scorer is decoupled
  by design and only consumes their output dicts. Importing them is never
  attempted; their result keys are parsed tolerantly instead.
* DEFENSIVE — every signal is parsed tolerantly (bool, dict, str, number
  all accepted where sensible); missing/None values mean "absence of
  evidence", never a penalty. Malformed input never raises.
* KEY-TOLERANT — both flat values (``{"honeypot": True}``) and nested
  module output (``{"security": {"honeypot": True}}``,
  ``{"deployer": {"rug_history": True}}``,
  ``{"gmgn": {"holders": {"sniper_count": 5}}}``) are accepted; every rule
  probes a list of key paths.
* MONOTONIC — adding any risk signal can only raise the score; only the
  ``kol`` mitigation subtracts.
* FAIL-OPEN — ``rug_risk_score(None)`` or a garbage dict returns a
  low-risk result (score 0, verdict "low", no flags, empty analysis).

Signal categories (the "25-point" breakdown)
---------------------------------------------
* CONTRACT (7): honeypot, mintable, freezable, lp_unlocked, renounced,
  proxy_upgradable, ownership_transferable
* SUPPLY (3): mint_supply_risk, supply_concentrated, burn_disabled
* LIQUIDITY (4): low_liquidity, lp_removed, lp_burned_pct, single_pool
* TRADING (4): wash_trading, volume_concentrated, bundle_suspect,
  sniper_cluster
* DEPLOYER (4): repeat_deployer, rug_history, bad_funder, fresh_wallet
* SOCIAL (3): known_scammer, kol_pump, coordinated_shill
* MOMENTUM (3): very_young, whale_exit, smart_money_exit

``renounced`` and ``lp_burned_pct`` are inverted protective signals: they
fire when ownership is confirmed *not* renounced / LP is confirmed mostly
*unburned* — the risky states. An explicit ``None``/missing value never
fires either (absence of evidence is not a penalty).

Weighting tiers
---------------
* near-critical (+40/+40/+40/+35): honeypot, known_scammer, rug_history,
  mintable
* high (+30/+28/+25/+25/+22/+22/+22/+20): lp_removed, mint_supply_risk,
  bad_funder, bundle_suspect, repeat_deployer, lp_unlocked, wash_trading
* medium (+18..+12): freezable, coordinated_shill, proxy_upgradable,
  supply_concentrated, whale_exit, kol_pump, ownership_transferable,
  sniper_cluster, low_liquidity, very_young, smart_money_exit
* low (+10..+6): burn_disabled, volume_concentrated, fresh_wallet,
  renounced, lp_burned_pct, single_pool
* mitigation (-10): kol_verified
* hard floor: a confirmed honeypot or known scammer is always >=
  ``CRITICAL_MIN`` (a lone honeypot is still critical).

Verdict thresholds: ``score >= 70`` → critical, ``>= 45`` → high,
``>= 25`` → medium, else low. [REVIEW] — thresholds and per-signal
weights are initial calibration; tune against labelled scan history
before release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    #: Rule = (flag, weight, reason, group, sources) where each source is a
    #: ``(key-paths, predicate)`` pair probed in order.
    _RuleSource = tuple[tuple[tuple[str, ...], ...], Callable[[Any], bool]]
    _Rule = tuple[str, int, str, str, tuple[_RuleSource, ...]]

# ── Tunables ────────────────────────────────────────────────────────────
HONEYPOT_WEIGHT = 40  # near-critical: confirmed sell-blocking
KNOWN_SCAMMER_WEIGHT = 40  # near-critical: identity confirmed
RUG_HISTORY_WEIGHT = 40  # near-critical: documented rug past
MINTABLE_WEIGHT = 35  # near-critical: unlimited supply lever
LP_REMOVED_WEIGHT = 30  # high: liquidity yanked (rug exit)
MINT_SUPPLY_RISK_WEIGHT = 28  # high: mint authority on live supply
BAD_FUNDER_WEIGHT = 25  # high: funded by known bad actor / mixer
BUNDLE_WEIGHT = 25  # high: coordinated launch
BUNDLED_WEIGHT = BUNDLE_WEIGHT  # backward-compat alias
REPEAT_DEPLOYER_WEIGHT = 22  # high: repeat-rug history
DEPLOYER_REPEAT_WEIGHT = REPEAT_DEPLOYER_WEIGHT  # backward-compat alias
LP_UNLOCKED_WEIGHT = 22  # high: rug exit possible
WASH_TRADING_WEIGHT = 20  # high: fake volume
FREEZABLE_WEIGHT = 18  # medium: balances can be frozen
COORDINATED_SHILL_WEIGHT = 18  # medium: shill ring activity
PROXY_UPGRADABLE_WEIGHT = 15  # medium: contract can be swapped
SUPPLY_CONCENTRATED_WEIGHT = 15  # medium: top-holder concentration
WHALE_EXIT_WEIGHT = 15  # medium: smart-money outflow
KOL_PUMP_WEIGHT = 15  # medium: KOL pushing the token
OWNERSHIP_TRANSFERABLE_WEIGHT = 12  # medium: owner can transfer
SNIPER_CLUSTER_WEIGHT = 12  # medium: coordinated early buys
LOW_LIQUIDITY_WEIGHT = 12  # medium
VERY_YOUNG_WEIGHT = 12  # medium
SMART_MONEY_EXIT_WEIGHT = 12  # medium: smart money selling
BURN_DISABLED_WEIGHT = 10  # low: no deflation lever
VOLUME_CONCENTRATED_WEIGHT = 10  # low: one maker dominates volume
FRESH_WALLET_WEIGHT = 10  # low: new / first-time deployer
RENOUNCED_WEIGHT = 8  # low: ownership NOT renounced
LP_BURNED_PCT_WEIGHT = 8  # low: LP mostly unburned
SINGLE_POOL_WEIGHT = 6  # low: one pool only
KOL_MITIGATION = 10  # verified KOL wallet reduces risk

CRITICAL_MIN = 70
HIGH_MIN = 45
MEDIUM_MIN = 25

LP_UNLOCKED_PCT_MAX = 50.0  # % locked below which LP counts as unlocked
VERY_YOUNG_SECONDS = 3600.0  # < 1h old
LOW_LIQUIDITY_USD = 10_000.0  # < $10k pooled liquidity
SUPPLY_CONCENTRATED_PCT = 60.0  # top-10 holders >= 60% of supply
LP_BURNED_PCT_MIN = 50.0  # burn % below this → "mostly unburned"
SNIPER_CLUSTER_MIN = 2  # GMGN sniper holders at/above this = cluster
VOLUME_CONCENTRATED_PCT = 40.0  # top maker share at/above this
REPEAT_DEPLOYER_MIN = 2  # prior deployments at/above this = repeat
FRESH_WALLET_SECONDS = 7 * 24 * 3600.0  # wallet younger than 7 days
SINGLE_POOL_MAX = 1  # pool_count at/below this = single pool

WEIGHTS: dict[str, int] = {
    "honeypot": HONEYPOT_WEIGHT,
    "known_scammer": KNOWN_SCAMMER_WEIGHT,
    "rug_history": RUG_HISTORY_WEIGHT,
    "mintable": MINTABLE_WEIGHT,
    "lp_removed": LP_REMOVED_WEIGHT,
    "mint_supply_risk": MINT_SUPPLY_RISK_WEIGHT,
    "bad_funder": BAD_FUNDER_WEIGHT,
    "bundle_suspect": BUNDLE_WEIGHT,
    "repeat_deployer": REPEAT_DEPLOYER_WEIGHT,
    "lp_unlocked": LP_UNLOCKED_WEIGHT,
    "wash_trading": WASH_TRADING_WEIGHT,
    "freezable": FREEZABLE_WEIGHT,
    "coordinated_shill": COORDINATED_SHILL_WEIGHT,
    "proxy_upgradable": PROXY_UPGRADABLE_WEIGHT,
    "supply_concentrated": SUPPLY_CONCENTRATED_WEIGHT,
    "whale_exit": WHALE_EXIT_WEIGHT,
    "kol_pump": KOL_PUMP_WEIGHT,
    "ownership_transferable": OWNERSHIP_TRANSFERABLE_WEIGHT,
    "sniper_cluster": SNIPER_CLUSTER_WEIGHT,
    "low_liquidity": LOW_LIQUIDITY_WEIGHT,
    "very_young": VERY_YOUNG_WEIGHT,
    "smart_money_exit": SMART_MONEY_EXIT_WEIGHT,
    "burn_disabled": BURN_DISABLED_WEIGHT,
    "volume_concentrated": VOLUME_CONCENTRATED_WEIGHT,
    "fresh_wallet": FRESH_WALLET_WEIGHT,
    "renounced": RENOUNCED_WEIGHT,
    "lp_burned_pct": LP_BURNED_PCT_WEIGHT,
    "single_pool": SINGLE_POOL_WEIGHT,
    "kol_verified": -KOL_MITIGATION,
}


# ── Signal parsing helpers (defensive, never raise) ─────────────────────


def _truthy_scalar(value: Any) -> bool:
    """Coerce a scalar to a verdict boolean (tolerant)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "critical",
            "high",
            "detected",
            "suspected",
        }
    return bool(value)


def _signal_bool(value: Any, keys: tuple[str, ...]) -> bool:
    """Truthy for bool / str / number, or dicts exposing one of ``keys``."""
    if value is None:
        return False
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) is not None:
                return _truthy_scalar(value.get(key))
        return False
    return _truthy_scalar(value)


def _to_float(value: Any) -> float | None:
    """Coerce a value to float, None for missing/unparseable."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_pct(value: Any) -> float | None:
    """Coerce a percent (number or ``"85%"`` string) to float."""
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    return _to_float(value)


def _probe(signals: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    """Return the first non-None leaf value along any nested key path.

    Walks ``signals["a"]["b"]`` for each path; a missing intermediate,
    a non-dict intermediate, or a ``None`` leaf is treated as absent.
    """
    for path in paths:
        node: Any = signals
        try:
            for key in path:
                if not isinstance(node, dict) or node.get(key) is None:
                    node = None
                    break
                node = node[key]
        except Exception:  # defensive: a bad path never raises
            node = None
        if node is not None:
            return node
    return None


def _whale_exit(value: Any) -> bool:
    """True when whale/smart-money flow indicates an exit/outflow."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < 0  # signed net flow: negative = outflow
    if isinstance(value, str):
        s = value.strip().lower()
        return any(tok in s for tok in ("exit", "outflow", "dump", "sell"))
    if isinstance(value, dict):
        for key in ("direction", "trend", "flow", "net", "whale_flow"):
            v = value.get(key)
            if v is None:
                continue
            if isinstance(v, (int, float)) and v < 0:
                return True
            if isinstance(v, str) and any(tok in v.lower() for tok in ("exit", "outflow", "dump", "sell")):
                return True
            if v is True:
                return True
    return False


def _lp_removed(value: Any) -> bool:
    return _signal_bool(value, ("lp_removed", "liquidity_removed", "is_lp_removed"))


def _lp_unlocked(value: Any) -> bool:
    """True when LP is *confirmed* unlocked.

    bool False → unlocked; a percent (number or ``"85%"``) below
    ``LP_UNLOCKED_PCT_MAX`` → unlocked; dict exposing ``unlocked`` /
    ``lp_unlocked`` → truthy means unlocked; None → no signal.
    """
    if value is None:
        return False
    if isinstance(value, dict):
        if value.get("unlocked") is not None:
            return _truthy_scalar(value.get("unlocked"))
        if value.get("lp_unlocked") is not None:
            return _truthy_scalar(value.get("lp_unlocked"))
        locked = value.get("locked")
        if locked is not None and not isinstance(locked, (int, float)):
            return not _truthy_scalar(locked)
        pct = _to_pct(value.get("pct", value.get("liquidity_locked_pct", value.get("lp_locked"))))
        if pct is not None:
            return pct < LP_UNLOCKED_PCT_MAX
        return False
    if isinstance(value, bool):
        return not value
    pct = _to_pct(value)
    if pct is not None:
        return pct < LP_UNLOCKED_PCT_MAX
    return False


def _very_young(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("age_seconds", "launch_age_seconds", "age"):
            seconds = _to_float(value.get(key))
            if seconds is not None:
                return seconds < VERY_YOUNG_SECONDS
        return False
    seconds = _to_float(value)
    return seconds is not None and seconds < VERY_YOUNG_SECONDS


def _low_liquidity(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("liquidity_usd", "liquidity", "pooled_liquidity", "usd"):
            usd = _to_float(value.get(key))
            if usd is not None:
                return usd < LOW_LIQUIDITY_USD
        return False
    usd = _to_float(value)
    return usd is not None and usd < LOW_LIQUIDITY_USD


def _renounced(value: Any) -> bool:
    """Risk when ownership is confirmed *not* renounced.

    ``renounced: True`` (owner renounced → safe) does NOT fire; only an
    explicit ``False`` / ``"no"`` / 0 or a dict exposing a falsy
    ``renounced``/``owner_renounced`` fires. None → no signal.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"no", "false", "0", "not renounced", "not_renounced"}
    if isinstance(value, dict):
        for key in ("renounced", "owner_renounced"):
            if value.get(key) is not None:
                return not _truthy_scalar(value.get(key))
    return False


def _supply_concentrated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= SUPPLY_CONCENTRATED_PCT
    if isinstance(value, str):
        pct = _to_pct(value)
        return pct is not None and pct >= SUPPLY_CONCENTRATED_PCT
    if isinstance(value, dict):
        if value.get("supply_concentrated") is not None:
            return _truthy_scalar(value.get("supply_concentrated"))
        for key in ("top_holder_pct", "top10_holder_pct", "top_10_pct", "concentration_pct", "concentration"):
            pct = _to_pct(value.get(key))
            if pct is not None:
                return pct >= SUPPLY_CONCENTRATED_PCT
    return False


def _lp_burned_pct(value: Any) -> bool:
    """Risk when LP is confirmed mostly *unburned* (burn < threshold).

    ``lp_burned: True`` / a high ``lp_burned_pct`` does NOT fire; a burn
    percent below ``LP_BURNED_PCT_MIN`` fires. None → no signal.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, dict):
        if value.get("lp_burned") is not None:
            return not _truthy_scalar(value.get("lp_burned"))
        pct = _to_pct(value.get("lp_burned_pct", value.get("burned_pct", value.get("liquidity_burned_pct"))))
        if pct is not None:
            return pct < LP_BURNED_PCT_MIN
        return False
    pct = _to_pct(value)
    return pct is not None and pct < LP_BURNED_PCT_MIN


def _single_pool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value <= SINGLE_POOL_MAX
    if isinstance(value, dict):
        if value.get("single_pool") is not None:
            return _truthy_scalar(value.get("single_pool"))
        n = value.get("pool_count", value.get("pair_count", value.get("pairs")))
        if isinstance(n, (int, float)):
            return n <= SINGLE_POOL_MAX
        if isinstance(n, list):
            return len(n) <= SINGLE_POOL_MAX
        return False
    return _truthy_scalar(value)


def _volume_concentrated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= VOLUME_CONCENTRATED_PCT
    if isinstance(value, str):
        pct = _to_pct(value)
        return pct is not None and pct >= VOLUME_CONCENTRATED_PCT
    if isinstance(value, dict):
        if value.get("volume_concentrated") is not None:
            return _truthy_scalar(value.get("volume_concentrated"))
        for key in ("top_maker_share_pct", "top_maker_share", "share", "concentration_pct"):
            pct = _to_pct(value.get(key))
            if pct is not None:
                return pct >= VOLUME_CONCENTRATED_PCT
    return False


def _bundle_suspect(value: Any) -> bool:
    return _signal_bool(value, ("bundled", "bundle_suspect", "is_bundled", "bundle"))


def _sniper_cluster(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= SNIPER_CLUSTER_MIN
    if isinstance(value, dict):
        if value.get("sniper_cluster") is not None:
            return _truthy_scalar(value.get("sniper_cluster"))
        n = value.get("sniper_count", value.get("snipers", value.get("sniper_holders")))
        if isinstance(n, (int, float)):
            return n >= SNIPER_CLUSTER_MIN
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("sniper", "cluster"))
    return _truthy_scalar(value)


def _repeat_deployer(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= REPEAT_DEPLOYER_MIN
    if isinstance(value, dict):
        if value.get("repeat_deployer") is not None:
            return _truthy_scalar(value.get("repeat_deployer"))
        if value.get("deployer_repeat") is not None:
            return _truthy_scalar(value.get("deployer_repeat"))
        n = value.get("prior_deployments")
        if isinstance(n, (int, float)):
            return n >= REPEAT_DEPLOYER_MIN
        return False
    return _truthy_scalar(value)


def _rug_history(value: Any) -> bool:
    return _signal_bool(value, ("rug_history", "has_rugged", "is_rug_puller", "rug_pulls", "rugged"))


def _bad_funder(value: Any) -> bool:
    return _signal_bool(
        value,
        ("bad_funder", "funded_by_scammer", "funded_by_bad_actor", "funder_bad", "mixer_funded"),
    )


def _fresh_wallet(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < FRESH_WALLET_SECONDS  # wallet_age_seconds
    if isinstance(value, dict):
        if value.get("fresh_wallet") is not None:
            return _truthy_scalar(value.get("fresh_wallet"))
        if value.get("first_deployment") is not None:
            return _truthy_scalar(value.get("first_deployment"))
        age = _to_float(value.get("wallet_age_seconds"))
        if age is not None:
            return age < FRESH_WALLET_SECONDS
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"fresh", "new", "first"}
    return _truthy_scalar(value)


def _kol_pump(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        # shill_detector returns a list of alert records.
        for alert in value:
            if not isinstance(alert, dict):
                continue
            pattern = str(alert.get("pattern") or "")
            if "pump" in pattern.lower() or "shill" in pattern.lower():
                return True
        return False
    if isinstance(value, dict):
        if value.get("kol_pump") is not None:
            return _truthy_scalar(value.get("kol_pump"))
        pattern = str(value.get("pattern") or "")
        return "pump" in pattern.lower() or "shill" in pattern.lower()
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("pump", "shill", "promo"))
    return _truthy_scalar(value)


def _coordinated_shill(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if value.get("coordinated_shill") is not None:
            return _truthy_scalar(value.get("coordinated_shill"))
        if value.get("coordinated") is not None:
            return _truthy_scalar(value.get("coordinated"))
        alerts = value.get("alerts") or value.get("shill_alerts")
        if isinstance(alerts, list):
            return len(alerts) >= 1
        if isinstance(alerts, (int, float)):
            return alerts >= 1
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("coordinated", "shill", "cluster"))
    return _truthy_scalar(value)


def _smart_money_exit(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < 0  # signed net flow: negative = outflow
    if isinstance(value, dict):
        if value.get("smart_money_sold") is not None:
            return _truthy_scalar(value.get("smart_money_sold"))
        if value.get("smart_money_exit") is not None:
            return _truthy_scalar(value.get("smart_money_exit"))
        for key in ("net_bought", "buy_pressure", "net"):
            n = _to_float(value.get(key))
            if n is not None:
                return n < 0
        if value.get("sold") is not None:
            return _truthy_scalar(value.get("sold"))
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("exit", "outflow", "sold", "dump"))
    return False


def _kol_verified(value: Any) -> bool:
    """True when the wallet is a verified KOL (mitigating signal)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        for key in ("is_kol", "kol_handle", "matched", "verified"):
            item = value.get(key)
            if item is None:
                continue
            if isinstance(item, str):
                if item.strip():
                    return True
            elif _truthy_scalar(item):
                return True
    return False


# ── Scoring rules: (flag, weight, reason, group, sources) ───────────────
# Ordered so flags/reasons read critical-tier first. Each rule carries one or
# more sources: ``(key-paths, predicate)`` pairs probed in order (flat + nested
# module output). The first source whose predicate fires wins the flag.
_RULES: tuple[_Rule, ...] = (
    # CONTRACT / near-critical
    (
        "honeypot",
        HONEYPOT_WEIGHT,
        "contract is a honeypot (cannot sell)",
        "CONTRACT",
        (
            (
                (("honeypot",), ("security", "honeypot")),
                lambda v: _signal_bool(v, ("is_honeypot", "honeypot")),
            ),
        ),
    ),
    (
        "known_scammer",
        KNOWN_SCAMMER_WEIGHT,
        "deployer is a known scammer",
        "SOCIAL",
        (
            (
                (("known_scammer",), ("social", "known_scammer")),
                lambda v: _signal_bool(v, ("known_scammer", "is_known_scammer")),
            ),
        ),
    ),
    (
        "rug_history",
        RUG_HISTORY_WEIGHT,
        "deployer has a documented rug history",
        "DEPLOYER",
        (
            (
                (("rug_history",), ("deployer", "rug_history"), ("deployer_reputation", "rug_history")),
                _rug_history,
            ),
        ),
    ),
    (
        "mintable",
        MINTABLE_WEIGHT,
        "token supply is mintable",
        "CONTRACT",
        (
            (
                (("mintable",), ("security", "mintable")),
                lambda v: _signal_bool(v, ("mintable", "can_mint", "is_mintable")),
            ),
        ),
    ),
    # LIQUIDITY / high
    (
        "lp_removed",
        LP_REMOVED_WEIGHT,
        "liquidity was removed (rug exit)",
        "LIQUIDITY",
        (
            (
                (("lp_removed",), ("liquidity", "lp_removed")),
                _lp_removed,
            ),
        ),
    ),
    (
        "bad_funder",
        BAD_FUNDER_WEIGHT,
        "deployer funded by a known bad actor / mixer",
        "DEPLOYER",
        (
            (
                (("bad_funder",), ("deployer", "bad_funder")),
                _bad_funder,
            ),
        ),
    ),
    (
        "mint_supply_risk",
        MINT_SUPPLY_RISK_WEIGHT,
        "mint authority poses supply risk",
        "SUPPLY",
        (
            (
                (("mint_supply_risk",),),
                lambda v: _signal_bool(v, ("mint_supply_risk", "mint_risk", "is_mint_risk")),
            ),
        ),
    ),
    (
        "bundle_suspect",
        BUNDLE_WEIGHT,
        "launch was bundled (sniper cluster)",
        "TRADING",
        (
            (
                (("bundled",), ("bundle",), ("bundle_suspect",), ("security", "bundle_suspect")),
                _bundle_suspect,
            ),
        ),
    ),
    (
        "lp_unlocked",
        LP_UNLOCKED_WEIGHT,
        "LP is unlocked (rug exit possible)",
        "CONTRACT",
        (
            # ``lp_unlocked`` carries the UNLOCKED state directly (bool/dict).
            ((("lp_unlocked",),), lambda v: _truthy_scalar(v)),
            # ``lp_locked`` carries the LOCKED state: False / low % = unlocked.
            (
                (("lp_locked",), ("security", "lp_locked"), ("authority", "lp_locked")),
                _lp_unlocked,
            ),
        ),
    ),
    (
        "repeat_deployer",
        REPEAT_DEPLOYER_WEIGHT,
        "deployer has a repeat-rug history",
        "DEPLOYER",
        (
            (
                (
                    ("deployer_repeat",),
                    ("repeat_deployer",),
                    ("deployer", "repeat_deployer"),
                    ("deployer", "prior_deployments"),
                ),
                _repeat_deployer,
            ),
        ),
    ),
    (
        "wash_trading",
        WASH_TRADING_WEIGHT,
        "wash trading detected (fake volume)",
        "TRADING",
        (
            (
                (("wash_trading",),),
                lambda v: _signal_bool(v, ("suspected", "is_wash_trading", "wash_trading", "detected")),
            ),
        ),
    ),
    # TRADING / medium
    (
        "freezable",
        FREEZABLE_WEIGHT,
        "token is freezable (balances can be frozen)",
        "CONTRACT",
        (
            (
                (("freezable",), ("freeze_authority",), ("authority", "freeze_authority")),
                lambda v: _signal_bool(v, ("freeze_authority", "freezable", "is_freezable")),
            ),
        ),
    ),
    (
        "coordinated_shill",
        COORDINATED_SHILL_WEIGHT,
        "coordinated shill activity detected",
        "SOCIAL",
        (
            (
                (("coordinated_shill",), ("shill",), ("shill_detector",)),
                _coordinated_shill,
            ),
        ),
    ),
    (
        "whale_exit",
        WHALE_EXIT_WEIGHT,
        "whale/smart-money outflow detected",
        "MOMENTUM",
        (
            (
                (("whale_flow",), ("whale",)),
                _whale_exit,
            ),
        ),
    ),
    (
        "supply_concentrated",
        SUPPLY_CONCENTRATED_WEIGHT,
        "token supply is concentrated (top holders)",
        "SUPPLY",
        (
            (
                (("supply_concentrated",), ("holders",), ("gmgn", "holders"), ("supply", "top_holder_pct")),
                _supply_concentrated,
            ),
        ),
    ),
    (
        "proxy_upgradable",
        PROXY_UPGRADABLE_WEIGHT,
        "contract is proxy-upgradable",
        "CONTRACT",
        (
            (
                (("proxy_upgradable",), ("proxy",), ("authority", "proxy_upgradable")),
                lambda v: _signal_bool(v, ("proxy", "upgradable", "is_proxy", "proxy_upgradable")),
            ),
        ),
    ),
    (
        "kol_pump",
        KOL_PUMP_WEIGHT,
        "KOL pumping/promoting the token",
        "SOCIAL",
        (
            ((("kol_pump",),), _kol_pump),
            # shill_detector returns a list of alert records.
            ((("shill",),), _kol_pump),
            ((("shill", "pattern"),), _kol_pump),
        ),
    ),
    (
        "low_liquidity",
        LOW_LIQUIDITY_WEIGHT,
        "liquidity is very low",
        "LIQUIDITY",
        (
            (
                (("liquidity",), ("liquidity_usd",), ("liquidity_usd_value",), ("pooled_liquidity",)),
                _low_liquidity,
            ),
        ),
    ),
    (
        "very_young",
        VERY_YOUNG_WEIGHT,
        "token is very young (< 1h old)",
        "MOMENTUM",
        (
            (
                (("age_seconds",), ("launch", "age_seconds")),
                _very_young,
            ),
        ),
    ),
    (
        "smart_money_exit",
        SMART_MONEY_EXIT_WEIGHT,
        "smart money is exiting",
        "MOMENTUM",
        (
            (
                (("smart_money_sold",), ("smart_money_exit",), ("smart_money",), ("gmgn", "smart_money")),
                _smart_money_exit,
            ),
        ),
    ),
    (
        "ownership_transferable",
        OWNERSHIP_TRANSFERABLE_WEIGHT,
        "ownership can be transferred",
        "CONTRACT",
        (
            (
                (
                    ("ownership_transferable",),
                    ("can_take_back_ownership",),
                    ("authority", "ownership_transferable"),
                ),
                lambda v: _signal_bool(
                    v, ("can_take_back_ownership", "ownership_transferable", "transferable", "owner_can_rug")
                ),
            ),
        ),
    ),
    (
        "sniper_cluster",
        SNIPER_CLUSTER_WEIGHT,
        "sniper cluster detected (coordinated buys)",
        "TRADING",
        (
            (
                (
                    ("sniper_cluster",),
                    ("sniper_count",),
                    ("holders", "sniper_count"),
                    ("gmgn", "holders", "sniper_count"),
                ),
                _sniper_cluster,
            ),
        ),
    ),
    (
        "volume_concentrated",
        VOLUME_CONCENTRATED_WEIGHT,
        "trade volume is concentrated in one maker",
        "TRADING",
        (
            (
                (("volume_concentrated",), ("top_maker_share_pct",), ("wash", "top_maker_share_pct")),
                _volume_concentrated,
            ),
        ),
    ),
    (
        "burn_disabled",
        BURN_DISABLED_WEIGHT,
        "token burning is disabled",
        "SUPPLY",
        (
            (
                (("burn_disabled",), ("supply", "burn_disabled")),
                lambda v: _signal_bool(v, ("burn_disabled", "is_burn_disabled")),
            ),
        ),
    ),
    (
        "fresh_wallet",
        FRESH_WALLET_WEIGHT,
        "deployer wallet is fresh / first deployment",
        "DEPLOYER",
        (
            (
                (
                    ("fresh_wallet",),
                    ("first_deployment",),
                    ("wallet_age_seconds",),
                    ("deployer", "first_deployment"),
                ),
                _fresh_wallet,
            ),
        ),
    ),
    (
        "renounced",
        RENOUNCED_WEIGHT,
        "ownership is NOT renounced (owner retains control)",
        "CONTRACT",
        (
            (
                (("renounced",), ("authority", "renounced"), ("security", "renounced")),
                _renounced,
            ),
        ),
    ),
    (
        "lp_burned_pct",
        LP_BURNED_PCT_WEIGHT,
        "LP is mostly unburned (burn < 50%)",
        "LIQUIDITY",
        (
            (
                (("lp_burned_pct",), ("lp_burned",), ("liquidity", "lp_burned_pct")),
                _lp_burned_pct,
            ),
        ),
    ),
    (
        "single_pool",
        SINGLE_POOL_WEIGHT,
        "liquidity sits in a single pool",
        "LIQUIDITY",
        (
            (
                (("single_pool",), ("pool_count",), ("liquidity", "pool_count")),
                _single_pool,
            ),
        ),
    ),
)

# ── Public API ──────────────────────────────────────────────────────────


def _clean() -> dict[str, Any]:
    return {"score": 0, "verdict": "low", "flags": [], "reasons": [], "analysis": []}


def _verdict_for(score: int) -> str:
    if score >= CRITICAL_MIN:
        return "critical"
    if score >= HIGH_MIN:
        return "high"
    if score >= MEDIUM_MIN:
        return "medium"
    return "low"


def rug_risk_score(signals: dict[str, Any]) -> dict[str, Any]:
    """Score a token's rug risk from an already-computed signal dict.

    Args:
        signals: dict of enrichment signals — flat values
            (``honeypot``, ``mintable``, ``lp_locked``, ``bundled``,
            ``wash_trading``, ``deployer_repeat``, ``rug_history``,
            ``known_scammer``, ``kol``, ``whale_flow``, ``age_seconds``,
            ``liquidity``, ...) or nested module output
            (``security``, ``deployer``, ``authority``, ``holders``,
            ``gmgn``, ``smart_money``, ``shill``, ...). Values may be
            bools, dicts, strings, or numbers; anything missing is treated
            as "no signal". Never raises.

    Returns:
        ``{"score": int (0-100), "verdict": "critical"|"high"|"medium"|"low",
        "flags": [str], "reasons": [str], "analysis": [{"signal", "weight",
        "detail"}]}`` — each contributing signal yields one machine flag,
        one human-readable reason, and one analysis entry (the "25-point
        breakdown").
    """
    if not isinstance(signals, dict):
        return _clean()

    score = 0
    flags: list[str] = []
    reasons: list[str] = []
    analysis: list[dict[str, Any]] = []

    for flag, weight, reason, _group, sources in _RULES:
        hit = False
        for paths, predicate in sources:
            try:
                fired = bool(predicate(_probe(signals, paths)))
            except Exception:  # defensive: a bad signal never raises
                fired = False
            if fired:
                hit = True
                break
        if hit:
            score += weight
            flags.append(flag)
            reasons.append(reason)
            analysis.append({"signal": flag, "weight": weight, "detail": reason})

    try:
        kol_hit = _kol_verified(_probe(signals, (("kol",), ("social", "kol"), ("crossref",))))
    except Exception:  # defensive
        kol_hit = False
    if kol_hit:
        score -= KOL_MITIGATION
        flags.append("kol_verified")
        reasons.append("wallet belongs to a verified KOL (mitigating)")
        analysis.append(
            {
                "signal": "kol_verified",
                "weight": -KOL_MITIGATION,
                "detail": "wallet belongs to a verified KOL (mitigating)",
            }
        )

    score = max(0, min(100, score))
    # Hard floor: a confirmed honeypot or known scammer is always critical —
    # identity is verified, the risk is not probabilistic.
    if any(flag in flags for flag in ("honeypot", "known_scammer")):
        score = max(score, CRITICAL_MIN)

    return {"score": score, "verdict": _verdict_for(score), "flags": flags, "reasons": reasons, "analysis": analysis}


# ── Renderers ───────────────────────────────────────────────────────────

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
_GROUP_OF: dict[str, str] = {rule[0]: rule[3] for rule in _RULES}
_GROUP_OF["kol_verified"] = "MITIGATION"


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
