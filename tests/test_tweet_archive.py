"""Pure unit tests for app.databus.tweet_archive.

Covers the write-once archive + tombstone semantics (never hard-delete),
the deletion diff -> ``kol.tweet_deleted`` events, the T1
"deleted-after-pump" penalty, and follower-drift identity detection.

All persistence tests use a ``tmp_path`` JSON log — no live registry file,
no network, no DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.databus.tweet_archive import (
    ArchiveTweet,
    TweetArchive,
    deleted_after_pump_penalty,
    detect_deletion,
    detect_identity_drift,
)

_FIXED_POSTED = "2026-08-01T00:00:00+00:00"
_FIXED_DELETED = "2026-08-02T00:00:00+00:00"


def _tweet(
    tweet_id: str = "t1",
    *,
    handle: str = "alice",
    ca: str | None = None,
    text: str = "gm",
    posted_at: str = _FIXED_POSTED,
) -> ArchiveTweet:
    return ArchiveTweet(
        tweet_id=tweet_id,
        author_handle=handle,
        text=text,
        contract_addresses=[ca] if ca else [],
        media_hash="media-hash-1",
        posted_at=posted_at,
        engagement={"likes": 10, "retweets": 2},
        raw_payload={"id": tweet_id, "author_id": "123"},
    )


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# ── ArchiveTweet serialization ─────────────────────────────────────────


def test_archive_tweet_to_dict_roundtrip() -> None:
    tweet = _tweet(ca="0xABC")
    data = tweet.to_dict()
    assert data["tweet_id"] == "t1"
    assert data["author_handle"] == "alice"
    assert data["contract_addresses"] == ["0xABC"]
    assert data["media_hash"] == "media-hash-1"
    assert data["engagement"] == {"likes": 10, "retweets": 2}
    assert data["raw_payload"] == {"id": "t1", "author_id": "123"}
    assert data["deleted_at"] is None
    assert data["detected_by"] is None
    # Dataclass -> dict -> dataclass roundtrip preserves everything.
    rebuilt = ArchiveTweet.from_dict(data).to_dict()
    assert rebuilt == data


# ── Archive / get round-trip + write-once ──────────────────────────────


def test_archive_get_roundtrip(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    assert archive.archive(_tweet()) is True
    got = archive.get("t1")
    assert got is not None
    assert got["tweet_id"] == "t1"
    assert got["author_handle"] == "alice"
    assert got["text"] == "gm"
    assert got["contract_addresses"] == []
    assert got["media_hash"] == "media-hash-1"
    assert got["posted_at"] == _FIXED_POSTED
    assert got["engagement"] == {"likes": 10, "retweets": 2}
    assert got["raw_payload"] == {"id": "t1", "author_id": "123"}
    assert got["deleted_at"] is None
    assert archive.get("nope") is None
    assert archive.to_list() == [got]


def test_archive_accepts_dict_and_is_write_once(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    assert archive.archive(_tweet().to_dict()) is True  # dict input works
    assert archive.archive(_tweet(text="changed")) is False  # write-once
    got = archive.get("t1")
    assert got is not None
    assert got["text"] == "gm"  # original never overwritten
    assert len(archive.to_list()) == 1


def test_archive_rejects_missing_id(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    assert archive.archive({"text": "no id"}) is False


# ── Tombstone: supersedes but never erases ─────────────────────────────


def test_tombstone_supersedes_preserves_original(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet(ca="0xABC"))
    assert archive.is_tombstoned("t1") is False

    assert archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED) is True
    assert archive.is_tombstoned("t1") is True

    got = archive.get("t1")
    assert got is not None
    # Tombstone markers attached (supersede)...
    assert got["deleted_at"] == _FIXED_DELETED
    assert got["detected_by"] == "poll_404"
    # ...but the original snapshot is intact (never erased).
    assert got["text"] == "gm"
    assert got["contract_addresses"] == ["0xABC"]
    assert got["media_hash"] == "media-hash-1"
    assert got["raw_payload"] == {"id": "t1", "author_id": "123"}
    assert len(archive.to_list()) == 1


def test_tombstone_unknown_and_idempotent(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    assert archive.tombstone("ghost", "poll_404") is False  # never archived
    archive.archive(_tweet())
    assert archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED) is True
    # Second tombstone is a no-op — first write wins.
    assert archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED) is False
    got = archive.get("t1")
    assert got is not None
    assert got["detected_by"] == "poll_404"


def test_append_only_log_reloads(tmp_path) -> None:
    path = tmp_path / "tweet_archive.json"
    first = TweetArchive(path)
    first.archive(_tweet(ca="0xABC"))
    first.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED)

    # The file is a newline-delimited JSON log: one archive + one tombstone row.
    lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [ln["record"] for ln in lines] == ["archive", "tombstone"]
    assert lines[0]["tweet"]["tweet_id"] == "t1"

    # A fresh instance replays the log: tombstone supersedes, original intact.
    second = TweetArchive(path)
    assert second.is_tombstoned("t1") is True
    got = second.get("t1")
    assert got is not None
    assert got["deleted_at"] == _FIXED_DELETED
    assert got["detected_by"] == "poll_404"
    assert got["text"] == "gm"
    assert got["contract_addresses"] == ["0xABC"]


# ── Deletion diff -> kol.tweet_deleted events ──────────────────────────


def test_detect_deletion_diff_emits_events(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    archive.archive(_tweet("t2", ca=None, text="no ca"))

    events = detect_deletion(["t1", "t2", "t3"], ["t1"], archive=archive, deleted_at=_FIXED_DELETED)

    assert [e["tweet_id"] for e in events] == ["t2", "t3"]  # sorted, deterministic
    assert all(e["type"] == "kol.tweet_deleted" for e in events)
    assert all(e["deleted_at"] == _FIXED_DELETED for e in events)

    # Archived tweet enriched with author + CA flag.
    assert events[0]["author_handle"] == "alice"
    assert events[0]["had_contract_addresses"] is False
    # Unknown id still yields an event with empty author / no-CA.
    assert events[1]["author_handle"] == ""
    assert events[1]["had_contract_addresses"] is False

    # Live id "t1" is not reported as deleted.
    assert not any(e["tweet_id"] == "t1" for e in events)


def test_detect_deletion_empty_and_no_archive(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    # No live ids -> everything deleted; no archive -> best-effort enrichment.
    events = detect_deletion(["t1"], [], deleted_at=_FIXED_DELETED)
    assert len(events) == 1
    assert events[0]["tweet_id"] == "t1"
    assert events[0]["had_contract_addresses"] is False  # unknown without archive
    # No deletions when live contains all known ids.
    assert detect_deletion(["t1"], ["t1"], deleted_at=_FIXED_DELETED) == []


# ── Deleted-after-pump T1 penalty ──────────────────────────────────────


def test_deleted_after_pump_penalty_fires(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED)

    sig = deleted_after_pump_penalty(archive.get("t1"), dump_map={"0xabc": 0.50})
    assert sig == {"handle": "alice", "tweet_id": "t1", "signal": "deleted_after_pump", "tier": "T1"}

    # Works with a tombstoned ArchiveTweet passed directly too.
    direct = archive.get("t1")
    sig2 = deleted_after_pump_penalty(ArchiveTweet.from_dict(direct), dump_map={"0xabc": 0.50})
    assert sig2 == sig


def test_deleted_after_pump_penalty_none_when_no_dump(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED)
    rec = archive.get("t1")

    # Dump below the 40% threshold -> no signal.
    assert deleted_after_pump_penalty(rec, dump_map={"0xabc": 0.10}) is None
    # Unknown address -> no signal.
    assert deleted_after_pump_penalty(rec, dump_map={"0xdef": 0.90}) is None
    # No dump map at all -> nothing proven -> no signal.
    assert deleted_after_pump_penalty(rec) is None


def test_deleted_after_pump_penalty_none_when_not_tombstoned(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    # Live tweet (not tombstoned) can never be a deletion signal.
    assert deleted_after_pump_penalty(archive.get("t1"), dump_map={"0xabc": 0.90}) is None


def test_deleted_after_pump_penalty_window_and_ordering(tmp_path) -> None:
    # deleted_at = posted + 24h.
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    archive.archive(_tweet("t1", ca="0xABC"))
    archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED)
    rec = archive.get("t1")
    posted = _epoch(_FIXED_POSTED)
    deleted = _epoch(_FIXED_DELETED)

    # Dump 12h after post, before deletion, within 72h window -> fires.
    within = deleted_after_pump_penalty(
        rec,
        dump_map={"0xabc": 0.50},
        dump_times={"0xabc": posted + 12 * 3600},
    )
    assert within is not None
    assert within["tier"] == "T1"

    # Dump *after* the deletion (dump at posted+48h, deleted at posted+24h)
    # is within the 72h window but not "deleted after a pump" -> no signal.
    after_delete = deleted_after_pump_penalty(
        rec,
        dump_map={"0xabc": 0.50},
        dump_times={"0xabc": deleted + 24 * 3600},
    )
    assert after_delete is None

    # Outside the window: tombstone much later (posted+200h) so only the 72h
    # window check can block a dump measured at posted+96h.
    late = TweetArchive(tmp_path / "late.json")
    late.archive(_tweet("t2", ca="0xABC"))
    late.tombstone(
        "t2",
        "poll_404",
        deleted_at=datetime.fromtimestamp(posted + 200 * 3600, tz=UTC).isoformat(),
    )
    outside = deleted_after_pump_penalty(
        late.get("t2"),
        dump_map={"0xabc": 0.50},
        dump_times={"0xabc": posted + 96 * 3600},
    )
    assert outside is None


def test_deleted_after_pump_penalty_any_address_fires(tmp_path) -> None:
    archive = TweetArchive(tmp_path / "tweet_archive.json")
    # A tweet that referenced two tokens; only the Solana one dumped.
    tweet = _tweet("t1", ca="0xABC", text="two cas")
    tweet.contract_addresses = ["0xabc", "So11111111111111111111111111111111111111112"]
    archive.archive(tweet)
    archive.tombstone("t1", "poll_404", deleted_at=_FIXED_DELETED)
    rec = archive.get("t1")
    sig = deleted_after_pump_penalty(rec, dump_map={"So11111111111111111111111111111111111111112": 0.60})
    assert sig is not None
    assert sig["tier"] == "T1"


# ── Identity drift -> kol.identity_changed ─────────────────────────────


def test_identity_drift_fires_on_large_drop() -> None:
    sig = detect_identity_drift("alice", 100_000, 10_000, now=_epoch(_FIXED_DELETED))
    assert sig is not None
    assert sig["type"] == "kol.identity_changed"
    assert sig["handle"] == "alice"
    assert sig["prev_followers"] == 100_000
    assert sig["cur_followers"] == 10_000
    assert sig["drift"] == 0.9
    assert sig["threshold"] == 0.75


def test_identity_drift_threshold_behavior() -> None:
    # 70% drop < 75% -> no signal.
    assert detect_identity_drift("alice", 100_000, 30_000) is None
    # Exactly 75% is NOT > 75% -> no signal (strict threshold).
    assert detect_identity_drift("alice", 100_000, 25_000) is None
    # 76% drop -> fires.
    sig = detect_identity_drift("alice", 100_000, 24_000)
    assert sig is not None
    assert sig["drift"] == 0.76
    # Custom threshold.
    assert detect_identity_drift("alice", 10_000, 9_000, threshold=0.05) is not None
    assert detect_identity_drift("alice", 10_000, 9_000, threshold=0.2) is None


def test_identity_drift_edges() -> None:
    # Follower *gain* is not an account switch.
    assert detect_identity_drift("alice", 1_000, 2_000) is None
    # No baseline -> cannot measure drift.
    assert detect_identity_drift("alice", 0, 10) is None
    # Unparseable counts -> None (never raises).
    assert detect_identity_drift("alice", "n/a", 10) is None
    # Handle is normalized.
    sig = detect_identity_drift("@Alice", 100_000, 10_000)
    assert sig is not None
    assert sig["handle"] == "alice"
