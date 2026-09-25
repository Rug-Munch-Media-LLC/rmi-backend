"""GetXAPI provider contract tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.shill import getxapi


class FakeCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> tuple[Any, bool]:
        return self.values.get(key), False

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self.values[key] = value


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self.payload


@pytest.mark.asyncio
async def test_fetch_profile_normalizes_getxapi_response(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = FakeCache()
    monkeypatch.setattr(getxapi, "resolve_key", lambda _name: "test-key")
    monkeypatch.setattr(
        getxapi,
        "_get_json",
        _async_return(
            {
                "data": {
                    "userName": "kol",
                    "id": "42",
                    "name": "KOL",
                    "description": "alpha",
                    "followers": 10,
                    "following": 2,
                    "statusesCount": 8,
                    "favouritesCount": 3,
                }
            }
        ),
    )

    profile = await getxapi.fetch_profile("@kol", cache=cache)  # type: ignore[arg-type]

    assert profile is not None
    assert profile["source"] == "getxapi"
    assert profile["handle"] == "kol"
    assert profile["metrics"]["followers"] == 10
    assert await cache.get("shill:getxapi:profile:kol") == (profile, False)


@pytest.mark.asyncio
async def test_fetch_user_tweets_normalizes_items(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = FakeCache()
    monkeypatch.setattr(getxapi, "resolve_key", lambda _name: "test-key")
    monkeypatch.setattr(
        getxapi,
        "_get_json",
        _async_return(
            {
                "tweets": [
                    {
                        "id": "123",
                        "text": "$PEPE alpha",
                        "url": "https://x.com/kol/status/123",
                        "likeCount": 4,
                    }
                ]
            }
        ),
    )

    tweets = await getxapi.fetch_user_tweets("kol", limit=5, cache=cache)  # type: ignore[arg-type]

    assert tweets[0]["tweet_id"] == "123"
    assert tweets[0]["text"] == "$PEPE alpha"
    assert tweets[0]["likes"] == 4


@pytest.mark.asyncio
async def test_missing_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getxapi, "resolve_key", lambda _name: "")
    assert await getxapi.fetch_profile("kol") is None
    assert await getxapi.fetch_user_tweets("kol") == []


def _async_return(value: dict[str, Any]):
    async def _call(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return value

    return _call
