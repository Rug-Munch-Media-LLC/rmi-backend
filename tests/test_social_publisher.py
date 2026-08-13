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


def test_real_vs_stub_adapters():
    adapters = default_adapters()
    # Real transports
    assert adapters["telegram"].implemented is True
    assert adapters["farcaster"].implemented is True
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
