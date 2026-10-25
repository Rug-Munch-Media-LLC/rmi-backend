"""Reaction contract: idempotency, duplicate prevention, auth, article scoping."""

from __future__ import annotations

from fastapi.testclient import TestClient  # noqa: TC002 — pytest keeps it runtime

from tests.unit.domains.engagement.conftest import anon, as_user, post
from tests.unit.domains.engagement.helpers import InMemoryEngagementStore  # noqa: TC001

REACTIONS = "/api/v1/articles/art-1/reactions"


def test_react_twice_is_idempotent(client: TestClient, store: InMemoryEngagementStore) -> None:
    as_user(client, "u1")
    first = post(client, REACTIONS, {"kind": "bullish"})
    second = post(client, REACTIONS, {"kind": "bullish"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["counts"] == {"bullish": 1}
    assert second.json()["counts"] == {"bullish": 1}
    assert len(store.reactions) == 1


def test_duplicate_prevention_at_store_level(store: InMemoryEngagementStore) -> None:
    import asyncio

    assert asyncio.run(store.add_reaction("art-1", "u1", "like")) is True
    assert asyncio.run(store.add_reaction("art-1", "u1", "like")) is False
    assert len(store.reactions) == 1


def test_multiple_kinds_from_one_user_are_distinct(client: TestClient) -> None:
    as_user(client, "u1")
    post(client, REACTIONS, {"kind": "like"})
    body = post(client, REACTIONS, {"kind": "important"}).json()

    assert body["counts"] == {"like": 1, "important": 1}
    assert sorted(body["my_reactions"]) == ["important", "like"]


def test_unreact_is_idempotent(client: TestClient) -> None:
    as_user(client, "u1")
    post(client, REACTIONS, {"kind": "bearish"})
    first = client.delete(f"{REACTIONS}/bearish")
    second = client.delete(f"{REACTIONS}/bearish")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["counts"] == {}
    assert second.json()["my_reactions"] == []


def test_react_requires_auth(client: TestClient) -> None:
    anon(client)
    assert post(client, REACTIONS, {"kind": "like"}).status_code == 401


def test_unknown_kind_is_rejected(client: TestClient) -> None:
    as_user(client, "u1")
    post(client, REACTIONS, {"kind": "like"})
    # DELETE path validates against the vocabulary; JSON body is validated by Pydantic.
    assert client.delete(f"{REACTIONS}/clap").status_code == 422
    assert post(client, REACTIONS, {"kind": "clap"}).status_code == 422


def test_react_on_unknown_article_is_404(client: TestClient) -> None:
    as_user(client, "u1")
    assert post(client, "/api/v1/articles/missing/reactions", {"kind": "like"}).status_code == 404


def test_reactors_listing_is_paginated(client: TestClient) -> None:
    for user in ("u1", "u2", "u3"):
        as_user(client, user)
        post(client, REACTIONS, {"kind": "like"})
    as_user(client, "u1")

    page = client.get(f"{REACTIONS}/like/users", params={"limit": 2, "offset": 2})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    assert body["items"][0]["user_id"] == "u3"


def test_anonymous_reads_hide_personal_state(client: TestClient) -> None:
    as_user(client, "u1")
    post(client, REACTIONS, {"kind": "like"})
    anon(client)

    body = client.get(REACTIONS).json()
    assert body["counts"] == {"like": 1}
    assert body["my_reactions"] == []
