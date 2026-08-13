"""Dry tests for account-switch / alias tracking (app.databus.account_alias).

Driven by the live ``account_aliases.json`` registry (maintained by the
fleet's evidence-based account-switch sweeps): when the registry has no
alias entries, the alias-resolution assertions are skipped rather than
failing on regenerated evidence.
"""

from __future__ import annotations

from app.databus import account_alias as aa, social_intel


def _reset() -> None:
    aa.clear_deleted_posts()
    social_intel.KOL_DATABASE.clear()
    social_intel.DETECTED_CAMPAIGNS.clear()


def _first_alias_pair() -> tuple[str, str] | None:
    """First (canonical, alias) pair present in the live registry."""
    for entry in aa.load_aliases():
        for alias in entry.get("aliases", []) or []:
            if str(alias).strip():
                return str(entry["canonical"]), str(alias)
    return None


def test_canonical_handle_resolves_alias() -> None:
    pair = _first_alias_pair()
    if pair is None:
        return  # no alias entries in registry — nothing to resolve
    canonical, alias = pair
    assert aa.canonical_handle(alias) == aa.canonical_handle(canonical)
    assert aa.canonical_handle(alias).lstrip("@").lower() == canonical.lower()
    # Canonical handle resolves to itself.
    assert aa.canonical_handle(canonical) == canonical.lower()
    # Unknown handles pass through untouched.
    assert aa.canonical_handle("VitalikButerin") == "vitalikbuterin"
    assert aa.canonical_handle("") == ""


def test_resolve_account_preserves_alias() -> None:
    pair = _first_alias_pair()
    if pair is None:
        return
    canonical, alias = pair
    acc = {"handle": alias, "name": alias, "category": "unknown"}
    resolved = aa.resolve_account(acc)
    assert resolved["handle"].lower() == canonical.lower()
    assert resolved["alias_of"] == alias
    # Original dict is not mutated.
    assert acc["handle"] == alias


def test_merge_alias_activity_keys_canonical() -> None:
    pair = _first_alias_pair()
    if pair is None:
        return
    canonical, alias = pair
    records = [
        {"handle": alias, "token": "SOL", "type": "buy"},
        {"handle": canonical, "token": "BTC", "type": "buy"},
    ]
    merged = aa.merge_alias_activity(records)
    assert len(merged) == 2
    assert {r["canonical_handle"] for r in merged} == {canonical.lower()}
    assert merged[0]["handle"].lower() == canonical.lower()
    assert merged[0]["alias_handle"] == alias


def test_merge_kol_registry_collapses_alias_keys() -> None:
    pair = _first_alias_pair()
    if pair is None:
        return
    canonical, alias = pair
    registry = {
        f"kol:{alias}": {
            "calls": [{"token": "SOL", "type": "buy"}],
            "metrics": {"total_calls": 1, "tokens_mentioned": {"SOL"}},
        },
        f"kol:{canonical}": {
            "calls": [{"token": "BTC", "type": "buy"}],
            "metrics": {"total_calls": 1, "tokens_mentioned": {"BTC"}},
        },
    }
    merged = aa.merge_kol_registry(registry)
    assert set(merged) == {f"kol:{canonical.lower()}"}
    assert len(merged[f"kol:{canonical.lower()}"]["calls"]) == 2
    assert merged[f"kol:{canonical.lower()}"]["metrics"]["total_calls"] == 2
    assert merged[f"kol:{canonical.lower()}"]["metrics"]["tokens_mentioned"] == {"SOL", "BTC"}


def test_deleted_post_registry_keyed_by_canonical_kol_token() -> None:
    _reset()
    pair = _first_alias_pair()
    if pair is None:
        return
    canonical, alias = pair
    rec = aa.record_deleted_post(alias, "SOL", call_type="buy")
    assert rec["handle"] == canonical.lower()
    assert rec["token"] == "SOL"
    aa.record_deleted_post(canonical, "BTC", call_type="buy")
    # Counts across alias + canonical land on the same KOL record.
    assert aa.deleted_post_count(canonical) == 2
    assert aa.deleted_post_count(alias) == 2
    assert aa.deleted_post_count(token="SOL") == 1
    assert aa.deleted_post_flag(alias, "SOL") is True
    assert aa.deleted_post_flag(canonical, "ETH") is False
    _reset()


def test_track_deleted_call_tags_registry_and_flags_scoring() -> None:
    _reset()
    import asyncio

    from app.databus import kol_scoring as ks

    pair = _first_alias_pair()
    if pair is None:
        return
    canonical, alias = pair
    ks.clear_registry()

    async def _run() -> None:
        await social_intel.track_kol_call(alias, "SOL", call_type="buy")
        await social_intel.track_kol_call(canonical, "SOL", call_type="buy")
        # Same canonical KOL -> merged under one key.
        assert set(social_intel.KOL_DATABASE) == {f"kol:{canonical.lower()}"}

        await social_intel.track_deleted_call(alias, "SOL", call_type="buy")
        profile = await social_intel.get_kol_profile(alias)
        assert profile["handle"] == canonical.lower()
        assert profile["metrics"]["deleted_calls"] == 1

        alerts = await social_intel.detect_shill_campaigns()
        deleted_campaigns = [c for c in alerts["active_campaigns"] if c["type"] == "deleted_posts"]
        assert deleted_campaigns, "deleted-posts shill signal not emitted"
        assert deleted_campaigns[0]["token"] == "SOL"

        ks.register_deleted_alert(canonical, 1)
        score = ks.score_kol(canonical, persist=False)
        assert score["deleted_alert_count"] >= 1
        assert score["components"]["deleted_post_penalty"] < 0

    asyncio.run(_run())
    _reset()
    ks.clear_registry()
