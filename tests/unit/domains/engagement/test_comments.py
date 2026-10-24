"""Comment contract: create, sanitise, threads, pagination, edit, soft delete."""

from __future__ import annotations

from fastapi.testclient import TestClient  # noqa: TC002 — pytest keeps it runtime

from tests.unit.domains.engagement.conftest import as_user, post
from tests.unit.domains.engagement.helpers import InMemoryEngagementStore  # noqa: TC001

COMMENTS = "/api/v1/articles/art-1/comments"


def _create(client: TestClient, body: str, parent_id: str | None = None) -> dict:
    response = post(client, COMMENTS, {"body": body, "parent_id": parent_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_create_comment_strips_html(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "<b>bullish</b>  on   this")

    assert created["body"] == "bullish on this"
    assert created["is_mine"] is True
    assert created["status"] == "visible"
    assert created["replies"] == []


def test_body_that_sanitises_to_nothing_is_rejected(client: TestClient) -> None:
    as_user(client, "u1")
    assert post(client, COMMENTS, {"body": "<script></script>"}).status_code == 422


def test_reply_is_nested_under_its_root(client: TestClient) -> None:
    as_user(client, "u1")
    root = _create(client, "root")
    as_user(client, "u2")
    _create(client, "reply", parent_id=root["id"])

    items = client.get(COMMENTS).json()["items"]
    assert len(items) == 1
    assert [reply["body"] for reply in items[0]["replies"]] == ["reply"]


def test_reply_to_reply_flattens_to_the_root(client: TestClient) -> None:
    as_user(client, "u1")
    root = _create(client, "root")
    as_user(client, "u2")
    reply = _create(client, "reply", parent_id=root["id"])
    nested = _create(client, "nested", parent_id=reply["id"])

    assert nested["parent_id"] == root["id"]
    items = client.get(COMMENTS).json()["items"]
    assert len(items) == 1
    assert len(items[0]["replies"]) == 2


def test_unknown_parent_is_404(client: TestClient) -> None:
    as_user(client, "u1")
    response = post(client, COMMENTS, {"body": "orphan", "parent_id": "does-not-exist"})
    assert response.status_code == 404


def test_thread_pagination(client: TestClient) -> None:
    as_user(client, "u1")
    for index in range(3):
        _create(client, f"root-{index}")

    first = client.get(COMMENTS, params={"limit": 2, "offset": 0}).json()
    second = client.get(COMMENTS, params={"limit": 2, "offset": 2}).json()

    assert first["total"] == 3
    assert len(first["items"]) == 2
    assert first["has_more"] is True
    assert len(second["items"]) == 1
    assert second["has_more"] is False
    assert second["items"][0]["body"] == "root-2"


def test_newest_sort_reverses_the_page(client: TestClient) -> None:
    as_user(client, "u1")
    for index in range(3):
        _create(client, f"root-{index}")

    body = client.get(COMMENTS, params={"sort": "newest", "limit": 1}).json()
    assert body["items"][0]["body"] == "root-2"


def test_edit_sets_edited_at(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "typo")

    response = client.patch(f"{COMMENTS}/{created['id']}", json={"body": "fixed"})
    assert response.status_code == 200
    assert response.json()["body"] == "fixed"
    assert response.json()["edited_at"] is not None


def test_soft_delete_leaves_a_tombstone(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "remove me")

    deleted = client.delete(f"{COMMENTS}/{created['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["body"] is None

    listing = client.get(COMMENTS).json()
    assert listing["items"][0]["deleted"] is True
    assert listing["items"][0]["body"] is None


def test_repeated_delete_is_a_no_op(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "double delete")

    assert client.delete(f"{COMMENTS}/{created['id']}").status_code == 200
    assert client.delete(f"{COMMENTS}/{created['id']}").status_code == 200


def test_edit_after_delete_is_forbidden(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "gone")
    client.delete(f"{COMMENTS}/{created['id']}")

    assert client.patch(f"{COMMENTS}/{created['id']}", json={"body": "back"}).status_code == 403


def test_store_records_one_row_per_comment(client: TestClient, store: InMemoryEngagementStore) -> None:
    as_user(client, "u1")
    _create(client, "one")
    _create(client, "two")
    assert len(store.comments) == 2
