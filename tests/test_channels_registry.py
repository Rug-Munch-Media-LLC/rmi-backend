"""Unit tests for the canonical channel registry (app.core.channels_registry).

Covers: role inventory completeness, gopass → env → fallback resolution
order, legacy env aliases, and value-free inventory rows. No live gopass —
the gopass reader is monkeypatched so tests are deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core import channels_registry as registry

if TYPE_CHECKING:
    import pytest


class TestInventory:
    def test_all_expected_roles_present(self) -> None:
        assert set(registry.CHANNEL_ROLES) == {"news", "alerts", "alpha", "scans", "main", "updates"}

    def test_store_paths_match_gopass_store_names(self) -> None:
        # Names only — the store entries themselves are verified at deploy time
        # with `gopass ls --flat` (see docs/CHANNELS.md).
        expected_paths = {
            "news": "rmi/telegram/CHANNEL_RMI_NEWS",
            "alerts": "rmi/telegram/CHANNEL_CRYPTO_ALERTS",
            "alpha": "rmi/telegram/CHANNEL_CRYPTO_ALPHA",
            "scans": "rmi/telegram/CHANNEL_COMMUNITY_SCANS",
            "main": "rmi/telegram/CHANNEL_CRYPTO_RUGMUNCHER",
            "updates": "rmi/telegram/CHANNEL_RMI_UPDATES",
        }
        assert {role: definition.store_path for role, definition in registry.CHANNEL_ROLES.items()} == expected_paths

    def test_news_env_alias_maps_to_canonical(self) -> None:
        assert registry.CHANNEL_ROLES["news"].env_var == "RMI_NEWS_CHANNEL_ID"
        assert registry.CHANNEL_ROLES["news"].legacy_env_vars == ("RMI_CRYPTONEWS_CHANNEL_ID",)

    def test_updates_registered_without_fallback(self) -> None:
        updates = registry.CHANNEL_ROLES["updates"]
        assert updates.store_path == "rmi/telegram/CHANNEL_RMI_UPDATES"
        assert updates.env_var == "RMI_UPDATES_CHANNEL_ID"
        assert updates.fallback is None

    def test_inventory_rows_are_value_free(self) -> None:
        for row in registry.channel_inventory():
            assert row.store_path.startswith("rmi/telegram/CHANNEL_")
            assert row.env_var.endswith("_CHANNEL_ID")


class TestResolveChannelId:
    def test_env_wins_over_fallback(self) -> None:
        resolved = registry.resolve_channel_id("news", env={"RMI_NEWS_CHANNEL_ID": "-1001"}, use_gopass=False)
        assert resolved == "-1001"

    def test_legacy_env_alias_used_when_canonical_unset(self) -> None:
        resolved = registry.resolve_channel_id("news", env={"RMI_CRYPTONEWS_CHANNEL_ID": "-1002"}, use_gopass=False)
        assert resolved == "-1002"

    def test_canonical_env_wins_over_legacy_alias(self) -> None:
        resolved = registry.resolve_channel_id(
            "news",
            env={"RMI_NEWS_CHANNEL_ID": "-1001", "RMI_CRYPTONEWS_CHANNEL_ID": "-1002"},
            use_gopass=False,
        )
        assert resolved == "-1001"

    def test_fallback_used_when_env_unset(self) -> None:
        resolved = registry.resolve_channel_id("alpha", env={}, use_gopass=False)
        assert resolved == registry.CHANNEL_ROLES["alpha"].fallback

    def test_no_fallback_when_include_fallback_false(self) -> None:
        resolved = registry.resolve_channel_id("alpha", env={}, use_gopass=False, include_fallback=False)
        assert resolved is None

    def test_gopass_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry, "_gopass_read", lambda path: "-100GOPASS")
        resolved = registry.resolve_channel_id("alerts", env={"RMI_ALERTS_CHANNEL_ID": "-100ENV"})
        assert resolved == "-100GOPASS"

    def test_unknown_role_returns_none(self) -> None:
        assert registry.resolve_channel_id("bogus", env={}, use_gopass=False) is None

    def test_updates_resolves_env_only(self) -> None:
        resolved = registry.resolve_channel_id("updates", env={"RMI_UPDATES_CHANNEL_ID": "-100U"}, use_gopass=False)
        assert resolved == "-100U"
        assert registry.resolve_channel_id("updates", env={}, use_gopass=False) is None
