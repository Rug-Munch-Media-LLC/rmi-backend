"""Suspicious-tokens listing executor for the chat tool-use layer.

"Give me the shady tokens you've seen launching" must surface REAL tokens
the bot has observed, with concrete reasons — never a generic lecture and
never fabricated names. Pulls launch records via the in-process detector +
GMGN provider (no x402 endpoints), then ranks by the consolidated rug-risk
score (honeypot / mintable / LP-lock / bundle / age / liquidity /
known-scammer overlap) when the pure scorer is available, else the legacy
age/liquidity heuristic. Fail-open → None (chat falls back to RAG/LLM).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

from ._shared import _to_float

logger = get_logger(__name__)

_SUSPICIOUS_LAUNCH_LIMIT = 30  # detector window to inspect
_SUSPICIOUS_FRESH_LIMIT = 20  # GMGN fresh pairs to supplement with
_SUSPICIOUS_MAX_ROWS = 8  # cap on rendered list
_SUSPICIOUS_YOUNG_SECONDS = 3600  # <1h age is the classic rug window
_SUSPICIOUS_LIQUIDITY_LOW = 1000.0  # <$1k LP
_SUSPICIOUS_LIQUIDITY_THIN = 5000.0  # <$5k LP
_SUSPICIOUS_MC_TINY = 5000.0  # market cap below this is a red flag
# Top-N newest launches that get per-token security enrichment (honeypot /
# mintable / LP-lock / bundle network checks are capped in the detector —
# matches app.databus.token_launch_detector._SECURITY_ENRICH_LIMIT).
_SUSPICIOUS_ENRICH_MAX = 5

_LAUNCH_ENTITY_KEYS = (
    "address",
    "creator",
    "deployer",
    "owner",
    "wallet",
    "maker",
    "launcher",
    "creator_address",
    "deployer_address",
    "owner_address",
)


def _is_truthy(value: Any) -> bool:
    """Best-effort truthiness for provider flag fields ("true"/1/"yes"/...)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "locked", "mintable", "honeypot")
    return False


def _suspicious_flags(launch: dict[str, Any]) -> list[str]:
    """Concrete flag reasons present in the launch record (empty when absent).

    Reads both the legacy flat fields (``is_honeypot``/``is_mintable``/
    ``lp_locked`` from GMGN/provider records) and the enriched ``security``
    sub-dict added by ``TokenLaunchDetector.enrich_launches``. Fields that are
    absent or unknown (None) never fire a reason — fail-closed.
    """
    flags: list[str] = []
    sec = launch.get("security")
    security = sec if isinstance(sec, dict) else {}

    for key in ("is_honeypot", "honeypot", "flagged_honeypot"):
        if _is_truthy(launch.get(key)) or _is_truthy(security.get("honeypot")):
            flags.append("honeypot")
            break
    for key in ("is_mintable", "mintable", "mintable_flag"):
        if _is_truthy(launch.get(key)) or _is_truthy(security.get("mintable")):
            flags.append("mintable")
            break
    if security.get("lp_locked") is not None and not _is_truthy(security.get("lp_locked")):
        flags.append("LP not locked")
    for key in ("lp_locked", "is_lp_locked", "liquidity_locked"):
        val = launch.get(key)
        if val is not None and not _is_truthy(val):
            flags.append("LP not locked")
            break
    if _is_truthy(security.get("bundle_suspect")):
        flags.append("bundled snipers")
    raw_flags = launch.get("risk_flags") or launch.get("flags")
    if isinstance(raw_flags, list):
        for item in raw_flags:
            text = str(item).strip()
            if text and text.lower() not in ("none", "null"):
                flags.append(text[:40])
                break
    elif isinstance(raw_flags, str):
        text = raw_flags.strip()
        if text and text.lower() not in ("none", "null"):
            flags.append(text[:40])
    return flags


