"""KOL Track-Record Scoring (trust score 0-100).

Computes a per-handle trust score from the signals gathered by the KOL
tracking pipeline (``social_intel``) plus local outcome/rug/scammer data:

    score = base(50)
            + 25 * win_rate                       (call accuracy)
            + 10 * clamp(avg_24h_return, -0.5, +0.5) / 0.5
            - 30 * rug_rate                       (rug rate of called tokens)
            - 6  * min(shill_alert_count, 5)      (shill-detector hits)
            - 40 * known_scammer                  (hard penalty, caps score)
            + 10 * wallet_attribution_confidence  (0..1)

Storage: an in-process registry (mirrors ``social_intel.KOL_DATABASE``) with
optional Redis JSON persistence under ``kol:score:{handle}``. The registry is
a *registry*, not a relational table — callers that need SQL can read the
track record via the existing ``kol_signal`` tables later (see
``docs/KOL-TRACKING-DESIGN.md``).

External data that does not exist yet degrades gracefully:
  - ``known_scammers.json`` is loaded when present, otherwise empty;
  - ``kol_wallets.py`` wallet confidence is queried when importable,
    otherwise 0.0 — register via :func:`register_wallet_confidence`.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("kol_scoring")

# ═══════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════

#: In-process score registry: handle -> KOLScoreRecord.
KOL_SCORE_REGISTRY: dict[str, KOLScoreRecord] = {}
_REGISTRY_LOCK = threading.Lock()

#: Local outcome store used when the shared call tracker has no +24h data
#: (``social_intel.KOL_DATABASE`` only records price_at_call, not outcome).
#: handle -> list of CallOutcome.
CALL_OUTCOMES: dict[str, list[CallOutcome]] = {}

#: Tokens known to have rugged (address or symbol). Extended at runtime via
#: :func:`register_rug_token`.
KNOWN_RUG_TOKENS: set[str] = set()

#: Explicit shill-alert counts per handle (in addition to what the shill
#: detector reports through ``social_intel.DETECTED_CAMPAIGNS``).
SHILL_ALERTS: dict[str, int] = {}

#: Deleted-call counts per handle — a KOL retracting calls after the fact is
#: a suspicious signal. Fed by :func:`register_deleted_alert` and by the
#: ``account_alias.DELETED_POSTS`` registry (via ``social_intel``).
DELETED_ALERTS: dict[str, int] = {}

#: Handle -> wallet-attribution confidence 0..1 (extendable; auto-read from
#: ``app.databus.kol_wallets`` when that module exists).
WALLET_CONFIDENCE: dict[str, float] = {}

#: Scammer handles merged from ``known_scammers.json`` + runtime additions.
KNOWN_SCAMMER_HANDLES: set[str] = set()

#: Handle -> documented rug-call entries (``kol_rug_history.json`` + runtime
#: additions via :func:`register_kol_rug`). This is the KOL's ACTUAL documented
#: rug history — the primary source for the rug-rate penalty.
KOL_RUG_HISTORY: dict[str, list[dict[str, Any]]] = {}


@dataclass
class CallOutcome:
    """A resolved KOL call with a realized +24h outcome."""

    token: str
    price_at_call: float = 0.0
    price_after_24h: float = 0.0
    win: bool | None = None  # explicit win flag; derived from return when None
    call_type: str = "buy"
    recorded_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class KOLScoreRecord:
    """Persisted per-KOL score + track record."""

    handle: str
    score: float
    risk_level: str
    components: dict[str, float]
    win_rate: float
    avg_return_24h: float
    rug_rate: float
    shill_alert_count: int
    deleted_alert_count: int
    known_scammer: bool
    wallet_confidence: float
    total_calls: int
    total_tokens: int
    documented_rug_count: int = 0
    track_record: dict[str, Any] = field(default_factory=dict)
    computed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ═══════════════════════════════════════════════════════════════════════
# External signal readers (graceful when the sources do not exist yet)
# ═══════════════════════════════════════════════════════════════════════


def _load_known_scammers() -> set[str]:
    """Load scammer handles from ``known_scammers.json`` when present.

    Prefers the canonical loader in ``shill_detector`` (designed concurrently)
    and falls back to a local reader when that module is unavailable. Returns
    an empty set when the file is missing.
    """
    try:  # canonical loader (shill_detector.py, same dir/file)
        from app.databus.shill_detector import load_known_scammers

        loaded = load_known_scammers()
        if isinstance(loaded, set):
            return {str(h).lower().lstrip("@") for h in loaded}
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    path = Path(__file__).resolve().parent / "known_scammers.json"
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("known_scammers.json unreadable: %s", str(e)[:120])
        return set()
    if isinstance(raw, list):
        return {str(h).lower().lstrip("@") for h in raw if str(h).strip()}
    if isinstance(raw, dict):
        result: set[str] = set()
        for key in ("handles", "addresses"):
            for h in raw.get(key, []) or []:
                result.add(str(h).lower().lstrip("@"))
        for key in ("entries", "accounts"):
            for item in raw.get(key, []) or []:
                if isinstance(item, dict) and item.get("handle"):
                    result.add(str(item["handle"]).lower().lstrip("@"))
        return {h for h in result if h.strip()}
    return set()


def _load_kol_rug_history() -> dict[str, list[dict[str, Any]]]:
    """Load documented KOL rug history (handle -> rug entries).

    Prefers the canonical loader in ``shill_detector`` (designed concurrently)
    and falls back to a local reader when that module is unavailable. Returns
    an empty map when the file is missing.
    """
    try:  # canonical loader (shill_detector.py, same dir/file)
        from app.databus.shill_detector import load_kol_rug_history

        loaded = load_kol_rug_history()
        if isinstance(loaded, dict):
            return {str(h).lower().lstrip("@"): list(v) for h, v in loaded.items()}
    except (ImportError, AttributeError, OSError, ValueError):
        pass
    path = Path(__file__).resolve().parent / "kol_rug_history.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("kol_rug_history.json unreadable: %s", str(e)[:120])
        return {}
    entries = raw if isinstance(raw, list) else raw.get("kol_rug_history") or raw.get("entries") or []
    if not isinstance(entries, list):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        handle = str(entry.get("handle") or entry.get("kol") or "").lower().lstrip("@")
        if not handle:
            continue
        rugs = entry.get("rugs") or entry.get("rug_calls") or []
        if isinstance(rugs, list):
            result.setdefault(handle, []).extend(r for r in rugs if isinstance(r, dict))
    return result


def get_kol_rug_history(handle: str) -> dict[str, Any]:
    """Return the documented rug history for a KOL handle (public query).

    Source is ``kol_rug_history.json`` (loaded at import) plus any runtime
    entries registered via :func:`register_kol_rug`.
    """
    key = handle.lower().lstrip("@")
    if not key:
        return {"handle": handle, "rugs": [], "rug_count": 0, "found": False, "source": "kol_rug_history"}
    rugs = list(KOL_RUG_HISTORY.get(key, []))
    return {
        "handle": key,
        "rugs": rugs,
        "rug_count": len(rugs),
        "found": len(rugs) > 0,
        "source": "kol_rug_history",
    }


def _wallet_attribution_confidence(handle: str) -> float:
    """Read wallet-attribution confidence for a handle.

    Prefers ``app.databus.kol_wallets`` when it exists (it does not yet),
    then the local registry. Returns 0.0 (neutral) when unknown.
    """
    cached = WALLET_CONFIDENCE.get(handle.lower())
    if cached is not None:
        return max(0.0, min(1.0, cached))
    try:  # pragma: no cover - defensive, module may not exist yet
        from app.databus.kol_wallets import get_wallet_confidence

        conf = get_wallet_confidence(handle)
        if isinstance(conf, (int, float)):
            return max(0.0, min(1.0, float(conf)))
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return 0.0


def _load_calls(handle: str) -> list[dict[str, Any]]:
    """Merge shared call tracker entries with local resolved outcomes.

    ``social_intel`` records calls at post time (no +24h price), while
    :func:`record_call_outcome` stores resolved results. Both feed the score.
    """
    calls: list[dict[str, Any]] = []
    try:  # shared tracker may be empty/unseeded — never fatal
        from app.databus import social_intel

        key = f"kol:{handle.lower().lstrip('@')}"
        data = social_intel.KOL_DATABASE.get(key)
        if data:
            calls.extend(data.get("calls", []))
    except (ImportError, AttributeError):
        pass
    calls.extend(
        {
            "token": o.token,
            "type": o.call_type,
            "price_at_call": o.price_at_call,
            "price_after_24h": o.price_after_24h,
            "win": o.win,
        }
        for o in CALL_OUTCOMES.get(handle.lower(), [])
    )
    return calls


def _shill_alert_count(handle: str) -> int:
    """Shill-alert count: local registry + shill-detector campaigns."""
    key = handle.lower()
    count = SHILL_ALERTS.get(key, 0)
    try:  # count confirmed campaigns this handle participated in
        from app.databus import social_intel

        for campaign in social_intel.DETECTED_CAMPAIGNS:
            if key in {str(k).lower().lstrip("@") for k in campaign.get("kols_involved", [])}:
                count += 1
    except (ImportError, AttributeError):
        pass
    return count


def _deleted_alert_count(handle: str) -> int:
    """Deleted-call count: local registry + account_alias.DELETED_POSTS."""
    key = handle.lower().lstrip("@")
    count = DELETED_ALERTS.get(key, 0)
    try:  # the alias module may be absent — never fatal
        from app.databus import account_alias

        count += account_alias.deleted_post_count(handle=key)
    except (ImportError, AttributeError):
        pass
    return count


# ═══════════════════════════════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════════════════════════════

# Component weights — tuned so a perfect trader lands near 90-100 and a
# known scammer is floored regardless of call accuracy.
_WIN_RATE_WEIGHT = 25.0
_RETURN_WEIGHT = 10.0  # applied to return normalized to [-1, 1] over ±50%
_RUG_RATE_WEIGHT = 30.0
#: Denominator for the documented rug rate when a KOL has no tracked calls:
#: >= 3 documented rug calls (and no call history) still hits full rug rate.
_DOCUMENTED_RUG_DENOM = 3
_SHILL_ALERT_WEIGHT = 6.0
_SHILL_ALERT_CAP = 5
# Deleted-call penalty — a KOL retracting calls after the fact loses trust.
_DELETED_ALERT_WEIGHT = float(os.getenv("DELETED_ALERT_WEIGHT", "5"))
_DELETED_ALERT_CAP = 5
_SCAMMER_PENALTY = 40.0
_WALLET_CONF_WEIGHT = 10.0
_RETURN_CLAMP = 0.5

_NEUTRAL_SCORE = 50.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _resolve_win(outcome: dict[str, Any]) -> bool:
    """Explicit win flag wins; otherwise a positive +24h return counts as a win."""
    if isinstance(outcome.get("win"), bool):
        return bool(outcome["win"])
    if outcome.get("price_at_call") and outcome.get("price_after_24h") is not None:
        return float(outcome["price_after_24h"]) > float(outcome["price_at_call"])
    return False


def _call_accuracy(calls: list[dict[str, Any]]) -> tuple[float, float]:
    """Win rate (0..1) and average +24h return (decimal fraction)."""
    scored = [c for c in calls if c.get("price_after_24h") is not None or isinstance(c.get("win"), bool)]
    if not scored:
        return 0.0, 0.0
    wins = sum(1 for c in scored if _resolve_win(c))
    returns: list[float] = []
    for c in scored:
        p0 = c.get("price_at_call")
        p1 = c.get("price_after_24h")
        if isinstance(p0, (int, float)) and isinstance(p1, (int, float)) and float(p0) > 0:
            returns.append(float(p1) / float(p0) - 1.0)
    win_rate = wins / len(scored)
    avg_return = sum(returns) / len(returns) if returns else 0.0
    return win_rate, avg_return


def _rug_rate(handle: str, calls: list[dict[str, Any]]) -> float:
    """Rug rate (0..1): max of the KOL's documented rug history and the
    fraction of called tokens known to have rugged.

    The documented history (``kol_rug_history.json``) is the primary source —
    a KOL with documented rug calls is penalized even when the local outcome
    store has not resolved those calls yet. When the KOL has no tracked calls,
    the documented count is scaled against ``_DOCUMENTED_RUG_DENOM`` so a
    history with >= 3 documented rugs hits the full penalty.
    """
    tokens = {str(c.get("token", "")).strip().upper() for c in calls if str(c.get("token", "")).strip()}
    token_rug_rate = 0.0
    if tokens:
        rugged = tokens & KNOWN_RUG_TOKENS
        token_rug_rate = len(rugged) / len(tokens)

    documented = KOL_RUG_HISTORY.get(handle.lower().lstrip("@"), [])
    if not documented:
        return token_rug_rate
    denominator = len(tokens) if tokens else _DOCUMENTED_RUG_DENOM
    documented_rug_rate = min(1.0, len(documented) / denominator)
    return max(token_rug_rate, documented_rug_rate)


def score_kol(handle: str, *, persist: bool = True) -> dict[str, Any]:
    """Compute (or recompute) the 0-100 trust score for a KOL handle.

    Reads call history from the shared tracker + local resolved outcomes,
    then combines call accuracy, rug rate, shill alerts, scammer overlap and
    wallet-attribution confidence into a single score. The result is stored
    in the registry (and optionally Redis) and returned as a dict.
    """
    key = handle.lower().lstrip("@")
    if not key:
        return {"handle": handle, "score": _NEUTRAL_SCORE, "risk_level": "MEDIUM", "found": False}

    calls = _load_calls(handle)
    win_rate, avg_return = _call_accuracy(calls)
    rug_rate = _rug_rate(handle, calls)
    documented_rug_count = len(KOL_RUG_HISTORY.get(key, []))
    shill_count = _shill_alert_count(handle)
    deleted_count = _deleted_alert_count(handle)
    known_scammer = key in KNOWN_SCAMMER_HANDLES
    wallet_conf = _wallet_attribution_confidence(handle)

    score = _NEUTRAL_SCORE
    score += _WIN_RATE_WEIGHT * win_rate
    score += _RETURN_WEIGHT * _clamp(avg_return, -_RETURN_CLAMP, _RETURN_CLAMP) / _RETURN_CLAMP
    score -= _RUG_RATE_WEIGHT * rug_rate
    score -= _SHILL_ALERT_WEIGHT * min(shill_count, _SHILL_ALERT_CAP)
    score -= _DELETED_ALERT_WEIGHT * min(deleted_count, _DELETED_ALERT_CAP)
    if known_scammer:
        score -= _SCAMMER_PENALTY
    score += _WALLET_CONF_WEIGHT * wallet_conf
    score = _clamp(score, 0.0, 100.0)

    risk_level = "HIGH" if score < 30 else "MEDIUM" if score < 60 else "LOW"

    tokens = sorted({str(c.get("token", "")).strip().upper() for c in calls if str(c.get("token", "")).strip()})
    record = KOLScoreRecord(
        handle=key,
        score=round(score, 1),
        risk_level=risk_level,
        components={
            "call_accuracy": round(_WIN_RATE_WEIGHT * win_rate, 1),
            "avg_return_24h": round(avg_return, 4),
            "rug_penalty": round(-_RUG_RATE_WEIGHT * rug_rate, 1),
            "documented_rug_penalty": round(
                -_RUG_RATE_WEIGHT * min(1.0, documented_rug_count / (len(tokens) or _DOCUMENTED_RUG_DENOM)), 1
            ),
            "shill_penalty": round(-_SHILL_ALERT_WEIGHT * min(shill_count, _SHILL_ALERT_CAP), 1),
            "deleted_post_penalty": round(-_DELETED_ALERT_WEIGHT * min(deleted_count, _DELETED_ALERT_CAP), 1),
            "scammer_penalty": round(-_SCAMMER_PENALTY, 1) if known_scammer else 0.0,
            "wallet_confidence_bonus": round(_WALLET_CONF_WEIGHT * wallet_conf, 1),
        },
        win_rate=round(win_rate, 4),
        avg_return_24h=round(avg_return, 4),
        rug_rate=round(rug_rate, 4),
        shill_alert_count=shill_count,
        deleted_alert_count=deleted_count,
        known_scammer=known_scammer,
        wallet_confidence=round(wallet_conf, 4),
        total_calls=len(calls),
        total_tokens=len(tokens),
        documented_rug_count=documented_rug_count,
        track_record={
            "calls": calls[-25:],
            "tokens_called": tokens,
            "win_rate": round(win_rate, 4),
            "avg_return_24h": round(avg_return, 4),
            "documented_rug_count": documented_rug_count,
        },
    )

    with _REGISTRY_LOCK:
        KOL_SCORE_REGISTRY[key] = record
    if persist:
        _store_redis(record)
    return record_to_dict(record)


def get_kol_score(handle: str) -> dict[str, Any]:
    """Return the stored score for a handle, computing it if absent."""
    key = handle.lower().lstrip("@")
    with _REGISTRY_LOCK:
        record = KOL_SCORE_REGISTRY.get(key)
    if record is None:
        cached = _load_redis(key)
        if cached is not None:
            return cached
        return score_kol(handle)
    return record_to_dict(record)


def get_kol_leaderboard(limit: int = 20) -> dict[str, Any]:
    """Leaderboard of tracked KOLs ranked by trust score (descending)."""
    limit = max(1, min(200, int(limit)))
    with _REGISTRY_LOCK:
        entries = [record_to_dict(r) for r in KOL_SCORE_REGISTRY.values()]
    entries.sort(key=lambda e: -float(e["score"]))
    return {
        "leaderboard": entries[:limit],
        "total_tracked": len(entries),
        "source": "kol_scoring",
    }


def record_to_dict(record: KOLScoreRecord) -> dict[str, Any]:
    """Serialize a score record for API/Redis use."""
    return {
        "handle": record.handle,
        "score": record.score,
        "risk_level": record.risk_level,
        "components": record.components,
        "win_rate": record.win_rate,
        "avg_return_24h": record.avg_return_24h,
        "rug_rate": record.rug_rate,
        "shill_alert_count": record.shill_alert_count,
        "deleted_alert_count": record.deleted_alert_count,
        "known_scammer": record.known_scammer,
        "wallet_confidence": record.wallet_confidence,
        "total_calls": record.total_calls,
        "total_tokens": record.total_tokens,
        "documented_rug_count": record.documented_rug_count,
        "track_record": record.track_record,
        "computed_at": record.computed_at,
        "found": True,
        "source": "kol_scoring",
    }


# ═══════════════════════════════════════════════════════════════════════
# Runtime registration hooks (used by the pipeline / tests)
# ═══════════════════════════════════════════════════════════════════════


def record_call_outcome(
    handle: str,
    token: str,
    price_at_call: float = 0.0,
    price_after_24h: float = 0.0,
    win: bool | None = None,
    call_type: str = "buy",
) -> None:
    """Record a resolved call outcome (+24h price) for scoring."""
    key = handle.lower().lstrip("@")
    CALL_OUTCOMES.setdefault(key, []).append(
        CallOutcome(
            token=str(token).upper(),
            price_at_call=price_at_call,
            price_after_24h=price_after_24h,
            win=win,
            call_type=call_type,
        )
    )


def register_rug_token(token: str) -> None:
    """Mark a token (symbol or address) as rugged for rug-rate scoring."""
    KNOWN_RUG_TOKENS.add(str(token).strip().upper())


def register_kol_rug(handle: str, entry: dict[str, Any]) -> None:
    """Add a documented rug call for a handle (runtime, mirrors JSON file).

    ``entry`` is any dict describing a documented rug call (``token_symbol``,
    ``token_address``, ``chain``, ``date``, ``outcome``, ``evidence_url``).
    """
    key = handle.lower().lstrip("@")
    if key:
        KOL_RUG_HISTORY.setdefault(key, []).append(dict(entry))


def register_shill_alert(handle: str, count: int = 1) -> None:
    """Increment the shill-alert count for a handle."""
    key = handle.lower().lstrip("@")
    SHILL_ALERTS[key] = SHILL_ALERTS.get(key, 0) + max(0, int(count))


def register_deleted_alert(handle: str, count: int = 1) -> None:
    """Increment the deleted-call count for a handle (suspicious retraction)."""
    key = handle.lower().lstrip("@")
    DELETED_ALERTS[key] = DELETED_ALERTS.get(key, 0) + max(0, int(count))


def register_shill_alerts_from_detector(result: dict[str, Any]) -> int:
    """Ingest ``analyze_shill_signals`` output into per-handle alert counts.

    Returns the number of distinct handles updated. The detector emits
    deduped alerts keyed by ``kol_id`` — the scoring registry consumes the
    count so confirmed alerts feed the shill penalty.
    """
    updated = 0
    for alert in result.get("alerts", []) or []:
        kol_id = str(alert.get("kol_id") or "").strip().lower().lstrip("@")
        if not kol_id:
            continue
        SHILL_ALERTS[kol_id] = SHILL_ALERTS.get(kol_id, 0) + 1
        updated += 1
    return updated


def register_known_scammer(handle: str) -> None:
    """Add a handle to the known-scammer set (runtime, mirrors JSON file)."""
    KNOWN_SCAMMER_HANDLES.add(handle.lower().lstrip("@"))


def register_wallet_confidence(handle: str, confidence: float) -> None:
    """Set wallet-attribution confidence (0..1) for a handle."""
    WALLET_CONFIDENCE[handle.lower().lstrip("@")] = _clamp(float(confidence), 0.0, 1.0)


def clear_registry() -> None:
    """Reset all in-memory state (test helper)."""
    with _REGISTRY_LOCK:
        KOL_SCORE_REGISTRY.clear()
    CALL_OUTCOMES.clear()
    KNOWN_RUG_TOKENS.clear()
    SHILL_ALERTS.clear()
    DELETED_ALERTS.clear()
    WALLET_CONFIDENCE.clear()
    KNOWN_SCAMMER_HANDLES.clear()
    KOL_RUG_HISTORY.clear()


# ═══════════════════════════════════════════════════════════════════════
# Redis persistence (best-effort, mirrors social_sentiment pattern)
# ═══════════════════════════════════════════════════════════════════════

_REDIS_PREFIX = "kol:score:"
_REDIS_TTL = 12 * 3600  # 12h, same as social_sentiment cache


def _store_redis(record: KOLScoreRecord) -> None:
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is None:
            return
        r.set(f"{_REDIS_PREFIX}{record.handle}", json.dumps(record_to_dict(record), default=str), ex=_REDIS_TTL)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("kol score redis store failed: %s", str(e)[:80])


def _load_redis(key: str) -> dict[str, Any] | None:
    try:
        from app.core.redis import get_redis

        r = get_redis()
        if r is None:
            return None
        raw = r.get(f"{_REDIS_PREFIX}{key}")
        if not raw:
            return None
        data = json.loads(raw)
        if isinstance(data, dict) and "score" in data:
            return data
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("kol score redis read failed: %s", str(e)[:80])
    return None


# Load the scammer overlap set once at import time (file may not exist yet).
KNOWN_SCAMMER_HANDLES.update(_load_known_scammers())

# Load the documented KOL rug history once at import time (file may not exist yet).
KOL_RUG_HISTORY.update(_load_kol_rug_history())
