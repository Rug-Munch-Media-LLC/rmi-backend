"""Tweet archive + tombstone + delete/rename detection engine.

Implements ``docs/SHILL-TRACKING-METHODOLOGY.md`` §2.2 (tweet archival —
defense against deletion) and §2.3 (delete/rename detection):

- **Write-once archive** — every shill-candidate tweet is snapshotted at
  ingestion (full text, CAs, media hash, timestamps, engagement, raw API
  payload) and persisted append-only to ``app/databus/tweet_archive.json``.
- **Tombstones, never hard-deletes** — a delete event appends a tombstone row
  (``tweet_id``, ``deleted_at``, ``detected_by``) that *supersedes* the record
  (``deleted_at``/``detected_by`` are attached to the in-memory record) but
  never erases the original fields.
- **Delete detection** — a scheduled diff of known vs live tweet ids emits
  ``kol.tweet_deleted`` events. **Deletion after a pump is itself a T1-tiering
  signal** (:func:`deleted_after_pump_penalty`).
- **Identity drift** — a follower-count drop beyond a threshold emits
  ``kol.identity_changed`` (account switched/abandoned).

Pure stdlib (``dataclasses``/``json``/``datetime``), no network, no DB. The
persistence file is a newline-delimited JSON log (``.json`` suffix kept per
spec): each line is either ``{"record": "archive", "tweet": {...}}`` or
``{"record": "tombstone", "tweet_id": ..., "deleted_at": ..., "detected_by": ...}``.
Loading is tolerant of a single JSON array too, so the file stays readable by
plain ``json.load`` tooling when short.

Thresholds are env-tunable (initial guesses per methodology §6.2 — see
``[REVIEW]`` flags).
"""

from __future__ import annotations

import copy
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("tweet_archive")

# ═══════════════════════════════════════════════════════════════════════
# Config (env-driven, tunable without code deploys — mirrors shill_detector)
# ═══════════════════════════════════════════════════════════════════════

#: Dump (fractional price decline) that makes a tombstoned tweet a T1 signal.
#: Methodology §2.3: "deletion after a pump is itself a T1-tiering signal";
#: §4.2 defines a dump as a peak-to-trough decline >= 40% within 72h.
_DUMP_THRESHOLD = float(os.getenv("TWEET_ARCHIVE_DUMP_THRESHOLD", "0.40"))

#: Follower-count relative drop that flags an account switch/abandonment.
#: Methodology §2.3: "account with 100k followers suddenly has 10k =
#: switched/abandoned" — a 90% drop; 75% is the conservative default.
_IDENTITY_DRIFT_THRESHOLD = float(os.getenv("IDENTITY_DRIFT_THRESHOLD", "0.75"))

#: Default append-only persistence path (overridable per instance for tests).
_ARCHIVE_PATH = Path(__file__).resolve().parent / "tweet_archive.json"


# ═══════════════════════════════════════════════════════════════════════
# Small helpers (mirror shill_detector._iso / _to_ts)
# ═══════════════════════════════════════════════════════════════════════


