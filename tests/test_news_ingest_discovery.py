"""Unit tests for Telegram channel discovery (app/domains/news_ingest/discovery.py).

Covers:
  * public channel/group enumeration via a fake Telethon ``get_dialogs``
  * private chats (no username) excluded
  * wrapper-client unwrap (``HeadlessTelegramClient._client``)
  * fail-open on missing ``get_dialogs`` and on exceptions

No live network I/O — the Telethon surface is faked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.domains.news_ingest.discovery import discover_followed_channels


class _FakeDialog:
    def __init__(self, entity: Any, name: str | None = None) -> None:
        self.entity = entity
        self.name = name


class _FakeDialogs:
    def __init__(self, items: list[_FakeDialog]) -> None:
        self.dialogs = items


class _FakeClient:
    def __init__(self, dialogs: list[_FakeDialog]) -> None:
        self._dialogs = dialogs

    async def get_dialogs(self, limit: int | None = None) -> _FakeDialogs:
        return _FakeDialogs(self._dialogs[:limit] if limit else self._dialogs)


def _channel(username: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(username=username, title=title)


def _private(title: str) -> SimpleNamespace:
    return SimpleNamespace(username=None, title=title)


async def test_discover_returns_public_channels_and_groups() -> None:
    client = _FakeClient(
        [
            _FakeDialog(_channel("cointelegraph", "CoinTelegraph"), "CoinTelegraph"),
            _FakeDialog(_channel("wublockchain", "Wu Blockchain"), "Wu Blockchain"),
            _FakeDialog(_private("Private Group")),  # excluded — no username
            _FakeDialog(_channel("defi_news", "DeFi News"), "DeFi News"),
        ]
    )
    result = await discover_followed_channels(client)
    assert result == [
        ("cointelegraph", "CoinTelegraph"),
        ("wublockchain", "Wu Blockchain"),
        ("defi_news", "DeFi News"),
    ]


async def test_discover_dedupes_repeated_usernames() -> None:
    client = _FakeClient(
        [
            _FakeDialog(_channel("cointelegraph", "CoinTelegraph")),
            _FakeDialog(_channel("cointelegraph", "CoinTelegraph")),  # duplicate
        ]
    )
    result = await discover_followed_channels(client)
    assert result == [("cointelegraph", "CoinTelegraph")]


async def test_discover_unwraps_headless_client_wrapper() -> None:
    inner = _FakeClient([_FakeDialog(_channel("cointelegraph", "CoinTelegraph"), "CoinTelegraph")])

    class Wrapper:
        _client = inner

    result = await discover_followed_channels(Wrapper())
    assert result == [("cointelegraph", "CoinTelegraph")]


async def test_discover_fail_open_missing_get_dialogs() -> None:
    class NoDialogs:
        pass

    assert await discover_followed_channels(NoDialogs()) == []


async def test_discover_fail_open_on_exception() -> None:
    class Boom:
        async def get_dialogs(self, limit: int | None = None) -> Any:
            raise RuntimeError("telegram unavailable")

    assert await discover_followed_channels(Boom()) == []


async def test_discover_fail_open_none_client() -> None:
    assert await discover_followed_channels(None) == []
