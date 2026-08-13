"""Pure unit tests for the KOL identity registry (app.databus.kol_identity).

No DB, no network, no external fixtures — persistence is exercised against
``tmp_path`` files only. Mirrors docs/SHILL-TRACKING-METHODOLOGY.md §2.1.
"""

from __future__ import annotations

import json
import re

from app.databus import kol_identity as ki

# ── mint_kol_uid ───────────────────────────────────────────────────


def test_mint_kol_uid_deterministic_and_distinct() -> None:
    a = ki.mint_kol_uid("VitalikButerin")
    b = ki.mint_kol_uid("VitalikButerin")
    c = ki.mint_kol_uid("VitalikButerin2")
    assert a == b
    assert a != c
    # 64 lowercase hex chars (sha256 digest).
    assert re.fullmatch(r"[0-9a-f]{64}", a)


def test_mint_kol_uid_normalizes_handle() -> None:
    assert ki.mint_kol_uid("@VitalikButerin") == ki.mint_kol_uid("vitalikbuterin")
    assert ki.mint_kol_uid("  vitalikbuterin  ") == ki.mint_kol_uid("VitalikButerin")


# ── profile_hash ───────────────────────────────────────────────────


def test_profile_hash_stable() -> None:
    assert ki.profile_hash("Vitalik Buterin", "https://x/avatar.png", "Ethereum") == ki.profile_hash(
        "Vitalik Buterin", "https://x/avatar.png", "Ethereum"
    )


def test_profile_hash_changes_with_fields() -> None:
    base = ("Vitalik Buterin", "https://x/avatar.png", "Ethereum")
    assert ki.profile_hash(*base) != ki.profile_hash("Vitalik B", base[1], base[2])
    assert ki.profile_hash(*base) != ki.profile_hash(base[0], "https://x/other.png", base[2])
    assert ki.profile_hash(*base) != ki.profile_hash(base[0], base[1], "Ethereum creator")


def test_profile_hash_treats_none_as_empty() -> None:
    assert ki.profile_hash("N", "A", None) == ki.profile_hash("N", "A", "")


# ── detect_rename ──────────────────────────────────────────────────


def _snap(handle: str, followers: int) -> dict:
    return {"handle": handle, "followers": followers}


def test_detect_rename_handle_change() -> None:
    event = ki.detect_rename(_snap("old_handle", 100_000), _snap("new_handle", 100_000))
    assert event is not None
    assert event["type"] == "kol.identity_changed"
    assert event["old_handle"] == "old_handle"
    assert event["new_handle"] == "new_handle"
    assert event["reason"] == "handle_changed"
    assert event["kol_uid"] == ki.mint_kol_uid("old_handle")
    assert event["detected_at"]


def test_detect_rename_follower_drift() -> None:
    # 100k -> 10k = 90% drift (> 75%) => account-switch signal.
    event = ki.detect_rename(_snap("same_handle", 100_000), _snap("same_handle", 10_000))
    assert event is not None
    assert event["type"] == "kol.identity_changed"
    assert event["old_handle"] == event["new_handle"] == "same_handle"
    assert event["reason"] == "follower_drift"
    assert event["kol_uid"] == ki.mint_kol_uid("same_handle")


def test_detect_rename_both_signals() -> None:
    event = ki.detect_rename(_snap("old_handle", 100_000), _snap("new_handle", 5_000))
    assert event is not None
    assert event["reason"] == "handle_changed_and_follower_drift"


def test_detect_rename_no_change() -> None:
    assert ki.detect_rename(_snap("stable", 50_000), _snap("stable", 50_000)) is None
    # Small drift (30%) is not an account switch.
    assert ki.detect_rename(_snap("stable", 100_000), _snap("stable", 70_000)) is None
    # Follower growth is never a switch signal.
    assert ki.detect_rename(_snap("growing", 10_000), _snap("growing", 200_000)) is None


# ── Registry: get/set round-trip ───────────────────────────────────


