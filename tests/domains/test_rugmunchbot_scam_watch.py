"""Unit tests for scam/shill channel ingestion (channels/scam_watch.py).

Covers:
  * source taxonomy (ScamSourceType + dialog keyword heuristics)
  * env config parsing (RMI_SCAM_CHANNELS / exclusions)
  * registry source selection (scammer_caller / shill_promotions; watchers off
    by default)
  * dialog auto-discovery (fake get_dialogs client, caps, fail-open)
  * sweep: read → normalize (category=scam_shill) → time-window → dedupe
  * address/ticker extraction (reuses canonical EVM/Solana regexes)
  * submit-for-scan (fail-open, bounded) + durable scanner queue + seen-dedupe
  * the scheduled job (no chat id required, returns counts, never raises)
  * JobQueue registration (interval + disabled skip)

No live network I/O — the reader, dialogs, Redis and the scanner service are
all faked/patched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.domains.telegram.rugmunchbot.channels import (
    scam_watch,
    scheduler as channels_scheduler,
)

# Dynamic NOW so the sweep time-window (default 6h lookback, computed against
# the real clock in scam_watch.sweep_scam_channels) always includes "fresh"
# messages and excludes the explicit `old` fixtures. A hardcoded date rots the
# moment real time passes the lookback window.
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    scam_watch._SEEN_CACHE.clear()
    channels_scheduler._posted_keys.clear()
    yield
    scam_watch._SEEN_CACHE.clear()
    channels_scheduler._posted_keys.clear()


def _msg(
    text: str,
    *,
    username: str = "pumpkingroup",
    message_id: int = 1,
    posted_at: datetime | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        channel=username.title(),
        channel_username=username,
        message_id=message_id,
        text=text,
        posted_at=posted_at or NOW,
    )


def _article(
    title: str,
    *,
    username: str = "pumpkingroup",
    message_id: int = 1,
    entities: list[str] | None = None,
) -> NewsArticle:
    return NewsArticle(
        title=title,
        body=title,
        source_channel=username.title(),
        source_url=f"https://t.me/{username}/{message_id}",
        category="scam_shill",
        posted_at=NOW,
        content_hash="x",
        entities=entities or ["$RUG"],
    )


class FakeReader:
    """news_ingest-style fake: ``read_messages(username, limit)``."""

    def __init__(self, messages_by_channel: dict[str, list[ChannelMessage]]) -> None:
        self.messages_by_channel = messages_by_channel

    async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
        return self.messages_by_channel.get(channel_username, [])[:limit]


class FakeDialogClient:
    """Followed-dialogs fake matching discovery.py's duck-typed surface."""

    def __init__(self, dialogs: list[tuple[str, str]]) -> None:
        self._dialogs = dialogs

    async def get_dialogs(self, limit: int | None = None) -> Any:
        items = [
            SimpleNamespace(entity=SimpleNamespace(username=username, title=title), name=title)
            for username, title in self._dialogs
        ]
        return SimpleNamespace(dialogs=items)


# ── Taxonomy ─────────────────────────────────────────────────────────────────


class TestTaxonomy:
    def test_source_types_cover_required_archetypes(self) -> None:
        values = {t.value for t in scam_watch.ScamSourceType}
        assert {
            "shill_channel",
            "pump_group",
            "alpha_rug_shill",
            "fake_airdrop",
            "seed_phrase_scam",
            "scam_token_channel",
        } <= values

    def test_heuristics_cover_required_keywords(self) -> None:
        haystack = " ".join(k for _, kws in scam_watch._DIALOG_HEURISTICS for k in kws).lower()
        for keyword in ("pump", "shill", "call", "alpha", "moon", "100x"):
            assert keyword in haystack

    def test_classify_dialog_maps_archetypes(self) -> None:
        assert scam_watch._classify_dialog("Pump Group VIP", "pumpvip") == scam_watch.ScamSourceType.PUMP_GROUP
        assert scam_watch._classify_dialog("Shill Central", "shillcentral") == scam_watch.ScamSourceType.SHILL_CHANNEL
        assert (
            scam_watch._classify_dialog("Alpha Calls 100x", "alphacalls") == scam_watch.ScamSourceType.ALPHA_RUG_SHILL
        )
        assert (
            scam_watch._classify_dialog("Seed Phrase Recovery", "seedhelp")
            == scam_watch.ScamSourceType.SEED_PHRASE_SCAM
        )
        assert (
            scam_watch._classify_dialog("Free Airdrop Presale", "freeairdrop") == scam_watch.ScamSourceType.FAKE_AIRDROP
        )
        assert scam_watch._classify_dialog("CoinTelegraph", "cointelegraph") is None

    def test_classify_dialog_never_flags_own_channels(self) -> None:
        assert scam_watch._classify_dialog("RugMunch Alpha", "rugmunchalpha") is None
        assert scam_watch._classify_dialog("RugMunchBot News", "rugmunchbot") is None

    def test_classify_dialog_honors_exclusions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_EXCLUDE", ["trustedpump"])
        assert scam_watch._classify_dialog("Pump Signals", "trustedpump") is None


