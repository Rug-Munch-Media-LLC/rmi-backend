"""
Deployer Reputation — behavioral fingerprint matching for scanner domain.

Part of the RMI scanner pipeline. Analyzes deployer behavior patterns
to produce predictive risk scores: goes beyond "this token IS a scam"
to "this deployer's fingerprint matches known scammers who haven't
rugged YET."

Signals analyzed:
  - First deployment? (new deployers are higher risk)
  - Funded by mixer/tornado? (laundering attempt)
  - Funded by CEX hot wallet? (legitimate)
  - Source code verified? (legitimacy signal)
  - Matches known scammer behavioral fingerprint?
  - Deployment frequency (batch deployer?)
  - Gas patterns (flashbot/MEV bundler?)
  - Contract bytecode similarity to known scams

Returns a 0-100 risk score with human-readable explanation.

Two APIs live here:

  1. ``score_deployer_risk()`` / ``build_behavioral_fingerprint()`` — the
     legacy behavioral-fingerprint scoring (re-exported by the
     ``app/deployer_reputation.py`` compat shim for ``app/streaming_rag.py``).

  2. ``deployer_reputation()`` / ``reputation_signal()`` — the bot-facing
     API that flags "repeat rug deployer" for RugMunchBot scans. Async,
     fail-open, injectable fetchers (see below).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Risk signal weights ──────────────────────────────────────────
SIGNALS: dict[str, dict[str, Any]] = {
    "first_deployment": {
        "weight": 0.15,
        "description": "First-time deployer (no prior contracts)",
        "risk_level": "medium",
    },
    "mixer_funded": {
        "weight": 0.25,
        "description": "Funded via Tornado Cash or mixer",
        "risk_level": "critical",
    },
    "cex_funded": {
        "weight": -0.15,  # negative = reduces risk
        "description": "Funded via centralized exchange (likely KYC'd)",
        "risk_level": "low",
    },
    "unverified_source": {
        "weight": 0.10,
        "description": "Contract source code not verified",
        "risk_level": "medium",
    },
    "batch_deployer": {
        "weight": 0.15,
        "description": "Deployer has >10 contracts (potential factory/scam ring)",
        "risk_level": "high",
    },
    "known_scammer_match": {
        "weight": 0.30,
        "description": "Behavioral fingerprint matches known scammer",
        "risk_level": "critical",
    },
    "code_similar_to_scam": {
        "weight": 0.20,
        "description": "Contract bytecode similar to known scam patterns",
        "risk_level": "high",
    },
    "funded_by_scammer": {
        "weight": 0.35,
        "description": "Deployer funded by known scam address",
        "risk_level": "critical",
    },
    "cloned_contract": {
        "weight": 0.12,
        "description": "Exact bytecode match to another deployed contract (cloned token)",
        "risk_level": "medium",
    },
    "high_deployment_velocity": {
        "weight": 0.15,
        "description": "Multiple deployments in short time window",
        "risk_level": "high",
    },
}

# ── Known scammer behavioral fingerprints ────────────────────────
# Each is a vector of [deployment_count, avg_interval_hours, mixer_funded,
# verified_pct, code_uniqueness, funding_chain_length]
KNOWN_PATTERNS: dict[str, dict[str, Any]] = {
    "rug_pull_factory": {
        "vector": [25, 2, 1, 0, 0.3, 1],
        "description": "Mass token deployer - dozens of unverified tokens, quick exits",
        "risk_modifier": 0.85,
    },
    "honeypot_operator": {
        "vector": [8, 48, 0, 0, 0.5, 2],
        "description": "Periodic deployer of honeypot tokens with similar bytecode",
        "risk_modifier": 0.80,
    },
    "phishing_ring": {
        "vector": [50, 1, 1, 0, 0.1, 1],
        "description": "High-volume airdrop phishing - hundreds of identical tokens",
        "risk_modifier": 0.95,
    },
    "impersonation_scammer": {
        "vector": [3, 720, 0, 1, 0.9, 1],
        "description": "Rare deployer, verified source, clones legitimate token code",
        "risk_modifier": 0.70,
    },
    "legitimate_dev": {
        "vector": [1, 8760, 0, 1, 1.0, 3],
        "description": "Single verified deployment from established developer",
        "risk_modifier": 0.05,
    },
}


def build_behavioral_fingerprint(
    address: str,
    deployment_count: int = 0,
    avg_interval_hours: float = 0,
    mixer_funded: bool = False,
    cex_funded: bool = False,
    source_verified: bool = False,
    code_uniqueness: float = 1.0,
    funding_chain_length: int = 0,
    rapid_deploy: bool = False,
    cloned_contract: bool = False,
    funded_by_scammer: bool = False,
) -> np.ndarray:
    """
    Build a behavioral fingerprint vector from deployer on-chain data.

    Vector dimensions:
      [0] deployment_count (normalized 0-50)
      [1] avg_interval_hours (normalized 0-168)
      [2] mixer_funded (0/1)
      [3] source_verified (0/1)
      [4] code_uniqueness (0-1)
      [5] funding_chain_length (normalized 0-10)
    """
    vec = np.array(
        [
            min(deployment_count, 50) / 50.0,
            min(avg_interval_hours, 168) / 168.0,
            1.0 if mixer_funded else 0.0,
            1.0 if source_verified else 0.0,
            code_uniqueness,
            min(funding_chain_length, 10) / 10.0,
        ],
        dtype=np.float32,
    )

    return vec


def score_deployer_risk(
    address: str,
    deployment_count: int = 0,
    avg_interval_hours: float = 0,
    mixer_funded: bool = False,
    cex_funded: bool = False,
    source_verified: bool = False,
    code_similarity: float = 0.0,
    code_uniqueness: float = 1.0,
    funding_chain_length: int = 0,
    rapid_deploy: bool = False,
    cloned_contract: bool = False,
    funded_by_scammer: bool = False,
) -> dict[str, Any]:
    """
    Score a deployer's pre-crime risk based on behavioral fingerprint.

    Parameters
    ----------
    address: Deployer wallet address
    deployment_count: Number of contracts deployed by this address
    avg_interval_hours: Average hours between deployments
    mixer_funded: True if deployer was funded via Tornado/mixer
    cex_funded: True if funded via centralized exchange
    source_verified: True if contract source is verified
    code_similarity: Cosine similarity to known scam bytecode (0-1)
    code_uniqueness: How unique the bytecode is (0-1, 1=completely unique)
    funding_chain_length: Number of hops from funding source
    rapid_deploy: True if >5 deploys in <6 hours
    cloned_contract: True if exact bytecode match to another deployed contract
    funded_by_scammer: True if funding came from known scam address

    Returns
    -------
    Dict with:
        - risk_score: int 0-100
        - risk_label: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
        - signals_found: List of triggered risk signals
        - mitigations: List of positive signals reducing risk
        - fingerprint_match: Closest known pattern match
        - summary: Human-readable one-liner
    """
    signals_found = []
    mitigations = []
    risk_score = 0.0

    # ── Evaluate each signal ──
    if deployment_count == 0:
        signals_found.append(SIGNALS["first_deployment"])
        risk_score += SIGNALS["first_deployment"]["weight"]

    if mixer_funded:
        signals_found.append(SIGNALS["mixer_funded"])
        risk_score += SIGNALS["mixer_funded"]["weight"]

    if cex_funded:
        mitigations.append(SIGNALS["cex_funded"])
        risk_score += SIGNALS["cex_funded"]["weight"]  # negative weight reduces score

    if not source_verified and deployment_count > 0:
        signals_found.append(SIGNALS["unverified_source"])
        risk_score += SIGNALS["unverified_source"]["weight"]

    if deployment_count > 10:
        signals_found.append(SIGNALS["batch_deployer"])
        risk_score += SIGNALS["batch_deployer"]["weight"]

    if code_similarity > 0.7:
        signals_found.append(SIGNALS["code_similar_to_scam"])
        risk_score += SIGNALS["code_similar_to_scam"]["weight"]

    if funded_by_scammer:
        signals_found.append(SIGNALS["funded_by_scammer"])
        risk_score += SIGNALS["funded_by_scammer"]["weight"]

    if cloned_contract:
        signals_found.append(SIGNALS["cloned_contract"])
        risk_score += SIGNALS["cloned_contract"]["weight"]

    if rapid_deploy:
        signals_found.append(SIGNALS["high_deployment_velocity"])
        risk_score += SIGNALS["high_deployment_velocity"]["weight"]

    # ── Behavioral fingerprint matching ──
    fingerprint = build_behavioral_fingerprint(
        address,
        deployment_count=deployment_count,
        avg_interval_hours=avg_interval_hours,
        mixer_funded=mixer_funded,
        source_verified=source_verified,
        code_uniqueness=code_uniqueness,
        funding_chain_length=funding_chain_length,
    )

    best_match = None
    best_similarity = 0.0
    for name, pattern in KNOWN_PATTERNS.items():
        pattern_vec = np.array(pattern["vector"], dtype=np.float32)
        # Cosine similarity between fingerprint and pattern
        sim = np.dot(fingerprint, pattern_vec) / (np.linalg.norm(fingerprint) * np.linalg.norm(pattern_vec) + 1e-8)
        if sim > best_similarity:
            best_similarity = float(sim)
            best_match = {
                "pattern": name,
                "similarity": round(best_similarity, 3),
                "description": pattern["description"],
                "risk_modifier": pattern["risk_modifier"],
            }

    if best_similarity > 0.7 and best_match and best_match["pattern"] != "legitimate_dev":
        signals_found.append(SIGNALS["known_scammer_match"])
        risk_score += SIGNALS["known_scammer_match"]["weight"]
        # Apply pattern-specific modifier
        risk_score *= 1.0 + best_match["risk_modifier"] * 0.3

    # ── Normalize to 0-100 ──
    risk_score = min(100, max(0, risk_score * 100))

    # ── Label ──
    if risk_score >= 80:
        label = "CRITICAL"
    elif risk_score >= 60:
        label = "HIGH"
    elif risk_score >= 35:
        label = "MEDIUM"
    else:
        label = "LOW"

    # ── Summary ──
    signal_count = len(signals_found)
    mitigation_count = len(mitigations)
    parts = []

    if signal_count == 0 and mitigation_count > 0:
        parts.append(f"Low risk ({risk_score:.0f}%) - strong positive signals")
    elif signal_count == 0:
        parts.append(f"Low risk ({risk_score:.0f}%) - insufficient data for assessment")
    elif risk_score >= 80:
        parts.append(f"CRITICAL risk ({risk_score:.0f}%) - {signal_count} warning signals")
    else:
        parts.append(f"{label} risk ({risk_score:.0f}%) - {signal_count} signals")

    if best_match and best_similarity > 0.5:
        parts.append(f"fingerprint matches '{best_match['description']}' (similarity: {best_similarity:.2f})")

    if mitigations:
        mitigating = [m["description"] for m in mitigations]
        parts.append(f"mitigated by: {'; '.join(mitigating[:2])}")

    return {
        "risk_score": round(risk_score),
        "risk_label": label,
        "signals_found": [
            {"signal": s["description"], "risk": s["risk_level"], "weight": s["weight"]} for s in signals_found
        ],
        "mitigations": [{"signal": m["description"], "impact": m["weight"]} for m in mitigations],
        "fingerprint_match": best_match,
        "fingerprint_vector": fingerprint.tolist(),
        "summary": ". ".join(parts),
        "address": address,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Bot-facing deployer reputation API — "repeat rug deployer" flag
# ═══════════════════════════════════════════════════════════════════════════
# Pure, testable core + injectable async fetchers. Every default fetcher is
# fail-open (returns None/False rather than raising), so a missing ClickHouse
# connection, missing databus JSON, or a query error degrades to a neutral
# score instead of breaking the scan.

#: Fetcher signatures (all async, all fail-open).
PriorDeploymentsFetcher = Callable[[str, str], Awaitable[int | None]]
RugHistoryFetcher = Callable[[str, str], Awaitable[bool]]
FundingSourceFetcher = Callable[[str, str], Awaitable[str | None]]
BadActorFetcher = Callable[[str], Awaitable[bool]]

#: Scoring weights — 0-100 risk, higher = riskier, 0 = neutral/clean.
_DEPLOYMENT_PER_TOKEN = 6.0  # +6 per prior token, capped below
_DEPLOYMENT_CAP = 40.0
_REPEAT_DEPLOYER_WEIGHT = 18.0  # 2+ prior tokens
_BATCH_DEPLOYER_WEIGHT = 12.0  # 10+ prior tokens
_RUG_HISTORY_WEIGHT = 40.0
_BAD_ACTOR_FUNDER_WEIGHT = 25.0
_MAX_SCORE = 100.0

_REPEAT_DEPLOYER_MIN = 2  # prior deployments before we call it a repeat deployer
_BATCH_DEPLOYER_MIN = 10

#: Databus JSON files used by the default bad-actor lookup.
_DATABUS_DIR = Path(__file__).resolve().parent.parent.parent / "databus"
_KNOWN_SCAMMERS_PATH = _DATABUS_DIR / "known_scammers.json"
_KOL_RUG_HISTORY_PATH = _DATABUS_DIR / "kol_rug_history.json"
_VERIFIED_KOL_WALLETS_PATH = _DATABUS_DIR / "verified_kol_wallets.json"

#: Lazy-loaded lookup index: address/handle -> bad-actor flags.
_scam_db: dict[str, Any] | None = None


def _norm_lookup_key(value: str) -> str:
    """Normalize an address/handle for lookup: lowercase EVM 0x, keep base58."""
    value = (value or "").strip()
    return value.lower() if value.startswith("0x") else value


def _load_scam_db() -> dict[str, Any]:
    """Load scam/KOL-rug databus files into a lookup index (lazy, fail-open).

    Files are optional: absence or malformed JSON yields empty sets/maps so
    the default bad-actor lookup degrades to "not flagged" (never raises).
    """
    global _scam_db
    if _scam_db is not None:
        return _scam_db

    index: dict[str, Any] = {
        "scammer_addresses": set(),  # direct address matches (future-proof)
        "scammer_handles": set(),
        "rug_handles": set(),  # KOL handles with documented rug history
        "wallet_to_handle": {},  # verified wallet address -> KOL handle
    }

    try:
        if _KNOWN_SCAMMERS_PATH.exists():
            raw = json.loads(_KNOWN_SCAMMERS_PATH.read_text(encoding="utf-8"))
            accounts = raw.get("accounts") if isinstance(raw, dict) else raw
            for acct in accounts or []:
                if not isinstance(acct, dict):
                    continue
                handle = _norm_lookup_key(str(acct.get("handle") or ""))
                if handle:
                    index["scammer_handles"].add(handle)
                for key in ("address", "addresses", "wallet"):
                    value = acct.get(key)
                    for addr in value if isinstance(value, list) else [value]:
                        if addr:
                            index["scammer_addresses"].add(_norm_lookup_key(str(addr)))
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("deployer_reputation_known_scammers_unreadable: %s", exc)
        index["scammer_addresses"] = set()
        index["scammer_handles"] = set()

    try:
        if _KOL_RUG_HISTORY_PATH.exists():
            raw = json.loads(_KOL_RUG_HISTORY_PATH.read_text(encoding="utf-8"))
            entries = raw.get("kol_rug_history") if isinstance(raw, dict) else raw
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                handle = _norm_lookup_key(str(entry.get("handle") or entry.get("kol") or ""))
                if handle and entry.get("rugs"):
                    index["rug_handles"].add(handle)
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("deployer_reputation_kol_rug_history_unreadable: %s", exc)
        index["rug_handles"] = set()

    try:
        if _VERIFIED_KOL_WALLETS_PATH.exists():
            raw = json.loads(_VERIFIED_KOL_WALLETS_PATH.read_text(encoding="utf-8"))
            wallets = raw.get("wallets") if isinstance(raw, dict) else raw
            for wallet in wallets or []:
                if not isinstance(wallet, dict):
                    continue
                addr = _norm_lookup_key(str(wallet.get("address") or ""))
                handle = _norm_lookup_key(str(wallet.get("handle") or ""))
                if addr and handle:
                    index["wallet_to_handle"][addr] = handle
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("deployer_reputation_verified_kol_wallets_unreadable: %s", exc)
        index["wallet_to_handle"] = {}

    _scam_db = index
    return index


def _lookup_bad_actor(address: str) -> tuple[bool, str]:
    """Return ``(flagged, description)`` when address is a known bad actor.

    Checks, in order:
      1. direct address match in known_scammers address fields
      2. verified KOL wallet mapping: address belongs to a known-scammer KOL
      3. verified KOL wallet mapping: address belongs to a KOL with rug history
      4. address itself is a known-scammer / rug KOL handle (handle-shaped input)
    """
    key = _norm_lookup_key(address)
    if not key:
        return False, ""
    db = _load_scam_db()
    if key in db["scammer_addresses"]:
        return True, "address listed in known_scammers"
    handle = db["wallet_to_handle"].get(key)
    if handle:
        if handle in db["scammer_handles"]:
            return True, f"wallet of known scammer {handle}"
        if handle in db["rug_handles"]:
            return True, f"wallet of KOL {handle} with documented rug history"
    if key in db["scammer_handles"]:
        return True, "handle listed in known_scammers"
    if key in db["rug_handles"]:
        return True, "handle has documented rug history"
    return False, ""


async def _default_fetch_prior_deployments(address: str, chain: str) -> int | None:
    """Count prior token deployments from own ClickHouse (deployer_events).

    Cross-chain count: a repeat deployer on another chain is still a repeat.
    Returns ``None`` when ClickHouse is unavailable or the query fails.
    """
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return None
        rows = client.execute(
            """
            SELECT count(DISTINCT token_address)
            FROM wallet_memory.deployer_events
            WHERE deployer = %(deployer)s
            """,
            {"deployer": _norm_lookup_key(address)},
            settings={"max_execution_time": 5},
        )
        if rows and rows[0]:
            return int(rows[0][0] or 0)
        return 0
    except Exception as exc:  # fail-open by contract
        logger.debug("deployer_reputation_prior_deployments_failed: %s", exc)
        return None


async def _default_fetch_rug_history(address: str, chain: str) -> bool:
    """True when the address matches a known scammer / rug-history KOL."""
    flagged, _ = _lookup_bad_actor(address)
    return flagged


async def _default_is_bad_actor(address: str) -> bool:
    """True when the address is a known scammer / rug-history KOL."""
    flagged, _ = _lookup_bad_actor(address)
    return flagged


async def _default_fetch_funding_source(address: str, chain: str) -> str | None:
    """First wallet that funded the address (evm_transfers), best-effort.

    Returns the normalized funder address, or ``None`` when the chain is
    unknown, ClickHouse is unavailable, or the address has no incoming
    transfer (new wallet).
    """
    if not chain:
        return None
    try:
        from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return None
        rows = client.execute(
            """
            SELECT from_address
            FROM wallet_memory.evm_transfers
            WHERE to_address = %(address)s
              AND chain = %(chain)s
              AND from_address NOT IN ('', %(zero)s, %(dead)s)
            ORDER BY block_timestamp ASC
            LIMIT 1
            """,
            {
                "address": _norm_lookup_key(address),
                "chain": (chain or "").lower(),
                "zero": "0x0000000000000000000000000000000000000000",
                "dead": "0x000000000000000000000000000000000000dead",
            },
            settings={"max_execution_time": 5},
        )
        if rows and rows[0]:
            return _norm_lookup_key(str(rows[0][0] or "")) or None
        return None
    except Exception as exc:  # fail-open by contract
        logger.debug("deployer_reputation_funding_source_failed: %s", exc)
        return None


async def _run_fetch(fetch: Callable[..., Awaitable[Any]] | None, *args: Any) -> Any:
    """Await an optional fetcher; return ``None`` on any failure (fail-open)."""
    if fetch is None:
        return None
    try:
        return await fetch(*args)
    except Exception as exc:  # fail-open: fetcher errors degrade to neutral
        logger.debug("deployer_reputation_fetch_failed: %s", exc)
        return None


async def deployer_reputation(
    address: str,
    chain: str,
    *,
    fetch_prior_deployments: PriorDeploymentsFetcher | None = None,
    fetch_rug_history: RugHistoryFetcher | None = None,
    fetch_funding_source: FundingSourceFetcher | None = None,
    is_bad_actor: BadActorFetcher | None = None,
) -> dict[str, Any]:
    """Score a deployer's reputation (0-100 risk; higher = riskier).

    Best-effort signals:
      - prior token deployments (deployer_events) — more = riskier
      - rug / scam history (known_scammers / kol_rug_history / scam DB)
      - funding source that is itself a known bad actor

    Fail-open: any fetcher error yields that signal's neutral value; a total
    failure returns ``{"score": 0, "prior_deployments": None, ...}``.

    Args:
        address: Deployer wallet address.
        chain: Source chain (ethereum, bsc, solana, ...).
        fetch_prior_deployments: async (address, chain) -> int | None
        fetch_rug_history: async (address, chain) -> bool
        fetch_funding_source: async (address, chain) -> str | None (funder id)
        is_bad_actor: async (address) -> bool

    Returns:
        {
            "score": float,                 # 0-100, 0 = neutral
            "prior_deployments": int | None,
            "repeat_deployer": bool,        # 2+ prior deployments
            "rug_history": bool,
            "funding_source": str | None,
            "evidence": str | None,
        }
    """
    neutral = {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }
    try:
        prior, rug, funder = await asyncio.gather(
            _run_fetch(fetch_prior_deployments or _default_fetch_prior_deployments, address, chain),
            _run_fetch(fetch_rug_history or _default_fetch_rug_history, address, chain),
            _run_fetch(fetch_funding_source or _default_fetch_funding_source, address, chain),
        )
    except Exception:  # fail-open by contract
        return dict(neutral)

    prior_count = int(prior) if isinstance(prior, (int, float)) and prior is not None else None
    rug_flag = bool(rug)
    funder_id = str(funder) if funder else None

    # A funding source that is itself a known bad actor is a strong signal.
    funder_bad = False
    if funder_id:
        funder_bad = bool(await _run_fetch(is_bad_actor or _default_is_bad_actor, funder_id))

    score = 0.0
    evidence_parts: list[str] = []
    if prior_count is not None and prior_count > 0:
        score += min(prior_count * _DEPLOYMENT_PER_TOKEN, _DEPLOYMENT_CAP)
        if prior_count >= _REPEAT_DEPLOYER_MIN:
            score += _REPEAT_DEPLOYER_WEIGHT
        if prior_count >= _BATCH_DEPLOYER_MIN:
            score += _BATCH_DEPLOYER_WEIGHT
        evidence_parts.append(f"deployer launched {prior_count} tokens")
    if rug_flag:
        score += _RUG_HISTORY_WEIGHT
        evidence_parts.append("deployer has rug history")
    if funder_bad:
        score += _BAD_ACTOR_FUNDER_WEIGHT
        evidence_parts.append(f"funded by known bad actor {funder_id}")

    return {
        "score": round(min(score, _MAX_SCORE), 1),
        "prior_deployments": prior_count,
        "repeat_deployer": prior_count is not None and prior_count >= _REPEAT_DEPLOYER_MIN,
        "rug_history": rug_flag,
        "funding_source": funder_id,
        "evidence": "; ".join(evidence_parts) if evidence_parts else None,
    }


def reputation_signal(result: dict[str, Any]) -> str | None:
    """Render a short human signal for a :func:`deployer_reputation` result.

    Priority: rug history > deployed-tokens count > generic repeat. Returns
    ``None`` when the deployer is clean / unknown.

    Examples:
        {"rug_history": True}                         -> "deployer has rug history"
        {"prior_deployments": 12}                     -> "deployer launched 12 tokens"
        {"repeat_deployer": True, "prior": None}      -> "repeat deployer"
        {"prior_deployments": 0}                      -> None
    """
    if result.get("rug_history"):
        return "deployer has rug history"
    prior = result.get("prior_deployments")
    if isinstance(prior, (int, float)) and prior >= 1:
        return f"deployer launched {int(prior)} tokens"
    if result.get("repeat_deployer"):
        return "repeat deployer"
    return None
