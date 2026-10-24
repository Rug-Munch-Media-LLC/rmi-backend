"""Security contract: IDOR, banned accounts, moderation rights, RFC 7807, limits."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from tests.unit.domains.engagement.conftest import RecordingBanPolicy, anon, as_user, post

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from tests.unit.domains.engagement.helpers import InMemoryEngagementStore

COMMENTS = "/api/v1/articles/art-1/comments"


def _create(client: TestClient, body: str = "hello") -> dict:
    response = post(client, COMMENTS, {"body": body})
    assert response.status_code == 201, response.text
    return response.json()


def test_comment_is_not_reachable_through_another_article(client: TestClient) -> None:
    """IDOR: a comment id from art-1 must 404 under art-2, not leak or mutate."""
    as_user(client, "u1")
    created = _create(client, "secret")

    assert client.patch(f"/api/v1/articles/art-2/comments/{created['id']}", json={"body": "x"}).status_code == 404
    assert client.delete(f"/api/v1/articles/art-2/comments/{created['id']}").status_code == 404


def test_moderation_route_also_scopes_by_article(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "secret")
    as_user(client, "mod", role="MODERATOR")
    url = f"/api/v1/articles/art-2/comments/{created['id']}/status"

    assert client.patch(url, json={"status": "hidden"}).status_code == 404


def test_another_user_cannot_edit_or_delete(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client, "mine")
    as_user(client, "u2")

    assert client.patch(f"{COMMENTS}/{created['id']}", json={"body": "hijack"}).status_code == 403
    assert client.delete(f"{COMMENTS}/{created['id']}").status_code == 403


def test_banned_user_cannot_post(
    client: TestClient, store: InMemoryEngagementStore, ban_policy: RecordingBanPolicy
) -> None:
    ban_policy.banned.add("u-banned")
    as_user(client, "u-banned")

    assert post(client, COMMENTS, {"body": "spam"}).status_code == 403
    assert store.comments == {}
    assert ban_policy.calls == ["u-banned"]


def test_anonymous_cannot_comment(client: TestClient) -> None:
    anon(client)
    assert post(client, COMMENTS, {"body": "hello"}).status_code == 401


def test_non_moderator_cannot_change_status(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client)
    as_user(client, "u2")

    response = client.patch(f"{COMMENTS}/{created['id']}/status", json={"status": "hidden"})
    assert response.status_code == 403


def test_moderator_status_transitions(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client)
    as_user(client, "mod", role="MODERATOR")
    url = f"{COMMENTS}/{created['id']}/status"

    hidden = client.patch(url, json={"status": "hidden"})
    assert hidden.status_code == 200
    assert hidden.json()["status"] == "hidden"
    assert hidden.json()["body"] is None

    removed = client.patch(url, json={"status": "removed"})
    assert removed.json()["status"] == "removed"

    illegal = client.patch(url, json={"status": "visible"})
    assert illegal.status_code == 422


def test_moderator_can_delete_someone_elses_comment(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client)
    as_user(client, "mod", role="ADMIN")

    assert client.delete(f"{COMMENTS}/{created['id']}").status_code == 200


def test_report_is_idempotent_and_auto_hides(client: TestClient) -> None:
    as_user(client, "author")
    created = _create(client)
    url = f"{COMMENTS}/{created['id']}/report"

    as_user(client, "r1")
    first = post(client, url, {"reason": "spam"})
    second = post(client, url, {"reason": "spam"})
    assert first.json()["report_count"] == 1
    assert second.json()["report_count"] == 1
    assert second.json()["status"] == "visible"

    as_user(client, "r2")
    post(client, url, {"reason": "spam"})
    as_user(client, "r3")
    threshold = post(client, url, {"reason": "spam"})
    assert threshold.json()["report_count"] == 3
    assert threshold.json()["status"] == "hidden"


def test_cannot_report_your_own_comment(client: TestClient) -> None:
    as_user(client, "u1")
    created = _create(client)

    assert post(client, f"{COMMENTS}/{created['id']}/report", {"reason": "me"}).status_code == 422


def test_errors_are_rfc7807(client: TestClient) -> None:
    as_user(client, "u1")
    response = client.patch(f"{COMMENTS}/missing", json={"body": "x"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert "type" in body
    assert "title" in body


def test_create_comment_is_rate_limited(client: TestClient) -> None:
    """Burst 5/min per identity; a unique API key isolates this bucket."""
    as_user(client, "u1")
    client.headers.update({"X-API-Key": f"ratelimit-{uuid.uuid4()}"})

    statuses = [post(client, COMMENTS, {"body": f"burst-{index}"}).status_code for index in range(6)]
    assert statuses[:5] == [201] * 5
    assert statuses[5] == 429