# ── Env config ───────────────────────────────────────────────────────────────


class TestEnvConfig:
    def test_env_channels_parses_and_strips_at(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_CHANNELS", ["@scamchan1", "scamchan2"])
        monkeypatch.setenv("RMI_SCAM_CHANNELS", "@scamchan1|scamchan2, scamchan3")
        scam_watch.SCAM_CHANNELS = scam_watch._env_list("RMI_SCAM_CHANNELS", [])
        assert scam_watch.SCAM_CHANNELS == ["scamchan1", "scamchan2", "scamchan3"]

    def test_env_channels_default_empty(self) -> None:
        assert scam_watch.SCAM_CHANNELS == []

    def test_env_int_clamps_at_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RMI_SCAM_WATCH_SCAN_LIMIT", "-5")
        assert scam_watch._env_int("RMI_SCAM_WATCH_SCAN_LIMIT", 5) == 0


# ── Registry sources ─────────────────────────────────────────────────────────


def _registry_entry(username: str, kind: str, category: str) -> dict[str, Any]:
    return {"username": username, "name": username.title(), "kind": kind, "category": category, "enabled": True}


class TestRegistrySources:
    def test_picks_scammer_and_shill_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [
            _registry_entry("cryptobanter", "news", "shill_promotions"),
            _registry_entry("scamcaller", "scammer_caller", "scam_alerts"),
            _registry_entry("pumpgroup", "scammer_group", "shill_promotions"),
            _registry_entry("cointelegraph", "news", "crypto_news"),
        ]
        with patch("app.databus.tg_channel_registry.load_tg_channels", return_value=entries):
            sources = scam_watch._registry_scam_sources()
        usernames = {s["username"] for s in sources}
        assert usernames == {"cryptobanter", "scamcaller", "pumpgroup"}
        by_name = {s["username"]: s for s in sources}
        assert by_name["scamcaller"]["subtype"] == scam_watch.ScamSourceType.SHILL_CHANNEL
        assert by_name["pumpgroup"]["subtype"] == scam_watch.ScamSourceType.PUMP_GROUP
        assert by_name["cryptobanter"]["subtype"] == scam_watch.ScamSourceType.SHILL_CHANNEL

    def test_watcher_categories_excluded_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [
            _registry_entry("SlowMist", "news", "scam_alerts"),
            _registry_entry("ScamSniffer", "news", "phishing_alerts"),
        ]
        with patch("app.databus.tg_channel_registry.load_tg_channels", return_value=entries):
            assert scam_watch._registry_scam_sources() == []
            included = scam_watch._registry_scam_sources(include_watchers=True)
        assert {s["username"] for s in included} == {"slowmist", "scamsniffer"}

    def test_fails_open_on_missing_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch("app.databus.tg_channel_registry.load_tg_channels", side_effect=RuntimeError("boom")):
            assert scam_watch._registry_scam_sources() == []


# ── Dialog auto-discovery ────────────────────────────────────────────────────


class TestDiscovery:
    async def test_flags_matching_dialogs_and_caps(self) -> None:
        client = FakeDialogClient(
            [
                ("pumpsignals", "Pump Signals VIP"),
                ("shillcity", "Shill City"),
                ("cointelegraph", "CoinTelegraph"),
                ("rugmunchalpha", "RugMunch Alpha"),
            ]
        )
        sources = await scam_watch.discover_scam_channels(client, max_discovered=10)
        usernames = {s["username"] for s in sources}
        assert usernames == {"pumpsignals", "shillcity"}
        for s in sources:
            assert s["category"] == "scam_shill"
            assert s["discovered"] is True

    async def test_respects_cap(self) -> None:
        client = FakeDialogClient(
            [("pumpsignals", "Pump Signals"), ("shillcity", "Shill City"), ("alphacalls", "Alpha Calls")]
        )
        assert len(await scam_watch.discover_scam_channels(client, max_discovered=2)) == 2

    async def test_fails_open_on_bad_client(self) -> None:
        assert await scam_watch.discover_scam_channels(FakeReader({}), max_discovered=10) == []

    async def test_zero_cap_disables(self) -> None:
        client = FakeDialogClient([("pumpsignals", "Pump Signals")])
        assert await scam_watch.discover_scam_channels(client, max_discovered=0) == []


# ── Sweep ────────────────────────────────────────────────────────────────────


class TestSweep:
    async def test_tags_items_scam_shill_and_filters_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_CHANNELS", ["pumpsignals"])
        old = NOW - timedelta(hours=48)
        reader = FakeReader(
            {
                "pumpsignals": [
                    _msg(
                        "100x gem launching now — contract address in the pinned post, buy before the presale ends",
                        message_id=2,
                    ),
                    _msg("old call from last week that already got dumped on holders", message_id=1, posted_at=old),
                ]
            }
        )
        with patch("app.domains.news_ingest.discovery.discover_followed_channels", AsyncMock(return_value=[])):
            articles = await scam_watch.sweep_scam_channels(client=reader, lookback_hours=6, per_channel_limit=10)
        assert len(articles) == 1
        assert articles[0].category == "scam_shill"
        assert articles[0].title.startswith("100x gem")

    async def test_dedupes_same_content_across_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_CHANNELS", ["pumpsignals", "shillcity"])
        body = "This token is going to 100x, buy before the presale ends — contract below, do not miss it"
        reader = FakeReader(
            {
                "pumpsignals": [_msg(body, message_id=1)],
                "shillcity": [_msg(body, message_id=2, username="shillcity")],
            }
        )
        with patch("app.domains.news_ingest.discovery.discover_followed_channels", AsyncMock(return_value=[])):
            articles = await scam_watch.sweep_scam_channels(client=reader, lookback_hours=6, per_channel_limit=10)
        assert len(articles) == 1

    async def test_returns_empty_when_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "_build_headless_client", AsyncMock(return_value=None))
        assert await scam_watch.sweep_scam_channels() == []

    async def test_never_raises_on_flaky_reader(self) -> None:
        class BrokenReader:
            async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
                raise RuntimeError("boom")

        with patch("app.domains.news_ingest.discovery.discover_followed_channels", AsyncMock(return_value=[])):
            assert await scam_watch.sweep_scam_channels(client=BrokenReader(), lookback_hours=6) == []