def _to_ts(value: Any) -> float:
    """Parse a timestamp into epoch seconds; 0.0 when unparseable."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value > 1e11:  # unix milliseconds
            return value / 1000.0
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() and len(s) >= 9:
            try:
                return float(s)
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
    logger.debug("Unparseable timestamp %r, using 0", value)
    return 0.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _normalize_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").lower()


def _normalize_address(addr: Any) -> str:
    """Normalize an address for matching (EVM case-insensitive, base58 exact)."""
    s = str(addr or "").strip()
    return s.lower() if s.startswith("0x") else s


# ═══════════════════════════════════════════════════════════════════════
# ArchiveTweet — the write-once snapshot
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ArchiveTweet:
    """Immutable snapshot of a shill-candidate tweet.

    ``deleted_at`` / ``detected_by`` are optional *tombstone markers* attached
    by :meth:`TweetArchive.tombstone` — they supersede the record for
    presentation (``get``/``to_list``/``is_tombstoned``) but the original
    fields are never erased or mutated.
    """

    tweet_id: str
    author_handle: str = ""
    text: str = ""
    contract_addresses: list[str] = field(default_factory=list)
    media_hash: str = ""
    posted_at: str = ""
    engagement: dict[str, Any] = field(default_factory=dict)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    # Tombstone markers (set by TweetArchive.tombstone, never by archive()).
    deleted_at: str | None = None
    detected_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tweet_id": self.tweet_id,
            "author_handle": self.author_handle,
            "text": self.text,
            "contract_addresses": list(self.contract_addresses),
            "media_hash": self.media_hash,
            "posted_at": self.posted_at,
            "engagement": dict(self.engagement),
            "raw_payload": dict(self.raw_payload),
            "deleted_at": self.deleted_at,
            "detected_by": self.detected_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveTweet:
        return cls(
            tweet_id=str(data.get("tweet_id") or ""),
            author_handle=str(data.get("author_handle") or ""),
            text=str(data.get("text") or ""),
            contract_addresses=list(data.get("contract_addresses") or []),
            media_hash=str(data.get("media_hash") or ""),
            posted_at=str(data.get("posted_at") or ""),
            engagement=dict(data.get("engagement") or {}),
            raw_payload=dict(data.get("raw_payload") or {}),
            deleted_at=data.get("deleted_at"),
            detected_by=data.get("detected_by"),
        )


# ═══════════════════════════════════════════════════════════════════════
# TweetArchive — append-only JSON persistence
# ═══════════════════════════════════════════════════════════════════════


class TweetArchive:
    """Write-once archive with supersede-on-tombstone semantics.

    Every mutation appends a newline-delimited JSON record to the persistence
    file — archive rows and tombstone rows are only ever *added*, never
    updated or removed. The in-memory index merges tombstone markers onto the
    original record so ``get()``/``to_list()`` return the superseded view
    while the original fields stay intact.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else _ARCHIVE_PATH
        self._lock = threading.Lock()
        #: tweet_id -> merged record dict (original fields + tombstone markers).
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    # ── persistence ─────────────────────────────────────────────────────

    def _append_line(self, entry: dict[str, Any]) -> None:
        """Append one JSON record to the log (caller holds the lock)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()

    def _load(self) -> None:
        """Replay the append-only log. Tolerates a single JSON value too."""
        if not self._path.is_file():
            return
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            records = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        else:
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict) and isinstance(data.get("records"), list):
                records = data["records"]
            else:
                records = [data]
        for entry in records:
            if not isinstance(entry, dict):
                continue
            if entry.get("record") == "tombstone":
                self._apply_tombstone(
                    str(entry.get("tweet_id") or ""),
                    str(entry.get("deleted_at") or ""),
                    str(entry.get("detected_by") or ""),
                )
            elif entry.get("record") == "archive" and isinstance(entry.get("tweet"), dict):
                self._apply_archive(entry["tweet"])
            elif entry.get("tweet_id"):  # bare archive record
                self._apply_archive(entry)

    def _apply_archive(self, record: dict[str, Any]) -> None:
        tweet_id = str(record.get("tweet_id") or "").strip()
        if not tweet_id or tweet_id in self._records:
            return  # write-once: first archive wins
        # Archive rows never carry tombstone markers — normalize them away so a
        # replayed archive row cannot look pre-deleted, then pin the keys to
        # None so callers can read `deleted_at`/`detected_by` unconditionally.
        clean = dict(record)
        clean.pop("deleted_at", None)
        clean.pop("detected_by", None)
        clean["deleted_at"] = None
        clean["detected_by"] = None
        self._records[tweet_id] = clean

    def _apply_tombstone(self, tweet_id: str, deleted_at: str, detected_by: str) -> None:
        if not tweet_id or tweet_id not in self._records:
            return
        if self._records[tweet_id].get("deleted_at"):
            return  # already tombstoned
        self._records[tweet_id]["deleted_at"] = deleted_at or _iso(datetime.now(UTC).timestamp())
        self._records[tweet_id]["detected_by"] = detected_by

    # ── public API ──────────────────────────────────────────────────────

    def archive(self, tweet: ArchiveTweet | dict[str, Any]) -> bool:
        """Snapshot a tweet (write-once).

        Accepts an :class:`ArchiveTweet` or a dict shaped like
        :meth:`ArchiveTweet.to_dict`. Returns ``False`` when the tweet id is
        already archived or the payload has no id — the original record is
        never overwritten.
        """
        record = tweet.to_dict() if isinstance(tweet, ArchiveTweet) else dict(tweet)
        tweet_id = str(record.get("tweet_id") or "").strip()
        if not tweet_id:
            logger.debug("archive skipped: no tweet_id")
            return False
        with self._lock:
            if tweet_id in self._records:
                logger.debug("archive skipped: tweet %s already archived (write-once)", tweet_id)
                return False
            self._apply_archive(record)
            self._append_line({"record": "archive", "tweet": self._records[tweet_id]})
            return True

    def get(self, tweet_id: str) -> dict[str, Any] | None:
        """Return the archived record (superseded by tombstone markers when
        deleted) or ``None`` when unknown. Never erases the original fields."""
        with self._lock:
            record = self._records.get(str(tweet_id))
            return copy.deepcopy(record) if record is not None else None

    def tombstone(self, tweet_id: str, detected_by: str, *, deleted_at: float | str | None = None) -> bool:
        """Mark an archived tweet as deleted. Appends a tombstone row and
        attaches ``deleted_at``/``detected_by`` to the record — the original
        snapshot is preserved. Returns ``False`` for unknown ids or when the
        tweet is already tombstoned."""
        tweet_id = str(tweet_id).strip()
        if not tweet_id:
            return False
        deleted_ts = _to_ts(deleted_at if deleted_at is not None else datetime.now(UTC).timestamp())
        deleted_iso = _iso(deleted_ts)
        detected = str(detected_by or "")
        with self._lock:
            if tweet_id not in self._records or self._records[tweet_id].get("deleted_at"):
                return False
            self._apply_tombstone(tweet_id, deleted_iso, detected)
            self._append_line(
                {
                    "record": "tombstone",
                    "tweet_id": tweet_id,
                    "deleted_at": deleted_iso,
                    "detected_by": detected,
                }
            )
            return True

    def is_tombstoned(self, tweet_id: str) -> bool:
        """True when the archived tweet has been marked deleted."""
        with self._lock:
            record = self._records.get(str(tweet_id))
            return bool(record and record.get("deleted_at"))

    def to_list(self) -> list[dict[str, Any]]:
        """All archived records in archive order (with tombstone markers)."""
        with self._lock:
            return [copy.deepcopy(r) for r in self._records.values()]


# ═══════════════════════════════════════════════════════════════════════
# Delete / rename detection (pure functions, methodology §2.3)
# ═══════════════════════════════════════════════════════════════════════


def detect_deletion(
    known_tweet_ids: Any,
    live_tweet_ids: Any,
    *,
    archive: TweetArchive | None = None,
    deleted_at: float | str | None = None,
) -> list[dict[str, Any]]:
    """Diff known vs live tweet ids → ``kol.tweet_deleted`` events.

    Every id present in ``known_tweet_ids`` but missing from
    ``live_tweet_ids`` produces an event. When an ``archive`` is supplied the
    event is enriched with the archived ``author_handle`` and whether the
    tweet referenced contract addresses (both default to empty/``False`` for
    ids not in the archive — the pure diff still works without it).

    Args:
        known_tweet_ids: iterable of tracked tweet ids (from the archive).
        live_tweet_ids: iterable of ids that still exist on the platform.
        archive: optional :class:`TweetArchive` used to enrich events.
        deleted_at: optional detection timestamp (epoch or ISO) for
            deterministic tests; defaults to now.
    """
    known = {str(i) for i in (known_tweet_ids or []) if str(i).strip()}
    live = {str(i) for i in (live_tweet_ids or []) if str(i).strip()}
    deleted_iso = _iso(_to_ts(deleted_at if deleted_at is not None else datetime.now(UTC).timestamp()))

    events: list[dict[str, Any]] = []
    for tweet_id in sorted(known - live):
        record = archive.get(tweet_id) if archive is not None else None
        events.append(
            {
                "type": "kol.tweet_deleted",
                "tweet_id": tweet_id,
                "author_handle": str((record or {}).get("author_handle") or ""),
                "deleted_at": deleted_iso,
                "had_contract_addresses": bool((record or {}).get("contract_addresses")),
            }
        )
    return events


def deleted_after_pump_penalty(
    tweet: ArchiveTweet | dict[str, Any],
    dump_window_hours: float = 72,
    *,
    dump_map: dict[str, float] | None = None,
    dump_times: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """T1-tiering signal: a tombstoned tweet whose token dumped >= 40%.

    Methodology §2.3 — "deletion after a pump is itself a T1-tiering signal".
    ``tweet`` may be an :class:`ArchiveTweet` (with tombstone markers) or the
    dict returned by :meth:`TweetArchive.get` / :meth:`TweetArchive.to_list`.

    Args:
        tweet: the archived (ideally tombstoned) tweet.
        dump_window_hours: window from ``posted_at`` in which a dump counts
            (methodology §4.2: peak-to-trough within 72h).
        dump_map: normalized contract address -> fractional decline (0.40 =
            40%). Missing addresses are ignored; without this map no dump can
            be proven, so nothing fires.
        dump_times: optional address -> epoch seconds when the dump was
            measured. When supplied the dump must land within
            ``dump_window_hours`` of ``posted_at`` and *before* the deletion
            (deleted-after-pump, not deleted-before-the-dump).

    Returns ``None`` unless a tombstoned tweet with contract addresses has a
    proven >= 40% dump, then
    ``{"handle", "tweet_id", "signal": "deleted_after_pump", "tier": "T1"}``.
    """
    record = tweet.to_dict() if isinstance(tweet, ArchiveTweet) else dict(tweet or {})
    if not record.get("deleted_at"):  # not tombstoned — no retraction signal
        return None
    addresses = [_normalize_address(a) for a in (record.get("contract_addresses") or []) if _normalize_address(a)]
    if not addresses:
        return None

    posted_ts = _to_ts(record.get("posted_at"))
    deleted_ts = _to_ts(record.get("deleted_at"))
    window_h = max(0.0, float(dump_window_hours))
    dump_map = dump_map or {}
    dump_times = dump_times or {}

    for addr in addresses:
        raw_dump = dump_map.get(addr)
        if raw_dump is None:
            continue
        try:
            dump_pct = float(raw_dump)
        except (TypeError, ValueError):
            continue
        if dump_pct < _DUMP_THRESHOLD:
            continue
        if addr in dump_times:
            dump_ts = _to_ts(dump_times[addr])
            if dump_ts > 0:
                if posted_ts > 0:
                    delta_h = (dump_ts - posted_ts) / 3600.0
                    if delta_h < 0 or delta_h > window_h:
                        continue
                if deleted_ts > 0 and dump_ts > deleted_ts:  # pump must precede deletion
                    continue
        return {
            "handle": str(record.get("author_handle") or ""),
            "tweet_id": str(record.get("tweet_id") or ""),
            "signal": "deleted_after_pump",
            "tier": "T1",
        }
    return None


def detect_identity_drift(
    handle: str,
    prev_followers: float,
    cur_followers: float,
    *,
    threshold: float = _IDENTITY_DRIFT_THRESHOLD,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Follower-count drift > ``threshold`` → ``kol.identity_changed``.

    Methodology §2.3: an account with 100k followers suddenly at 10k has
    switched/abandoned. Drift is the *relative decline* ``(prev - cur) /
    prev``; only declines beyond the threshold fire (a follower gain is not an
    account switch). ``prev <= 0`` cannot be measured and never fires.

    Returns ``None`` or
    ``{"type": "kol.identity_changed", "handle", "prev_followers",
    "cur_followers", "drift", "threshold", "detected_at"}``.
    """
    try:
        prev = float(prev_followers or 0.0)
        cur = float(cur_followers or 0.0)
    except (TypeError, ValueError):
        return None
    if prev <= 0:
        return None
    drift = (prev - cur) / prev
    if drift <= float(threshold):
        return None
    return {
        "type": "kol.identity_changed",
        "handle": _normalize_handle(handle),
        "prev_followers": prev,
        "cur_followers": cur,
        "drift": round(drift, 4),
        "threshold": float(threshold),
        "detected_at": _iso(now if now is not None else datetime.now(UTC).timestamp()),
    }
