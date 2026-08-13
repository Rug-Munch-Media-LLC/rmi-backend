"""Account-switch / alias tracking for KOL identity.

Resolves renamed/alternate X handles to a canonical identity so posts from
a renamed KOL still attribute to the same ``kol_id`` across the x_intel /
social_intel / shill / scoring pipelines.

Also maintains a ``deleted_posts`` registry: a KOL call that was later
deleted from X is a suspicious signal — the KOL retracted a call after the
fact — and feeds shill detection and KOL scoring (see
:func:`deleted_post_flag` / :func:`deleted_post_count`).

Registry source: ``app/databus/account_aliases.json`` (optional). When the
file is absent every handle is its own canonical identity and the module
degrades to a pass-through — the pipeline stays functional.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("account_alias")

# ═══════════════════════════════════════════════════════════════════════
# Alias registry
# ═══════════════════════════════════════════════════════════════════════

_ALIAS_FILE = Path(__file__).resolve().parent / "account_aliases.json"

#: alias (normalized) -> canonical handle (normalized).
ALIAS_TO_CANONICAL: dict[str, str] = {}
#: normalized canonical -> original casing (for display/API output).
CANONICAL_DISPLAY: dict[str, str] = {}
#: Set of known canonical handles (normalized).
CANONICALS: set[str] = set()
#: Entry metadata by canonical handle (name etc.).
CANONICAL_META: dict[str, dict[str, Any]] = {}

_ALIAS_LOCK = threading.Lock()


def _normalize(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def load_aliases() -> list[dict[str, Any]]:
    """Load alias registry entries. Returns [] when absent/malformed.

    Schema (``account_aliases.json``): ``{"accounts": [{"canonical_handle":
    ..., "aliases": [...], ...}]}`` — the key is ``canonical_handle``, not
    ``canonical`` (the registry is maintained by the fleet's evidence-based
    account-switch sweeps). Both key names are tolerated for robustness.
    """
    try:
        if not _ALIAS_FILE.exists():
            return []
        data = json.loads(_ALIAS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return []
        entries = data.get("accounts") or data.get("entries") or []
        normalized = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            canonical = e.get("canonical_handle") or e.get("canonical")
            if not canonical:
                continue
            # Normalize to the loader's internal entry shape.
            normalized.append(
                {
                    "canonical": str(canonical),
                    "aliases": e.get("aliases", []) or [],
                    "name": e.get("name"),
                    "verified": e.get("verified"),
                }
            )
        return normalized
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"account_aliases.json load failed: {e}")
        return []


def _build_maps(entries: list[dict[str, Any]]) -> None:
    """Rebuild the lookup maps from registry entries."""
    alias_map: dict[str, str] = {}
    canonicals: set[str] = set()
    display: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}
    for entry in entries:
        canonical = _normalize(entry.get("canonical"))
        if not canonical:
            continue
        canonicals.add(canonical)
        display[canonical] = str(entry.get("canonical"))
        meta[canonical] = {
            "name": entry.get("name") or entry.get("canonical"),
            "canonical": entry.get("canonical"),
        }
        # The canonical handle is always its own identity.
        alias_map[canonical] = canonical
        for alias in entry.get("aliases", []) or []:
            norm = _normalize(alias)
            if norm:
                alias_map[norm] = canonical
    with _ALIAS_LOCK:
        ALIAS_TO_CANONICAL.clear()
        ALIAS_TO_CANONICAL.update(alias_map)
        CANONICAL_DISPLAY.clear()
        CANONICAL_DISPLAY.update(display)
        CANONICALS.clear()
        CANONICALS.update(canonicals)
        CANONICAL_META.clear()
        CANONICAL_META.update(meta)


_build_maps(load_aliases())


def canonical_handle(handle: Any) -> str:
    """Resolve any handle (including aliases) to its canonical identity.

    Non-alias handles resolve to themselves. Never raises.
    """
    norm = _normalize(handle)
    return ALIAS_TO_CANONICAL.get(norm, norm)


def _display_canonical(canonical: str) -> str:
    """Pretty (original-casing) form of a normalized canonical handle."""
    return CANONICAL_DISPLAY.get(canonical, canonical)


def resolve_account(account: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an account dict with ``handle`` canonicalized.

    When the original handle was an alias, the original value is preserved
    under ``alias_of`` so display names/attribution history survive.
    """
    if not isinstance(account, dict) or not account.get("handle"):
        return account
    original = str(account["handle"])
    canonical = canonical_handle(original)
    resolved = dict(account)
    resolved["handle"] = original if canonical == _normalize(original) else _display_canonical(canonical)
    if canonical != _normalize(original):
        resolved["alias_of"] = original
    return resolved


