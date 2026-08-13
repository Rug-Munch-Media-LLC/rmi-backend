"""KOL identity registry — docs/SHILL-TRACKING-METHODOLOGY.md §2.1.

Implements the *identity binding* layer of the shill-tracking pipeline:
identity is bound to an immutable ``kol_uid``, never a screen name.

On first observation a KOL's handle is minted into a deterministic
``kol_uid`` (sha256 of the first-seen lowercase handle). A poll loop
compares profile snapshots with :func:`detect_rename`; when the handle
changes or the follower count drifts >75% (an account-switch /
abandonment signal), a ``kol.identity_changed`` event is emitted and the
handle-history ledger records ``(old_handle, new_handle, detected_at)``.

Persistence is **write-once**: rows are appended to
``app/databus/kol_identity.json`` and never updated or hard-deleted. A
rename appends a *superseding* row for the same ``kol_uid`` (with the
ledger attached); on load, later rows for a ``kol_uid`` supersede earlier
ones while the full history remains in the file.

Pure stdlib on purpose — this module must stay importable by scanners and
pipeline code with zero heavyweight dependencies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("databus.kol_identity")

#: Default append-only ledger path (write-once identity rows).
DEFAULT_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "kol_identity.json")

#: Follower-drift threshold: if the current follower count has fallen to
#: <= 25% of the previous snapshot (drift > 75%), treat the account as
#: switched / abandoned (methodology §2.3: 100k -> 10k = switched).
#: [REVIEW] Initial guess — calibrate against labeled account-switch data.
FOLLOWER_DRIFT_THRESHOLD = 0.75

#: Event type emitted by :func:`detect_rename` (methodology §2.3).
IDENTITY_CHANGED_EVENT = "kol.identity_changed"


# ── Handle normalization (mirrors kol_wallet_linker.normalize_handle) ──


def normalize_handle(handle: Any) -> str:
    """Lower-case, strip '@' — the canonical handle form."""
    return str(handle or "").lstrip("@").strip().lower()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _as_int(value: Any) -> int | None:
    """Coerce a follower-count value to int, tolerating str/float/None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


# ── Identity minting / profile hashing ─────────────────────────────