def test_registry_record_get_round_trip(tmp_path) -> None:
    reg = ki.IdentityRegistry(path=tmp_path / "ledger.json")
    identity = ki.KolIdentity(
        kol_uid=ki.mint_kol_uid("VitalikButerin"),
        current_handle="VitalikButerin",
        display_name="Vitalik Buterin",
        avatar_hash=ki.profile_hash("Vitalik Buterin", "https://x/a.png", "bio"),
        bio_hash=ki.profile_hash("", "", "bio"),
        follower_snapshot=100_000,
    )
    reg.record(identity)

    got = reg.get(identity.kol_uid)
    assert got is not None
    assert got.current_handle == "vitalikbuterin"
    assert got.display_name == "Vitalik Buterin"
    assert got.follower_snapshot == 100_000
    assert got.first_seen_at
    assert got.last_updated_at

    assert reg.get("missing-uid") is None


def test_registry_get_by_handle_case_insensitive(tmp_path) -> None:
    reg = ki.IdentityRegistry(path=tmp_path / "ledger.json")
    identity = ki.KolIdentity(kol_uid=ki.mint_kol_uid("Alice"), current_handle="Alice")
    reg.record(identity)

    assert reg.get_by_handle("alice") is not None
    assert reg.get_by_handle("@Alice") is not None
    assert reg.get_by_handle("ALICE") is not None
    assert reg.get_by_handle("nobody") is None
    assert reg.get_by_handle("") is None


def test_registry_renamed_identity_supersedes_and_ledgers(tmp_path) -> None:
    reg = ki.IdentityRegistry(path=tmp_path / "ledger.json")
    uid = ki.mint_kol_uid("old_handle")
    reg.record(ki.KolIdentity(kol_uid=uid, current_handle="old_handle", first_seen_at="2026-01-01T00:00:00+00:00"))
    reg.record(ki.KolIdentity(kol_uid=uid, current_handle="new_handle"))

    current = reg.get(uid)
    assert current is not None
    assert current.current_handle == "new_handle"
    # Mint time is immutable across the rename.
    assert current.first_seen_at == "2026-01-01T00:00:00+00:00"
    # The ledger records (old_handle -> new_handle).
    assert len(current.handle_history) == 1
    entry = current.handle_history[0]
    assert entry["old_handle"] == "old_handle"
    assert entry["new_handle"] == "new_handle"
    assert entry["detected_at"]

    # Old handle stays searchable via the ledger.
    assert reg.get_by_handle("old_handle") is current

    # Write-once: both rows persist; superseded row is never hard-deleted.
    rows = reg.to_list()
    assert len(rows) == 2
    assert {r["current_handle"] for r in rows} == {"old_handle", "new_handle"}

    # Current state collapses to the latest row per kol_uid.
    assert len(reg.all_()) == 1


def test_registry_persists_and_reloads(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    reg = ki.IdentityRegistry(path=path)
    uid = ki.mint_kol_uid("Alice")
    reg.record(ki.KolIdentity(kol_uid=uid, current_handle="Alice", follower_snapshot=42))
    reg.record(ki.KolIdentity(kol_uid=uid, current_handle="Alicia"))

    reloaded = ki.IdentityRegistry(path=path)
    got = reloaded.get(uid)
    assert got is not None
    assert got.current_handle == "alicia"
    # The superseding row carries the *current* profile state; the original
    # snapshot remains intact in the append-only ledger.
    assert got.follower_snapshot == 0
    rows = reloaded.to_list()
    assert len(rows) == 2  # append-only history survives reload
    assert any(r["follower_snapshot"] == 42 and r["current_handle"] == "alice" for r in rows)
    assert reloaded.get_by_handle("alice") is got  # history handle resolves after reload


def test_registry_tolerates_missing_and_corrupt_ledger(tmp_path) -> None:
    reg = ki.IdentityRegistry(path=tmp_path / "does-not-exist.json")
    assert reg.all_() == []
    assert reg.to_list() == []

    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json!!!", encoding="utf-8")
    reg2 = ki.IdentityRegistry(path=bad)
    assert reg2.all_() == []

    not_a_list = tmp_path / "notalist.json"
    not_a_list.write_text(json.dumps({"accounts": []}), encoding="utf-8")
    reg3 = ki.IdentityRegistry(path=not_a_list)
    assert reg3.all_() == []


def test_registry_mints_uid_when_missing(tmp_path) -> None:
    reg = ki.IdentityRegistry(path=tmp_path / "ledger.json")
    identity = ki.KolIdentity(current_handle="Bob")  # no kol_uid supplied
    stored = reg.record(identity)
    assert stored.kol_uid == ki.mint_kol_uid("Bob")
    assert reg.get(stored.kol_uid) is not None