def merge_alias_activity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remap a list of post/call records so alias handles attribute to canonical.

    Each record keeps its original author under ``alias_handle`` (when it was
    an alias) and gains a ``canonical_handle`` key. Records are not dropped —
    merging under the canonical record is about attribution keying.
    """
    merged: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            merged.append(rec)
            continue
        out = dict(rec)
        for key in ("handle", "author", "username", "kol_id"):
            if out.get(key):
                original = str(out[key])
                canonical = canonical_handle(original)
                if canonical != _normalize(original):
                    out["alias_handle"] = original
                    out[key] = _display_canonical(canonical)
                break
        out["canonical_handle"] = canonical_handle(
            out.get("handle") or out.get("author") or out.get("username") or out.get("kol_id")
        )
        merged.append(out)
    return merged


def merge_kol_registry(registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge a ``KOL_DATABASE``-like registry so alias keys collapse to canonical.

    Input is keyed ``kol:{handle}``; output is keyed ``kol:{canonical}`` with
    ``calls`` concatenated and ``metrics`` summed (sets unioned). The caller's
    registry is not mutated.
    """
    merged: dict[str, dict[str, Any]] = {}
    for key, data in registry.items():
        handle = key.removeprefix("kol:")
        canonical = canonical_handle(handle)
        new_key = f"kol:{canonical}"
        if new_key not in merged:
            merged[new_key] = {
                "calls": copy.deepcopy(data.get("calls", [])),
                "metrics": copy.deepcopy(data.get("metrics", {})),
            }
            continue
        target = merged[new_key]
        target["calls"].extend(copy.deepcopy(data.get("calls", [])))
        src_metrics = data.get("metrics", {})
        dst_metrics = target["metrics"]
        for k, v in src_metrics.items():
            if k == "tokens_mentioned" and isinstance(v, set):
                dst_metrics.setdefault(k, set()).update(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                dst_metrics[k] = dst_metrics.get(k, 0) + v
            else:
                dst_metrics.setdefault(k, v)
    return merged


# ═══════════════════════════════════════════════════════════════════════
# Deleted-post tracking (suspicious signal)
# ═══════════════════════════════════════════════════════════════════════

#: canonical handle -> list of deleted-call records.
DELETED_POSTS: dict[str, list[dict[str, Any]]] = {}
_DELETED_LOCK = threading.Lock()
_MAX_DELETED_PER_KOL = 200


def record_deleted_post(
    handle: Any,
    token: str,
    call_type: str = "buy",
    chain: str = "solana",
    reason: str = "deleted",
    original_handle: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """Record a KOL call that was later deleted from X.

    Keyed by canonical handle + token so alias calls land on the same record.
    Returns the stored record.
    """
    canonical = canonical_handle(handle)
    entry = {
        "handle": canonical,
        "original_handle": str(original_handle or handle or ""),
        "token": str(token or ""),
        "type": call_type,
        "chain": chain,
        "reason": reason,
        "timestamp": datetime.now(UTC).isoformat(),
        "id": hashlib.sha256(f"{canonical}|{token}|{time.time()}".encode()).hexdigest()[:8],
    }
    entry.update({k: v for k, v in kw.items() if k not in entry})
    with _DELETED_LOCK:
        bucket = DELETED_POSTS.setdefault(canonical, [])
        bucket.append(entry)
        del bucket[: max(0, len(bucket) - _MAX_DELETED_PER_KOL)]
    return entry


def get_deleted_posts(handle: Any = None, token: str | None = None) -> list[dict[str, Any]]:
    """Return deleted-post records, optionally filtered by handle and/or token."""
    canonical = canonical_handle(handle) if handle is not None else None
    with _DELETED_LOCK:
        records = (
            list(DELETED_POSTS.get(canonical, []))
            if canonical is not None
            else [rec for bucket in DELETED_POSTS.values() for rec in bucket]
        )
    if token:
        records = [r for r in records if str(r.get("token", "")).lower() == str(token).lower()]
    return records


def deleted_post_count(handle: Any = None, token: str | None = None) -> int:
    """Count deleted-post records, optionally filtered by handle and/or token."""
    return len(get_deleted_posts(handle=handle, token=token))


def deleted_post_flag(handle: Any, token: str | None = None) -> bool:
    """True when the KOL has deleted any call (optionally for a given token)."""
    return deleted_post_count(handle=handle, token=token) > 0


def clear_deleted_posts() -> None:
    """Reset the deleted-posts registry (test/ops helper)."""
    with _DELETED_LOCK:
        DELETED_POSTS.clear()
