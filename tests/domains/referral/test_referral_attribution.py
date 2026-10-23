"""Tests for referral attribution: allowlist, rejection, hashing, endpoints."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.referral.allowlist import (
    InvalidAttributionError,
    UnknownPartnerError,
    resolve_partner_url,
    validate_code,
    validate_surface,
)
from app.domains.referral.constants import PARTNER_TARGETS
from app.domains.referral.deps import get_store, require_internal_key
from app.domains.referral.hashing import hash_identifier
from app.domains.referral.router import router


class FakeStore:
    """In-memory stand-in for ReferralStore."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = False

    async def record(self, **row: Any) -> uuid.UUID:
        if self.fail:
            raise RuntimeError("db down")
        self.rows.append(row)
        return uuid.uuid4()

    async def stats(self, *, partner: str | None = None, days: int = 7, limit: int = 100):
        return [{"partner": partner or "jupiter", "clicks": len(self.rows)}]


@pytest.fixture
def api() -> Iterator[tuple[TestClient, FakeStore]]:
    store = FakeStore()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[require_internal_key] = lambda: None
    with TestClient(app) as client:
        yield client, store


# ── allowlist ────────────────────────────────────────────────────────────


def test_every_allowlisted_partner_resolves_to_its_own_https_host() -> None:
    for partner in PARTNER_TARGETS:
        url = resolve_partner_url(partner, "rugmunch")
        assert url.startswith("https://")
        assert "rugmunch" in url


def test_unknown_partner_is_rejected() -> None:
    with pytest.raises(UnknownPartnerError):
        resolve_partner_url("not-a-partner", "code")


@pytest.mark.parametrize(
    "bad_code",
    ["https://evil.example", "//evil.example", "../etc/passwd", "a b", "code?x=1", ""],
)
def test_code_cannot_smuggle_a_url(bad_code: str) -> None:
    with pytest.raises(InvalidAttributionError):
        validate_code(bad_code)


def test_unknown_surface_is_rejected() -> None:
    with pytest.raises(InvalidAttributionError):
        validate_surface("carrier-pigeon")


# ── hashing ──────────────────────────────────────────────────────────────


def test_hash_is_stable_and_does_not_contain_the_raw_identifier() -> None:
    digest = hash_identifier("user-12345")
    assert digest == hash_identifier("user-12345")
    assert digest is not None
    assert "user-12345" not in digest
    assert len(digest) == 32


def test_hash_differs_per_identifier_and_skips_empty() -> None:
    assert hash_identifier("a") != hash_identifier("b")
    assert hash_identifier(None) is None
    assert hash_identifier("   ") is None


def test_hash_salt_changes_the_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFERRAL_HASH_SALT", "salt-one")
    first = hash_identifier("user-1")
    monkeypatch.setenv("REFERRAL_HASH_SALT", "salt-two")
    assert first != hash_identifier("user-1")


# ── endpoints ────────────────────────────────────────────────────────────


def test_click_records_hashed_identifiers(api: tuple[TestClient, FakeStore]) -> None:
    client, store = api
    resp = client.post(
        "/api/v1/referral/click",
        json={
            "partner": "jupiter",
            "code": "rugmunch",
            "surface": "bot",
            "tier": "pro",
            "user_id": "tg:98765",
            "chat_id": "-100200300",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_url"].startswith("https://jup.ag/")
    assert store.rows[0]["surface"] == "bot"
    assert store.rows[0]["tier"] == "pro"
    assert store.rows[0]["referrer_hash"] != "tg:98765"
    assert "tg:98765" not in str(store.rows[0])


def test_click_rejects_unknown_partner(api: tuple[TestClient, FakeStore]) -> None:
    client, store = api
    resp = client.post(
        "/api/v1/referral/click",
        json={"partner": "evil", "code": "x", "surface": "bot"},
    )
    assert resp.status_code == 404
    assert store.rows == []


def test_click_rejects_unknown_surface(api: tuple[TestClient, FakeStore]) -> None:
    client, _ = api
    resp = client.post(
        "/api/v1/referral/click",
        json={"partner": "jupiter", "code": "x", "surface": "email"},
    )
    assert resp.status_code == 422


def test_go_redirects_only_to_allowlisted_host(api: tuple[TestClient, FakeStore]) -> None:
    client, store = api
    resp = client.get(
        "/api/v1/referral/go/gmgn",
        params={"code": "rugmunch", "surface": "scan_quick"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://gmgn.ai/")
    assert resp.headers["cache-control"] == "no-store"
    assert store.rows[0]["surface"] == "scan_quick"


def test_go_rejects_open_redirect_attempts(api: tuple[TestClient, FakeStore]) -> None:
    client, store = api
    assert client.get("/api/v1/referral/go/evil", params={"code": "x"}, follow_redirects=False).status_code == 404
    assert (
        client.get(
            "/api/v1/referral/go/jupiter",
            params={"code": "//evil.example", "surface": "frontend"},
            follow_redirects=False,
        ).status_code
        == 422
    )
    assert store.rows == []


def test_go_still_redirects_when_the_write_fails(api: tuple[TestClient, FakeStore]) -> None:
    client, store = api
    store.fail = True
    resp = client.get("/api/v1/referral/go/padre", params={"code": "crm"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://trade.padre.gg/")


def test_stats_requires_configuration_and_returns_buckets(api: tuple[TestClient, FakeStore]) -> None:
    client, _ = api
    resp = client.get("/api/v1/referral/stats", params={"partner": "jupiter", "days": 7})
    assert resp.status_code == 200
    assert resp.json()["buckets"][0]["partner"] == "jupiter"
