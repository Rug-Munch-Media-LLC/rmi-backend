"""Tests for the Ghost Content API proxy — no network, no DB, no Redis.

The proxy's whole job is: reach the real Ghost Content API, apply the tag
filter server-side, keep the Content API key server-side, and fail loudly
(503 + host) instead of returning an empty 200 when Ghost is unreachable.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.intelligence_panel import router
from app.services import ghost_content as gc

app = FastAPI()
app.include_router(router)

RAW_POST: dict[str, Any] = {
    "id": "6adb3e7cd8914b00013ed426",
    "slug": "the-morning-rug-october-23-2026",
    "url": "https://rugmunch.io/the-morning-rug-october-23-2026/",
    "title": "The Morning Rug",
    "excerpt": "Rug Munch Intelligence - AI-powered market summary",
    "html": "<p>body</p>",
    "feature_image": "https://rugmunch.io/brand/rmi_badge_v4.png",
    "published_at": "2026-10-23T12:00:00.000Z",
    "updated_at": "2026-10-23T12:00:00.000Z",
    "reading_time": 4,
    "tags": [{"name": "news"}],
    "authors": [{"name": "Rug Muncher"}],
}

PAYLOAD = {
    "posts": [RAW_POST],
    "meta": {"pagination": {"page": 1, "limit": 20, "pages": 1, "total": 1}},
}


def _factory(handler: Any) -> Any:
    def build(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return build


@pytest.fixture(autouse=True)
def _ghost_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOST_URL", "http://rmi-ghost:2368")
    monkeypatch.setenv("GHOST_CONTENT_API_KEY", "test-key")
    monkeypatch.setenv("GHOST_SITE_URL", "https://rugmunch.io")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("", "status:published"),
        ("news", "status:published+tag:news"),
        ("market-summary", "status:published+tag:market-summary"),
        ("news+status:draft", "status:published+tag:news"),
        ("  ", "status:published"),
    ],
)
def test_build_filter_scopes_server_side(tag: str, expected: str) -> None:
    assert gc.build_filter(tag) == expected


def test_posts_endpoint_returns_real_post_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD)

    monkeypatch.setattr(gc, "_CLIENT_FACTORY", _factory(handler))

    resp = client.get("/api/v1/intelligence/ghost/posts", params={"limit": 5, "tag": "news"})

    assert resp.status_code == 200
    post = resp.json()["posts"][0]
    assert post["slug"] == "the-morning-rug-october-23-2026"
    assert post["feature_image"] == "https://rugmunch.io/brand/rmi_badge_v4.png"
    assert post["excerpt"].startswith("Rug Munch Intelligence")
    assert post["published_at"] == "2026-10-23T12:00:00.000Z"
    assert post["tags"] == ["news"]

    request = seen[0]
    assert request.url.params["filter"] == "status:published+tag:news"
    assert request.url.params["limit"] == "5"
    assert request.url.params["key"] == "test-key"
    assert request.headers["X-Forwarded-Proto"] == "https"
    assert request.headers["X-Forwarded-Host"] == "rugmunch.io"
    # Key stays server-side: never echoed in the response body.
    assert "test-key" not in resp.text


def test_dns_failure_returns_503_naming_host(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=request)

    monkeypatch.setattr(gc, "_CLIENT_FACTORY", _factory(handler))

    resp = client.get("/api/v1/intelligence/ghost/posts")

    assert resp.status_code == 503
    body = resp.json()
    assert body["posts"] == []
    assert body["error"]["code"] == "ghost_dns_error"
    assert body["error"]["host"] == "rmi-ghost:2368"
    assert body["meta"]["degraded"] is True


def test_missing_content_key_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GHOST_CONTENT_API_KEY", "")

    resp = client.get("/api/v1/intelligence/ghost/posts")

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ghost_key_not_configured"


def test_upstream_error_status_is_not_masked(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": [{"message": "boom"}]})

    monkeypatch.setattr(gc, "_CLIENT_FACTORY", _factory(handler))

    resp = client.get("/api/v1/intelligence/ghost/posts")

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ghost_http_error"
    assert "status=500" in resp.json()["error"]["detail"]


def test_post_by_slug_404_when_ghost_has_none(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"posts": []})

    monkeypatch.setattr(gc, "_CLIENT_FACTORY", _factory(handler))

    resp = client.get("/api/v1/intelligence/ghost/posts/does-not-exist")

    assert resp.status_code == 404
