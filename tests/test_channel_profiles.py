"""Channel purpose registry + purpose-based routing (app.core.channel_profiles / routing).

Every assertion here is about behaviour that would post to the WRONG public channel
if it regressed: a whale move on the security feed, a scan card on main, a news
item on the alpha channel. No network, no gopass, no live Telegram.
"""

from __future__ import annotations

from app.core import channel_profiles, channel_routing
from app.core.channels_registry import CHANNEL_ROLES
from app.domains.alerts.publisher import DEFAULT_CHANNELS
from app.domains.alerts.telegram_transport import CHANNEL_BINDINGS

PROFILES = channel_profiles.load_profiles()


class TestRegistry:
    def test_roles_match_the_channel_registry(self) -> None:
        assert (
            set(PROFILES)
            == set(CHANNEL_ROLES)
            == {"news", "alerts", "alpha", "scans", "main", "updates"}
        )

    def test_every_profile_quotes_a_live_description(self) -> None:
        for role, profile in PROFILES.items():
            assert len(profile.telegram_description) > 30, f"{role}: no quoted getChat description"
            assert profile.purpose, f"{role}: no purpose"
            assert profile.cadence and profile.tone and profile.dedupe_scope, (
                f"{role}: incomplete profile"
            )
            assert profile.max_chars > 0
            assert profile.bot_status == "administrator", f"{role}: bot is not an admin"

    def test_kinds_are_inside_the_closed_vocabulary(self) -> None:
        for profile in PROFILES.values():
            assert profile.kinds <= channel_profiles.KINDS, profile.channel
            assert profile.kinds, f"{profile.channel}: no kinds = permanently silent channel"

    def test_specialist_channels_do_not_overlap(self) -> None:
        assert PROFILES["alerts"].kinds == frozenset({"alerts"})
        assert PROFILES["news"].kinds == frozenset({"news"})
        assert PROFILES["scans"].kinds == frozenset({"scans"})
        assert PROFILES["alpha"].kinds == frozenset({"whales", "alpha"})
        assert PROFILES["updates"].kinds == frozenset({"updates"})

    def test_every_tier_routed_channel_has_a_profile_role(self) -> None:
        non_chats = {"site"}  # no Telegram surface, always skipped by the transport
        for logical in set(DEFAULT_CHANNELS) - non_chats:
            assert logical in channel_routing.LOGICAL_PROFILE_CHANNELS, logical
        for role in set(channel_routing.LOGICAL_PROFILE_CHANNELS.values()):
            assert role in PROFILES, role

    def test_every_transport_binding_has_a_profile_role(self) -> None:
        for binding in CHANNEL_BINDINGS:
            assert binding.logical in channel_routing.LOGICAL_PROFILE_CHANNELS, binding.logical


class TestKindMapping:
    def test_sources_map_to_one_kind(self) -> None:
        assert channel_profiles.kind_for_source("scanner.finding") == "scans"
        assert channel_profiles.kind_for_source("rugbrain.exploit") == "alerts"
        assert channel_profiles.kind_for_source("rugbrain.whale") == "whales"
        assert channel_profiles.kind_for_source("telegram_scrape.news") == "news"
        assert channel_profiles.kind_for_source("market.daily_wrap") == "market_recaps"
        assert channel_profiles.kind_for_source("lite_chain_indexer.whale_transfer") == "whales"

    def test_unknown_source_has_no_kind(self) -> None:
        assert channel_profiles.kind_for_source("made.up.producer") is None
        assert channel_profiles.kind_for_source("") is None

    def test_scanner_category_splits_on_severity(self) -> None:
        assert channel_profiles.kind_for_alert("scanner", "high") == "alerts"
        assert channel_profiles.kind_for_alert("scanner", "critical") == "alerts"
        assert channel_profiles.kind_for_alert("scanner", "medium") == "scans"
        assert channel_profiles.kind_for_alert("scanner", "low") == "scans"

    def test_security_categories_are_alerts(self) -> None:
        for category in ("exploit", "rug_pull", "scam_alert", "honeypot", "deployer_sell"):
            assert channel_profiles.kind_for_alert(category, "high") == "alerts", category

    def test_unmapped_category_has_no_kind(self) -> None:
        assert channel_profiles.kind_for_alert("brand_new_thing", "critical") is None