# ── Address / ticker extraction ──────────────────────────────────────────────


class TestExtraction:
    def test_classify_entity(self) -> None:
        assert scam_watch.classify_entity("$PEPE") == "ticker"
        assert scam_watch.classify_entity("0x6982508145454Ce325dDbE47a25d4ec3d2311933") == "evm"
        assert scam_watch.classify_entity("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") == "solana"
        assert scam_watch.classify_entity("not-an-entity") == ""

    def test_extract_token_addresses_splits_by_kind(self) -> None:
        article = _article(
            "Presale now",
            entities=[
                "$PEPE",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "$PEPE",
            ],
        )
        addresses, tickers = scam_watch.extract_token_addresses(article)
        assert tickers == ["PEPE"]
        assert addresses == [
            {"address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933", "chain": "ethereum"},
            {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "chain": "solana"},
        ]

    def test_collect_submissions_dedupes_across_articles(self) -> None:
        addr = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
        articles = [
            _article("Shill A", username="pumpsignals", message_id=1, entities=["$AAA", addr]),
            _article("Shill B", username="shillcity", message_id=2, entities=[addr]),
        ]
        submissions = scam_watch._collect_submissions(articles)
        assert len(submissions) == 1
        assert submissions[0]["address"] == addr
        assert submissions[0]["chain"] == "ethereum"
        assert submissions[0]["source_channel"] == "Pumpsignals"


# ── Scanner feed ─────────────────────────────────────────────────────────────