def _launch_age_seconds(launch: dict[str, Any]) -> float | None:
    """Launch age in seconds from ``age_seconds`` or ``created_at``; None when unknown."""
    try:
        age = launch.get("age_seconds")
        if age is not None and float(age) > 0:
            return float(age)
    except (TypeError, ValueError) as e:
        logger.warning(f"_launch_age_seconds failed: {e}")
    created = launch.get("created_at")
    if created is None:
        return None
    try:
        ts = float(created)
        if ts > 1e11:  # epoch milliseconds
            ts /= 1000.0
        return max(0.0, datetime.now(UTC).timestamp() - ts)
    except (TypeError, ValueError):
        return None


def _launch_scammer_hit(launch: dict[str, Any], scammers: set[str]) -> str | None:
    """Concrete reason when an entity field of the launch matches a known scammer."""
    if not scammers:
        return None
    for key in _LAUNCH_ENTITY_KEYS:
        val = launch.get(key)
        if val is None:
            continue
        norm = str(val).strip()
        if not norm:
            continue
        if norm.startswith("0x"):
            norm = norm.lower()
        if norm in scammers:
            return "deployer matches known scammer" if key != "address" else "address matches known scammer"
    # Enriched security sub-dict — the creator/owner resolution wired by the
    # launch detector ("known creator" reason when it matches a known scammer).
    sec = launch.get("security")
    if isinstance(sec, dict):
        creator = sec.get("creator_address") or sec.get("owner_address")
        if creator:
            norm = str(creator).strip().lower() if str(creator).strip().startswith("0x") else str(creator).strip()
            if norm in scammers:
                return "creator matches known scammer"
    return None


