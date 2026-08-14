"""Pure unit tests for the multi-platform social publisher.

No network, no DB, no real transports: every adapter in the orchestrator
tests is a fake, and the real adapters are only exercised for their
``enabled`` flags (config reads) and stub behaviour.
"""

from __future__ import annotations

import pytest

from app.domains.social import (
    ALL_PLATFORMS,
    DiscordPublisher,
    FarcasterPublisher,
    MastodonPublisher,
    PlatformAdapter,
    PostizPublisher,
    SocialPublisher,
    TelegramPublisher,
    XPublisher,
    default_adapters,
)
from app.domains.social.publisher import PostResult, _error, _ok


class FakeAdapter(PlatformAdapter):
    """Test double — records calls, controllable result / exception."""

    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        result: PostResult | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.name = name
        self._enabled = enabled
        self._result = result if result is not None else _ok(f"{name}-post-id")
        self._exc = exc
        self.calls: list[tuple[str, str | None]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def post(self, text: str, *, media_url: str | None = None) -> PostResult:
        self.calls.append((text, media_url))
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def publisher() -> SocialPublisher:
    """Publisher with two fakes: telegram (enabled) + x (disabled)."""
    telegram = FakeAdapter("telegram", enabled=True)
    x = FakeAdapter("x", enabled=False)
    return SocialPublisher(adapters={"telegram": telegram, "x": x})


# ── Registry / adapter surface ─────────────────────────────────────────


def test_default_adapters_register_all_platforms():
    adapters = default_adapters()
    assert set(adapters) == set(ALL_PLATFORMS)
    assert isinstance(adapters["telegram"], TelegramPublisher)
    assert isinstance(adapters["farcaster"], FarcasterPublisher)
    assert isinstance(adapters["x"], XPublisher)
    assert isinstance(adapters["mastodon"], MastodonPublisher)
    assert isinstance(adapters["discord"], DiscordPublisher)
    assert isinstance(adapters["postiz"], PostizPublisher)


def test_real_vs_stub_adapters():
    adapters = default_adapters()
    # Real transports
    assert adapters["telegram"].implemented is True
    assert adapters["farcaster"].implemented is True
    assert adapters["postiz"].implemented is True
    # Documented stubs
    assert adapters["x"].implemented is False
    assert adapters["mastodon"].implemented is False
    assert adapters["discord"].implemented is False


def test_telegram_enabled_requires_channel(monkeypatch):
    monkeypatch.delenv("RMI_MAIN_CHANNEL_ID", raising=False)
    assert TelegramPublisher().enabled is False
    monkeypatch.setenv("RMI_MAIN_CHANNEL_ID", "-100123456789")
    assert TelegramPublisher().enabled is True
    # Explicit chat id wins over env.
    assert TelegramPublisher(chat_id=42).enabled is True


def test_farcaster_enabled_requires_creds(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "farcaster_api_key", "")
    monkeypatch.setattr(settings, "farcaster_signer_uuid", "")
    assert FarcasterPublisher().enabled is False
    monkeypatch.setattr(settings, "farcaster_api_key", "neynar-key")
    monkeypatch.setattr(settings, "farcaster_signer_uuid", "uuid-1")
    assert FarcasterPublisher().enabled is True


def test_stub_enabled_flags(monkeypatch):
    from app.config import settings

    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    assert XPublisher().enabled is False
    monkeypatch.setenv("X_BEARER_TOKEN", "token")
    assert XPublisher().enabled is True

    monkeypatch.setattr(settings, "mastodon_access_token", "")
    monkeypatch.setattr(settings, "mastodon_instance_url", "")
    assert MastodonPublisher().enabled is False
    monkeypatch.setattr(settings, "mastodon_access_token", "token")
    monkeypatch.setattr(settings, "mastodon_instance_url", "https://mastodon.social")
    assert MastodonPublisher().enabled is True

    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    assert DiscordPublisher().enabled is False
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123")
    assert DiscordPublisher().enabled is True


async def test_stub_post_returns_error_even_when_enabled(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "token")
    result = await XPublisher().post("hello")
    assert result["ok"] is False
    assert result["post_id"] is None
    assert result["error"] is not None
    assert "stub" in result["error"].lower()


# ── publish_everywhere behaviour ────────────────────────────────────────


async def test_publishes_to_enabled_and_skips_disabled(publisher):
    results = await publisher.publish_everywhere("GM")
    assert results["telegram"]["ok"] is True
    assert results["telegram"]["post_id"] == "telegram-post-id"
    assert results["x"]["ok"] is False
    assert results["x"]["error"] == "not enabled"

    # The disabled adapter was never invoked.
    telegram: FakeAdapter = publisher.adapters["telegram"]
    x: FakeAdapter = publisher.adapters["x"]
    assert telegram.calls == [("GM", None)]
    assert x.calls == []


async def test_failing_platform_does_not_block_others():
    boom = FakeAdapter("farcaster", exc=RuntimeError("neynar down"))
    ok = FakeAdapter("telegram")
    publisher = SocialPublisher(adapters={"farcaster": boom, "telegram": ok})

    results = await publisher.publish_everywhere("msg", media_url="https://x.io")

    assert results["farcaster"]["ok"] is False
    assert results["farcaster"]["error"] is not None
    assert "neynar down" in results["farcaster"]["error"]
    assert results["telegram"]["ok"] is True
    assert ok.calls == [("msg", "https://x.io")]  # still posted


async def test_adapter_returning_failure_does_not_block_others():
    fail = FakeAdapter("x", result=_error("rate limited"))
    ok = FakeAdapter("telegram")
    publisher = SocialPublisher(adapters={"x": fail, "telegram": ok})

    results = await publisher.publish_everywhere("msg")
    assert results["x"]["ok"] is False
    assert results["telegram"]["ok"] is True


async def test_platforms_subset_filter(publisher):
    results = await publisher.publish_everywhere("GM", platforms=["telegram"])
    assert set(results) == {"telegram"}
    assert results["telegram"]["ok"] is True


async def test_unknown_platform_reported(publisher):
    results = await publisher.publish_everywhere("GM", platforms=["telegram", "nope"])
    assert results["nope"]["ok"] is False
    assert "unknown platform" in results["nope"]["error"]
    assert results["telegram"]["ok"] is True


async def test_post_alias_matches_publish_everywhere(publisher):
    assert await publisher.post("GM") == await publisher.publish_everywhere("GM")


async def test_enabled_platforms_lists_only_configured(publisher):
    assert publisher.enabled_platforms() == ["telegram"]


async def test_return_dict_shape(publisher):
    results = await publisher.publish_everywhere("GM")
    for entry in results.values():
        assert set(entry) == {"ok", "post_id", "error"}
        assert isinstance(entry["ok"], bool)
        assert entry["error"] is None or isinstance(entry["error"], str)


# ── PostizPublisher (REAL — routes through the Postiz domain service) ───


class FakePostizService:
    """Test double for app.domains.postiz.services.scheduling.schedule_post."""

    def __init__(self, *, result: dict | None = None, exc: Exception | None = None) -> None:
        self._result = result if result is not None else {"id": "postiz-post-1"}
        self._exc = exc
        self.requests: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.requests.append(request)
        if self._exc is not None:
            raise self._exc
        return self._result


def _all_postiz_target_ids() -> set[str]:
    """Every Postiz platform id minus the real-direct-adapter platforms."""
    from app.domains.postiz.platforms import PLATFORMS

    return {pid for pid in PLATFORMS if pid not in {"telegram", "farcaster"}}


def test_postiz_enabled_when_service_available():
    # The Postiz service exists in this repo, so the real adapter is enabled.
    assert PostizPublisher().enabled is True


def test_postiz_enabled_with_injected_service():
    # Injection short-circuits the availability probe.
    assert PostizPublisher(schedule_post=FakePostizService()).enabled is True


async def test_postiz_posts_via_service_with_platform_ids():
    service = FakePostizService()
    adapter = PostizPublisher(schedule_post=service)

    result = await adapter.post("GM everyone", media_url="https://rugmunch.io/1")

    assert result["ok"] is True
    assert result["post_id"] == "postiz-post-1"
    assert len(service.requests) == 1
    req = service.requests[0]
    assert req["text"] == "GM everyone"
    assert req["media_urls"] == ["https://rugmunch.io/1"]
    # Targets every Postiz platform except those with a real direct adapter.
    assert set(req["platforms"]) == _all_postiz_target_ids()
    assert "telegram" not in req["platforms"]
    assert "farcaster" not in req["platforms"]


async def test_postiz_custom_platform_ids_are_lowercased():
    service = FakePostizService()
    adapter = PostizPublisher(schedule_post=service, platform_ids=["bluesky", "NOSTR"])

    result = await adapter.post("hi")

    assert result["ok"] is True
    assert service.requests[0]["platforms"] == ["bluesky", "nostr"]


async def test_postiz_fail_open_when_service_raises():
    service = FakePostizService(exc=RuntimeError("postiz store down"))
    adapter = PostizPublisher(schedule_post=service)

    result = await adapter.post("hello")

    assert result["ok"] is False
    assert result["post_id"] is None
    assert "postiz store down" in result["error"]


async def test_postiz_service_missing_returns_error(monkeypatch):
    import sys

    # Simulate the Postiz domain not being installed.
    monkeypatch.setitem(sys.modules, "app.domains.postiz.services.scheduling", None)

    result = await PostizPublisher().post("hello")

    assert result["ok"] is False
    assert "Postiz service unavailable" in result["error"]


async def test_publish_everywhere_includes_postiz():
    service = FakePostizService()
    telegram = FakeAdapter("telegram", enabled=True)
    postiz = PostizPublisher(schedule_post=service)
    publisher = SocialPublisher(adapters={"telegram": telegram, "postiz": postiz})

    results = await publisher.publish_everywhere("GM", media_url="https://x.io")

    assert results["telegram"]["ok"] is True
    assert results["postiz"]["ok"] is True
    assert results["postiz"]["post_id"] == "postiz-post-1"
    assert service.requests[0]["media_urls"] == ["https://x.io"]


async def test_postiz_failure_does_not_block_direct_adapters():
    service = FakePostizService(exc=RuntimeError("postiz down"))
    telegram = FakeAdapter("telegram", enabled=True)
    postiz = PostizPublisher(schedule_post=service)
    publisher = SocialPublisher(adapters={"telegram": telegram, "postiz": postiz})

    results = await publisher.publish_everywhere("GM")

    assert results["postiz"]["ok"] is False
    assert results["telegram"]["ok"] is True
    assert telegram.calls == [("GM", None)]  # still posted