def mint_kol_uid(handle: str) -> str:
    """Mint a deterministic, stable UID from a first-seen handle.

    ``sha256(first-seen lowercase handle)`` hex digest. Identical handles
    (modulo casing / leading '@') always mint the same UID, so the UID is
    stable across re-observations while remaining opaque to callers.

    [REVIEW] sha256 of the handle is deterministic but *revealing* — the
    handle can be brute-forced from the UID. Fine for an internal
    identity ledger; do not use as a public commitment / privacy handle.
    """
    key = normalize_handle(handle)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def profile_hash(name: str, avatar_url: str, bio: str) -> str:
    """Stable hash of the mutable profile fields (name/avatar/bio).

    Used by the polling loop to detect *any* profile change; identity
    semantics are then decided by :func:`detect_rename`. None is treated
    as the empty string so snapshots with missing fields hash stably.
    """
    payload = "\x1f".join(
        [
            str(name or "").strip(),
            str(avatar_url or "").strip(),
            str(bio or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_rename(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    """Compare two profile snapshots and emit a ``kol.identity_changed`` event.

    Snapshots are dicts with at least a ``handle`` key; optional keys:
    ``followers`` (int/str), plus ``name``/``avatar_url``/``bio`` (not
    consulted here — kept for the polling loop via :func:`profile_hash`).

    Returns an event dict ``{type, kol_uid, old_handle, new_handle,
    reason, detected_at}`` when either:

      - the handle changed (``reason="handle_changed"``), or
      - follower drift exceeds :data:`FOLLOWER_DRIFT_THRESHOLD`
        (``reason="follower_drift"``) — the account-switch signal.
      - both → ``reason="handle_changed_and_follower_drift"``.

    Returns ``None`` when the identity is unchanged.
    """
    prev_handle = normalize_handle(previous.get("handle"))
    cur_handle = normalize_handle(current.get("handle"))

    reasons: list[str] = []
    if prev_handle and cur_handle and prev_handle != cur_handle:
        reasons.append("handle_changed")

    prev_followers = _as_int(previous.get("followers"))
    cur_followers = _as_int(current.get("followers"))
    if (
        prev_followers is not None
        and cur_followers is not None
        and prev_followers > 0
        and (1.0 - cur_followers / prev_followers) > FOLLOWER_DRIFT_THRESHOLD
    ):
        reasons.append("follower_drift")

    if not reasons:
        return None

    return {
        "type": IDENTITY_CHANGED_EVENT,
        "kol_uid": mint_kol_uid(prev_handle or cur_handle),
        "old_handle": prev_handle,
        "new_handle": cur_handle,
        "reason": "_and_".join(reasons),
        "detected_at": _now_iso(),
    }


# ── Data model ─────────────────────────────────────────────────────


@dataclass(slots=True)
class KolIdentity:
    """A bound KOL identity (immutable UID + mutable profile state).

    ``handle_history`` is the ledger: a list of
    ``{old_handle, new_handle, detected_at}`` rename records. The ledger
    is append-only and never pruned — old handles stay searchable.
    """

    kol_uid: str = ""
    current_handle: str = ""
    display_name: str = ""
    avatar_hash: str = ""
    bio_hash: str = ""
    follower_snapshot: int = 0
    handle_history: list[dict[str, str]] = field(default_factory=list)
    first_seen_at: str = field(default_factory=_now_iso)
    last_updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly ledger row."""
        return {
            "kol_uid": self.kol_uid,
            "current_handle": self.current_handle,
            "display_name": self.display_name,
            "avatar_hash": self.avatar_hash,
            "bio_hash": self.bio_hash,
            "follower_snapshot": self.follower_snapshot,
            "handle_history": [dict(h) for h in self.handle_history],
            "first_seen_at": self.first_seen_at,
            "last_updated_at": self.last_updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KolIdentity | None:
        """Rebuild from a ledger row; ``None`` when the row is unusable."""
        if not isinstance(data, dict):
            return None
        current_handle = normalize_handle(data.get("current_handle"))
        kol_uid = str(data.get("kol_uid") or "").strip()
        if not kol_uid:
            kol_uid = mint_kol_uid(current_handle) if current_handle else ""
        if not kol_uid and not current_handle:
            return None
        history: list[dict[str, str]] = []
        for entry in data.get("handle_history", []) or []:
            if isinstance(entry, dict):
                old = normalize_handle(entry.get("old_handle"))
                new = normalize_handle(entry.get("new_handle"))
                if old or new:
                    history.append(
                        {
                            "old_handle": old,
                            "new_handle": new,
                            "detected_at": str(entry.get("detected_at") or _now_iso()),
                        }
                    )
        followers = _as_int(data.get("follower_snapshot")) or 0
        return cls(
            kol_uid=kol_uid,
            current_handle=current_handle,
            display_name=str(data.get("display_name") or ""),
            avatar_hash=str(data.get("avatar_hash") or ""),
            bio_hash=str(data.get("bio_hash") or ""),
            follower_snapshot=followers,
            handle_history=history,
            first_seen_at=str(data.get("first_seen_at") or _now_iso()),
            last_updated_at=str(data.get("last_updated_at") or _now_iso()),
        )


# ── Registry ───────────────────────────────────────────────────────


class IdentityRegistry:
    """In-process + append-only JSON identity registry.

    ``record(identity)`` stores a row and persists it to the ledger. A
    rename is recorded as a *superseding* row for the same ``kol_uid``:
    ``first_seen_at`` is preserved and the rename is appended to the
    handle-history ledger. Rows are never deleted or edited in place —
    :meth:`to_list` returns the full append-only history, while
    :meth:`all_` returns the current (last-row-wins) state.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._path = Path(path) if path is not None else Path(DEFAULT_REGISTRY_PATH)
        self._identities: dict[str, KolIdentity] = {}
        #: current + historical handle -> kol_uid (old handles stay searchable).
        self._handle_index: dict[str, str] = {}
        #: Append-only row history (superseded rows included).
        self._rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._load()

    # ── Public API ────────────────────────────────────────────────

    def record(self, identity: KolIdentity) -> KolIdentity:
        """Store an identity row (write-once semantics) and persist it.

        Re-recording an existing ``kol_uid`` supersedes the previous row:
        ``first_seen_at`` is preserved, any handle change is appended to
        ``handle_history``, and the previous row remains in the ledger.
        Returns the stored (possibly merged) identity.
        """
        with self._lock:
            # The registry stores the canonical (lowercase, '@'-less) form.
            identity.current_handle = normalize_handle(identity.current_handle)
            if not identity.kol_uid:
                identity.kol_uid = mint_kol_uid(identity.current_handle)
            existing = self._identities.get(identity.kol_uid)
            now = _now_iso()

            if existing is not None:
                # Immutable mint time — never moves forward.
                if not identity.first_seen_at or identity.first_seen_at > existing.first_seen_at:
                    identity.first_seen_at = existing.first_seen_at
                if not identity.handle_history:
                    identity.handle_history = list(existing.handle_history)
                elif existing.handle_history:
                    # Merge: existing ledger entries always survive re-records.
                    merged: dict[tuple[str, str, str], dict[str, str]] = {
                        (h.get("old_handle", ""), h.get("new_handle", ""), h.get("detected_at", "")): dict(h)
                        for h in existing.handle_history
                    }
                    for h in identity.handle_history:
                        merged.setdefault(
                            (h.get("old_handle", ""), h.get("new_handle", ""), h.get("detected_at", "")), dict(h)
                        )
                    identity.handle_history = sorted(merged.values(), key=lambda h: h.get("detected_at", ""))
                # Auto-ledger a rename observed on the existing identity.
                if (
                    existing.current_handle
                    and identity.current_handle
                    and existing.current_handle != identity.current_handle
                ):
                    already = any(
                        normalize_handle(h.get("old_handle")) == existing.current_handle
                        and normalize_handle(h.get("new_handle")) == identity.current_handle
                        for h in identity.handle_history
                    )
                    if not already:
                        identity.handle_history.append(
                            {
                                "old_handle": existing.current_handle,
                                "new_handle": identity.current_handle,
                                "detected_at": now,
                            }
                        )

            identity.last_updated_at = now
            self._identities[identity.kol_uid] = identity

            # Keep old handles searchable; latest row wins on handle reuse.
            if existing is not None and existing.current_handle != identity.current_handle:
                self._handle_index[existing.current_handle] = identity.kol_uid
            self._handle_index[identity.current_handle] = identity.kol_uid
            for h in identity.handle_history:
                old = normalize_handle(h.get("old_handle"))
                if old:
                    self._handle_index[old] = identity.kol_uid

            row = identity.to_dict()
            self._rows.append(row)
            self._persist()
            return identity

    def get(self, kol_uid: str) -> KolIdentity | None:
        """Return the current identity for a UID (``None`` when unknown)."""
        with self._lock:
            return self._identities.get(kol_uid.strip())

    def get_by_handle(self, handle: str) -> KolIdentity | None:
        """Resolve a handle (current or historical) to its identity.

        Handle lookup is case-insensitive and '@'-tolerant; historical
        handles from the ledger resolve to the same identity.
        """
        key = normalize_handle(handle)
        if not key:
            return None
        with self._lock:
            kol_uid = self._handle_index.get(key)
            if kol_uid is None:
                return None
            return self._identities.get(kol_uid)

    def all_(self) -> list[KolIdentity]:
        """Current registry state — one identity per ``kol_uid`` (superseded
        rows collapsed to the latest)."""
        with self._lock:
            return list(self._identities.values())

    def to_list(self) -> list[dict[str, Any]]:
        """The full append-only ledger rows (superseded rows included)."""
        with self._lock:
            return [dict(row) for row in self._rows]

    # ── Persistence (write-once, corrupt/missing file tolerated) ───

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("kol_identity ledger unreadable (%s): %s", self._path, str(e)[:120])
            return
        if not isinstance(raw, list):
            logger.warning("kol_identity ledger malformed (not a list): %s", self._path)
            return
        for row in raw:
            if not isinstance(row, dict):
                continue
            self._rows.append(dict(row))
            identity = KolIdentity.from_dict(row)
            if identity is None:
                continue
            # Last row per kol_uid supersedes earlier ones.
            self._identities[identity.kol_uid] = identity
            self._handle_index[identity.current_handle] = identity.kol_uid
            for h in identity.handle_history:
                old = normalize_handle(h.get("old_handle"))
                if old:
                    self._handle_index[old] = identity.kol_uid

    def _persist(self) -> None:
        """Atomically write the full ledger (row history preserved)."""
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._rows, indent=2, default=str) + "\n", encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:  # pragma: no cover - defensive
            logger.warning("kol_identity ledger write failed (%s): %s", self._path, str(e)[:120])


#: Module-level default registry (mirrors kol_scoring registry pattern).
REGISTRY = IdentityRegistry()