def _launch_suspicion_score(launch: dict[str, Any], scammers: set[str]) -> tuple[float, str]:
    """(suspicion_score, concrete_reason) for a launch record; (0.0, "") when none.

    Signals, all derived from the record's own fields:
      - honeypot / mintable / unlocked-LP / bundled snipers (+60 each) →
        "honeypot" / "mintable" / "LP not locked" / "bundled snipers" — from
        the flat provider flags OR the enriched ``security`` sub-dict; these
        rank ABOVE age/liquidity so concrete scam signals always win.
      - age < 1h                      (+40)  → "age 4m"
      - liquidity 0 / <$1k / <$5k     (+50/35/15) → "0 liquidity" / "LP $850"
      - entity matches known scammer  (+100) → "deployer matches known scammer"
                                              / "creator matches known scammer"
      - market cap < $5k              (+10)  → "MC $1.2k"
    """
    score = 0.0
    reasons: list[str] = []

    age_s = _launch_age_seconds(launch)
    if age_s is not None and age_s < _SUSPICIOUS_YOUNG_SECONDS:
        score += 40
        minutes = int(age_s // 60)
        reasons.append(f"age {minutes}m" if minutes >= 1 else "age <1m")

    liq = _to_float(launch.get("liquidity"))
    if liq is not None:
        if liq <= 0:
            score += 50
            reasons.append("0 liquidity")
        elif liq < _SUSPICIOUS_LIQUIDITY_LOW:
            score += 35
            reasons.append(f"LP ${liq:,.0f}")
        elif liq < _SUSPICIOUS_LIQUIDITY_THIN:
            score += 15
            reasons.append(f"LP ${liq:,.0f}")

    scam_reason = _launch_scammer_hit(launch, scammers)
    if scam_reason:
        score += 100
        reasons.append(scam_reason)

    for flag in _suspicious_flags(launch):
        score += 60
        reasons.append(flag)

    mc = _to_float(launch.get("market_cap"))
    if mc is not None and 0 < mc < _SUSPICIOUS_MC_TINY:
        score += 10
        reasons.append(f"MC ${mc:,.0f}")

    if not reasons:
        return 0.0, ""
    deduped = list(dict.fromkeys(reasons))  # drop duplicate flag wording
    return score, "; ".join(deduped)[:120]


def _launch_rug_signals(launch: dict[str, Any], scammers: set[str]) -> dict[str, Any]:
    """Build the consolidated rug-risk signal dict from a launch record.

    Feeds the enriched ``security`` sub-dict (honeypot / mintable /
    lp_locked / bundle_suspect), the legacy flat provider flags, the record's
    own age / liquidity / market-cap, and any creator/deployer overlap with
    known scammers. ``rug_risk_score`` tolerates unknown keys (absent signal =
    no evidence); a tiny market cap alone is surfaced via the fallback scorer
    when the consolidated one yields nothing.
    """
    sec = launch.get("security")
    security = sec if isinstance(sec, dict) else {}

    def _pick(*keys: str) -> Any:
        for key in keys:
            val = launch.get(key)
            if val is not None:
                return val
        return None

    return {
        "honeypot": (
            security.get("honeypot")
            if security.get("honeypot") is not None
            else _pick("is_honeypot", "honeypot", "flagged_honeypot")
        ),
        "mintable": (
            security.get("mintable")
            if security.get("mintable") is not None
            else _pick("is_mintable", "mintable", "mintable_flag")
        ),
        "lp_locked": (
            security.get("lp_locked")
            if security.get("lp_locked") is not None
            else _pick("lp_locked", "is_lp_locked", "liquidity_locked")
        ),
        "bundled": (
            security.get("bundle_suspect")
            if security.get("bundle_suspect") is not None
            else _pick("bundle_suspect", "bundled", "is_bundled")
        ),
        "deployer_repeat": _pick("deployer_repeat", "repeat_rug", "is_repeat_deployer"),
        "known_scammer": bool(_launch_scammer_hit(launch, scammers)),
        "age_seconds": _launch_age_seconds(launch),
        "liquidity": _to_float(launch.get("liquidity")),
        "market_cap": _to_float(launch.get("market_cap")),
    }


def _launch_rug_risk(launch: dict[str, Any], scammers: set[str]) -> dict[str, Any] | None:
    """Consolidated rug-risk result (score/verdict/flags/reasons) or None.

    Prefers the pure ``rug_risk_score`` over the legacy heuristic: returns the
    scorer's result ONLY when it produced concrete reasons. None (scorer
    unavailable or silent) means the caller falls back to
    ``_launch_suspicion_score``.
    """
    # rug_risk_score is patch-mocked by tests on app.domains.chat_tools.* —
    # resolve it through the package shim at call time (lazy to avoid an
    # import cycle — the shim imports this module).
    from app.domains.chat_tools import rug_risk_score

    if rug_risk_score is None:
        return None
    try:
        result = rug_risk_score(_launch_rug_signals(launch, scammers))
    except Exception as e:
        logger.debug("chat_tool_rug_risk_failed", error=str(e)[:120])
        return None
    if not isinstance(result, dict):
        return None
    if not (result.get("reasons") or result.get("flags")):
        return None
    return result


def _rug_risk_segment(result: dict[str, Any]) -> str:
    """Render ``score 92/100 CRITICAL — honeypot · mintable`` from a rug-risk result.

    Reuses ``risk_verdict_text``'s canonical flag→text mapping (top 3 flags,
    ``kol_verified`` hidden unless alone); falls back to a bare
    ``score N/100 VERDICT`` when there are no flags to show.
    """
    # risk_verdict_text is patch-mocked by tests on app.domains.chat_tools.* —
    # resolve it through the package shim at call time (lazy to avoid an
    # import cycle — the shim imports this module).
    from app.domains.chat_tools import risk_verdict_text

    if not isinstance(result, dict):
        return ""
    score = int(result.get("score") or 0)
    verdict = str(result.get("verdict") or "low").upper()
    rendered = risk_verdict_text(result) if risk_verdict_text is not None else ""
    tail = ""
    if rendered and "—" in rendered:
        tail = rendered.split("—", 1)[1].strip()
        if tail.lower() == "no significant risk flags":
            tail = ""
    if not tail:
        return f"score {score}/100 {verdict}"
    return f"score {score}/100 {verdict} — {tail}"


def _rank_suspicious(launches: list[dict[str, Any]], scammers: set[str]) -> list[tuple[dict[str, Any], str]]:
    """Rank launches by consolidated rug-risk (desc); only records with a reason.

    Prefers the pure ``rug_risk_score`` (honeypot/mintable/LP-lock/bundle +
    age/liquidity + known-scammer overlap) and falls back to the legacy
    ``_launch_suspicion_score`` heuristic when the consolidated scorer is
    unavailable or silent. Records with NO concrete reason are dropped.
    """
    ranked: list[tuple[float, dict[str, Any], str]] = []
    for launch in launches:
        if not isinstance(launch, dict):
            continue
        result = _launch_rug_risk(launch, scammers)
        if result is not None:
            reason = _rug_risk_segment(result)
            if reason:
                ranked.append((float(result.get("score") or 0), launch, reason))
                continue
        score, reason = _launch_suspicion_score(launch, scammers)
        if score > 0 and reason:
            ranked.append((score, launch, reason))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [(launch, reason) for _, launch, reason in ranked]


def _merge_launches(primary: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate launch records, deduping by address (symbol when address empty)."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for launch in [*primary, *extra]:
        if not isinstance(launch, dict):
            continue
        key = str(launch.get("address") or "").strip() or str(launch.get("symbol") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(launch)
    return merged


def _render_suspicious(ranked: list[tuple[dict[str, Any], str]]) -> str | None:
    """Numbered 🔴 list of the top suspicious launches; None when empty."""
    if not ranked:
        return None
    lines: list[str] = []
    for i, (launch, reason) in enumerate(ranked[:_SUSPICIOUS_MAX_ROWS], start=1):
        symbol = str(launch.get("symbol") or "???").strip().upper()
        chain = str(launch.get("chain") or "solana").strip() or "solana"
        address = str(launch.get("address") or "").strip()
        short = address[:10] + "…" if len(address) > 10 else (address or "?")
        lines.append(f"{i}. 🔴 {symbol} ({chain}) — {short} — {reason}")
    return "Suspicious recent launches:\n" + "\n".join(lines)


async def _exec_suspicious(message: str = "") -> str | None:
    """List recent launches that look shady, ranked by the 25-point rug-risk score.

    Fetches REAL launches via the in-process detector + GMGN provider (never
    the x402 endpoints), best-effort enriches the newest ones with the
    detector's security quick-checks (honeypot/mintable/LP-lock/creator/bundle),
    then ranks by the consolidated rug-risk score — feeding each launch's
    security fields + age/liquidity/mcap + creator/deployer known-scammer
    overlap into the pure scorer; falls back to the legacy age/liquidity
    heuristic when the scorer is unavailable or silent. Records without a
    concrete reason are dropped. Fail-open → None when no launch data is
    available — chat falls back to RAG/LLM. Never invents tokens or reasons.
    """
    launches: list[dict[str, Any]] = []
    try:
        from app.databus.token_launch_detector import get_launch_detector

        detector = get_launch_detector()
        raw = await detector.get_recent_launches(limit=_SUSPICIOUS_LAUNCH_LIMIT)
        if isinstance(raw, list):
            launches = raw
        # Best-effort security enrichment (honeypot/mintable/LP-lock/creator/
        # bundle). Runs only when the detector instance supports it; a missing
        # or failing enricher leaves the raw launch list untouched (fail-open).
        try:
            enricher = getattr(detector, "enrich_launches", None)
            if callable(enricher):
                enriched = await enricher(launches, max_checks=_SUSPICIOUS_ENRICH_MAX)
                if isinstance(enriched, list):
                    launches = enriched
        except Exception as e:
            logger.debug("chat_tool_suspicious_enrich_failed", error=str(e)[:120])
    except Exception as e:
        logger.debug("chat_tool_suspicious_launches_failed", error=str(e)[:120])

    # Supplement with GMGN fresh pairs (fail-open — never blocks the list).
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        fresh = await get_gmgn_provider().get_new_pairs("solana", limit=_SUSPICIOUS_FRESH_LIMIT)
        if isinstance(fresh, list):
            launches = _merge_launches(launches, fresh)
    except Exception as e:
        logger.debug("chat_tool_suspicious_gmgn_failed", error=str(e)[:120])

    if not launches:
        return None

    try:
        from app.databus.shill_detector import load_known_scammers

        scammers: set[str] = set(load_known_scammers())
    except Exception as e:
        logger.debug("chat_tool_suspicious_scammers_failed", error=str(e)[:120])
        scammers = set()

    return _render_suspicious(_rank_suspicious(launches, scammers))