class TestScannerFeed:
    async def test_submit_for_scan_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_SCAN_LIMIT", 5)
        with patch(
            "app.domains.scanner.service.scan_token",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            assert await scam_watch.submit_for_scan("0xabc") is False

    async def test_submit_for_scan_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SimpleNamespace(risk_flags=["honeypot"], safety_score=10)
        with patch("app.domains.scanner.service.scan_token", new=AsyncMock(return_value=result)):
            assert await scam_watch.submit_for_scan("0xabc", "ethereum") is True

    async def test_mark_seen_dedupes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "get_redis_async", lambda: None)
        assert await scam_watch._mark_seen("0xabc") is True
        assert await scam_watch._mark_seen("0xabc") is False
        assert await scam_watch._mark_seen("0xdef") is True

    async def test_feed_scanner_bounded_and_skips_seen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_SCAN_LIMIT", 2)
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_QUEUE_LIMIT", 3)
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_ENQUEUE", True)
        monkeypatch.setattr(scam_watch, "get_redis_async", lambda: None)
        scanned: list[str] = []
        enqueued: list[str] = []

        async def fake_scan(address: str, chain: str = "ethereum") -> bool:
            scanned.append(address)
            return True

        async def fake_enqueue(entry: dict[str, Any]) -> bool:
            enqueued.append(str(entry.get("address")))
            return True

        monkeypatch.setattr(scam_watch, "submit_for_scan", fake_scan)
        monkeypatch.setattr(scam_watch, "_enqueue_for_scanner", fake_enqueue)
        submissions = [
            {
                "address": f"0x{i}",
                "chain": "ethereum",
                "source_channel": "c",
                "source_url": "u",
                "category": "scam_shill",
            }
            for i in range(5)
        ]
        submitted, queued = await scam_watch._feed_scanner(submissions)
        assert submitted == 2  # bounded by SCAN_LIMIT
        assert queued == 3  # bounded by QUEUE_LIMIT
        assert scanned == ["0x0", "0x1"]
        assert enqueued == ["0x0", "0x1", "0x2"]

    async def test_feed_scanner_respects_enqueue_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_SCAN_LIMIT", 0)
        monkeypatch.setattr(scam_watch, "SCAM_WATCH_ENQUEUE", False)
        monkeypatch.setattr(scam_watch, "get_redis_async", lambda: None)
        submitted, queued = await scam_watch._feed_scanner(
            [
                {
                    "address": "0x1",
                    "chain": "ethereum",
                    "source_channel": "c",
                    "source_url": "u",
                    "category": "scam_shill",
                }
            ]
        )
        assert (submitted, queued) == (0, 0)


# ── Scheduled job ────────────────────────────────────────────────────────────


class TestRunJob:
    async def test_runs_without_ctx_and_returns_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        article = _article("Pump it", entities=["$AAA", "0x6982508145454Ce325dDbE47a25d4ec3d2311933"])
        with (
            patch.object(scam_watch, "sweep_scam_channels", new=AsyncMock(return_value=[article])),
            patch.object(scam_watch, "_feed_scanner", new=AsyncMock(return_value=(1, 1))),
        ):
            assert await scam_watch.run_scam_watch() == 2

    async def test_skips_when_sweep_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(scam_watch, "sweep_scam_channels", new=AsyncMock(return_value=[])):
            assert await scam_watch.run_scam_watch() == 0

    async def test_never_raises_on_sweep_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(scam_watch, "sweep_scam_channels", new=AsyncMock(side_effect=RuntimeError("boom"))):
            assert await scam_watch.run_scam_watch() == 0


# ── Registration ─────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})


class TestRegistration:
    def test_registers_scam_watch_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "NEWS_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "SCAM_WATCH_ENABLED", True)
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        names = {j["name"] for j in fake_jq.jobs}
        assert "channel_scam_watch" in names
        job = next(j for j in fake_jq.jobs if j["name"] == "channel_scam_watch")
        assert job["interval"] == scam_watch.SCAM_WATCH_INTERVAL_MINUTES * 60
        # The job registers without any chat id resolution (feeds scanner, posts nothing).
        assert job["first"] == channels_scheduler._FIRST_RUN_SECONDS["scam_watch"]

    def test_scam_watch_job_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "SCAM_WATCH_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert "channel_scam_watch" not in {j["name"] for j in fake_jq.jobs}