class TestRouteContent:
    def test_kinds_land_on_the_channels_that_want_them(self) -> None:
        assert channel_routing.route_content("scans", PROFILES) == ("main", "scans")
        assert channel_routing.route_content("alerts", PROFILES) == ("alerts", "main")
        assert channel_routing.route_content("whales", PROFILES) == ("alpha", "main")
        assert channel_routing.route_content("news", PROFILES) == ("main", "news")
        assert channel_routing.route_content("market_recaps", PROFILES) == ("main",)
        assert channel_routing.route_content("updates", PROFILES) == ("main", "updates")

    def test_default_deny(self) -> None:
        assert channel_routing.route_content("", PROFILES) == ()
        assert channel_routing.route_content(None, PROFILES) == ()  # type: ignore[arg-type]
        assert channel_routing.route_content("not_a_kind", PROFILES) == ()
        assert channel_routing.route_content("scans", {}) == ()

    def test_kind_is_normalised(self) -> None:
        assert channel_routing.route_content("  SCANS ", PROFILES) == ("main", "scans")

    def test_accepts_raw_yaml_rows(self) -> None:
        raw = {"scans": {"kinds": ["scans"]}, "alerts": {"kinds": ["alerts"]}}
        assert channel_routing.route_content("scans", raw) == ("scans",)


def _plan(*entries: tuple[str, str, str]) -> list[dict[str, object]]:
    """Build a route_alerts plan: one channel with its (category, severity) alerts."""
    return [
        {"channel": chan, "alerts": [{"category": cat, "severity": sev} for cat, sev in alerts]}
        for chan, alerts in entries
    ]


class TestPlanFilter:
    def test_critical_exploit_reaches_security_and_main_only(self) -> None:
        plan = _plan(
            ("rmialerts", [("exploit", "critical")]),
            ("rmicryptonews", [("exploit", "critical")]),
            ("rmialpha", [("exploit", "critical")]),
            ("main_channel", [("exploit", "critical")]),
        )
        kept, denied = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert [entry["channel"] for entry in kept] == ["rmialerts", "main_channel"]
        assert {row["channel"] for row in denied} == {"rmicryptonews", "rmialpha"}
        assert {row["reason"] for row in denied} == {channel_routing.DENY_KIND_NOT_IN_PROFILE}

    def test_high_whale_is_denied_on_the_security_feed(self) -> None:
        plan = _plan(("rmialerts", [("whale", "high")]), ("rmialpha", [("whale", "high")]))
        kept, denied = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert [entry["channel"] for entry in kept] == ["rmialpha"]
        assert denied == [
            {
                "channel": "rmialerts",
                "category": "whale",
                "kind": "whales",
                "reason": "kind_not_in_profile",
            }
        ]

    def test_scanner_high_stays_an_alert_scanner_low_becomes_a_scan(self) -> None:
        plan = _plan(("rmialerts", [("scanner", "high"), ("scanner", "low")]))
        kept, denied = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert kept[0]["alerts"] == [{"category": "scanner", "severity": "high"}]
        assert denied[0]["kind"] == "scans"

    def test_unmapped_category_is_denied_not_guessed(self) -> None:
        plan = _plan(("rmialerts", [("brand_new_thing", "critical")]))
        kept, denied = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert kept == []
        assert denied[0]["reason"] == channel_routing.DENY_KIND_UNMAPPED

    def test_non_chat_surface_passes_through(self) -> None:
        plan = _plan(("site", [("exploit", "critical")]))
        kept, denied = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert kept == plan and denied == []

    def test_filter_never_adds_a_channel(self) -> None:
        plan = _plan(("rmicryptonews", [("news", "low")]))
        kept, _ = channel_routing.filter_plan_by_profile(plan, PROFILES)
        assert {entry["channel"] for entry in kept} <= {"rmicryptonews"}
        assert "rmiscans" not in {entry["channel"] for entry in kept}

    def test_alert_objects_are_supported(self) -> None:
        from app.domains.alerts.models import PlatformAlert

        alert = PlatformAlert(
            title="t", body="b", category="exploit", severity="critical", source="test"
        )
        kept, _ = channel_routing.filter_plan_by_profile(
            [{"channel": "rmialerts", "alerts": [alert]}], PROFILES
        )
        assert kept and kept[0]["alerts"] == [alert]
