"""Pure tests for the Telegram-channel source registry (app.databus.tg_channel_registry).

No network access. File-backed tests use ``tmp_path`` so the seed registry under
``app/databus`` is never modified.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.databus import tg_channel_registry as tcr
from app.databus.tg_channel_registry import VALID_KINDS

if TYPE_CHECKING:
    from pathlib import Path

# Keys present in every unified_news_sources.json source record.
_UNIFIED_SOURCE_KEYS = {
    "id",
    "name",
    "url",
    "category",
    "type",
    "priority",
    "quality_tier",
    "language",
    "requires_api_key",
    "verified",
    "needs_verification",
}


def _write_registry(tmp_path: Path, entries: list[dict], **top: object) -> Path:
    path = tmp_path / "tg_channels.json"
    payload: dict = {"version": "1.0", "updated": "2026-08-13", "channels": entries}
    payload.update(top)
    path.write_text(json.dumps(payload, indent=2))
    return path


def _news_entry(username: str = "example") -> dict:
    return {
        "username": username,
        "name": f"{username} News",
        "kind": "news",
        "category": "crypto_news",
        "reliable": True,
        "scrape_times_per_day": 8,
        "notes": "fixture",
        "enabled": True,
    }


# ─── seed registry ────────────────────────────────────────────────────────────


def test_seed_file_parses() -> None:
    channels = tcr.load_tg_channels()
    assert len(channels) >= 15
    seen: set[str] = set()
    for entry in channels:
        assert isinstance(entry, dict)
        assert entry["username"].strip()
        assert entry["name"].strip()
        assert entry["kind"] in VALID_KINDS
        assert entry["category"].strip()
        assert isinstance(entry["reliable"], bool)
        assert isinstance(entry["enabled"], bool)
        assert isinstance(entry["scrape_times_per_day"], int)
        assert entry["scrape_times_per_day"] >= 0
        key = tcr.channel_dedup_key(entry)
        assert key not in seen, f"duplicate username {key!r}"
        seen.add(key)


def test_seed_has_news_and_scammer_kinds() -> None:
    channels = tcr.load_tg_channels()
    news = [e for e in channels if e["kind"] == "news"]
    callers = [e for e in channels if e["kind"] == "scammer_caller"]
    assert len(news) >= 10, "starter set should include ~10-15 news channels"
    assert len(callers) >= 5, "starter set should include ~5 scammer-caller channels"


def test_seed_sorted_by_kind_then_username() -> None:
    channels = tcr.load_tg_channels()
    keys = [(e["kind"], tcr.channel_dedup_key(e)) for e in channels]
    assert keys == sorted(keys)


# ─── load validation ──────────────────────────────────────────────────────────


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    good_a = _news_entry("alpha")
    good_b = _news_entry("beta")
    malformed = [
        {"username": "", "name": "Empty", "kind": "news", "category": "crypto_news"},  # blank username
        {"name": "NoUsername", "kind": "news", "category": "crypto_news"},  # missing username
        {"username": "badkind", "name": "Bad Kind", "kind": "meme", "category": "crypto_news"},  # unknown kind
        {
            "username": "badenabled",
            "name": "Bad Enabled",
            "kind": "news",
            "category": "crypto_news",
            "enabled": "yes",
        },  # wrong bool type
        {
            "username": "badscrape",
            "name": "Bad Scrape",
            "kind": "news",
            "category": "crypto_news",
            "scrape_times_per_day": -1,
        },  # negative int
        "not-a-dict",
    ]
    path = _write_registry(tmp_path, [good_a, *malformed, good_b])
    loaded = tcr.load_tg_channels(str(path))
    usernames = [tcr.channel_dedup_key(e) for e in loaded]
    assert usernames == ["alpha", "beta"]


def test_load_tolerates_missing_file(tmp_path: Path) -> None:
    assert tcr.load_tg_channels(str(tmp_path / "nope.json")) == []


def test_load_tolerates_bare_list(tmp_path: Path) -> None:
    path = tmp_path / "tg_channels.json"
    path.write_text(json.dumps([_news_entry("bare")]))
    loaded = tcr.load_tg_channels(str(path))
    assert [e["username"] for e in loaded] == ["bare"]


# ─── filters ──────────────────────────────────────────────────────────────────


def test_channels_by_kind(tmp_path: Path) -> None:
    entries = [
        _news_entry("newschan"),
        {
            "username": "callerchan",
            "name": "Caller",
            "kind": "scammer_caller",
            "category": "scam_alerts",
            "reliable": True,
            "enabled": True,
        },
        {
            "username": "intelchan",
            "name": "Intel",
            "kind": "intel",
            "category": "exploit_alerts",
            "reliable": True,
            "enabled": True,
        },
    ]
    path = _write_registry(tmp_path, entries)
    callers = tcr.channels_by_kind("scammer_caller", str(path))
    assert [e["username"] for e in callers] == ["callerchan"]
    assert tcr.channels_by_kind("scammer_group", str(path)) == []


def test_channels_by_category(tmp_path: Path) -> None:
    entries = [
        {**_news_entry("newschan"), "category": "crypto_news"},
        {**_news_entry("defichan"), "category": "defi"},
        {**_news_entry("otherchan"), "category": "crypto_news"},
    ]
    path = _write_registry(tmp_path, entries)
    crypto_news = tcr.channels_by_category("crypto_news", str(path))
    assert sorted(e["username"] for e in crypto_news) == ["newschan", "otherchan"]


def test_enabled_channels(tmp_path: Path) -> None:
    entries = [
        {**_news_entry("onchan"), "enabled": True},
        {**_news_entry("offchan"), "enabled": False},
        _news_entry("defaultchan"),  # enabled defaults True
    ]
    path = _write_registry(tmp_path, entries)
    enabled = tcr.enabled_channels(str(path))
    assert sorted(e["username"] for e in enabled) == ["defaultchan", "onchan"]


# ─── upsert ───────────────────────────────────────────────────────────────────


def test_upsert_dedupes_by_normalized_username(tmp_path: Path) -> None:
    path = _write_registry(tmp_path, [_news_entry("alpha")])
    first = tcr.upsert_channel({**_news_entry("@Alpha"), "scrape_times_per_day": 24}, str(path))
    assert first["username"] == "@Alpha"
    loaded = tcr.load_tg_channels(str(path))
    assert len(loaded) == 1  # replaced, not duplicated
    assert tcr.channel_dedup_key(loaded[0]) == "alpha"
    assert loaded[0]["scrape_times_per_day"] == 24


def test_upsert_appends_new_channel_and_updates_metadata(tmp_path: Path) -> None:
    path = _write_registry(tmp_path, [_news_entry("alpha")])
    stored = tcr.upsert_channel(_news_entry("beta"), str(path))
    assert stored["username"] == "beta"
    loaded = tcr.load_tg_channels(str(path))
    assert [e["username"] for e in loaded] == ["alpha", "beta"]
    with open(path) as f:
        payload = json.load(f)
    assert payload["total_channels"] == 2
    assert payload["channels"] == loaded
    # custom top-level keys survive an upsert
    with open(path) as f:
        assert "version" in json.load(f)


def test_upsert_creates_file_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "fresh.json"
    stored = tcr.upsert_channel(_news_entry("fresh"), str(path))
    assert stored["username"] == "fresh"
    assert tcr.load_tg_channels(str(path))[0]["username"] == "fresh"


def test_upsert_rejects_malformed_entry(tmp_path: Path) -> None:
    path = _write_registry(tmp_path, [])
    with pytest.raises(ValueError, match="invalid tg_channel entry"):
        tcr.upsert_channel({"name": "no-kind", "kind": "wat", "category": "x"}, str(path))


# ─── dedup key ────────────────────────────────────────────────────────────────


def test_channel_dedup_key_normalization() -> None:
    assert tcr.channel_dedup_key("@Cointelegraph") == "cointelegraph"
    assert tcr.channel_dedup_key("CoinDesk") == "coindesk"
    assert tcr.channel_dedup_key("  @TheBlock__  ") == "theblock__"
    assert tcr.channel_dedup_key({"username": "@Case"}) == "case"
    assert tcr.channel_dedup_key({"username": "MiXeD"}) == "mixed"
    assert tcr.channel_dedup_key("") == ""


# ─── to_news_source ───────────────────────────────────────────────────────────


def test_to_news_source_shape() -> None:
    entry = {
        "username": "@Cointelegraph",
        "name": "CoinTelegraph",
        "kind": "news",
        "category": "crypto_news",
        "reliable": True,
        "scrape_times_per_day": 12,
        "notes": "official",
        "enabled": True,
    }
    mapped = tcr.to_news_source(entry)
    assert set(mapped) == _UNIFIED_SOURCE_KEYS
    assert mapped["id"] == "cointelegraph"
    assert mapped["url"] == "https://t.me/cointelegraph"
    assert mapped["type"] == "telegram"
    assert mapped["category"] == "crypto_news"
    assert mapped["priority"] == 1
    assert mapped["quality_tier"] == "established"
    assert mapped["requires_api_key"] is False
    assert mapped["verified"] is True
    assert mapped["needs_verification"] is False


def test_to_news_source_unreliable_flagged() -> None:
    entry = {**_news_entry("shillchan"), "kind": "scammer_caller", "category": "shill_promotions", "reliable": False}
    mapped = tcr.to_news_source(entry)
    assert mapped["id"] == "shillchan"
    assert mapped["priority"] == 2
    assert mapped["quality_tier"] == "independent"
    assert mapped["verified"] is False
    assert mapped["needs_verification"] is True


def test_to_news_source_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="invalid tg_channel entry"):
        tcr.to_news_source({"username": "", "name": "x", "kind": "news", "category": "crypto_news"})
