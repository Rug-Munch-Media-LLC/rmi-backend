"""HTTP tests for /api/v1/poly/attribution using an injected fake session."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.poly_attribution import router
from app.core.database import get_db
from app.domains.poly_attribution.hashing import hash_identifier

SALT = "test-salt"
PARTNER_KEY = "partner-secret"
IDEM = "idem-abc"


class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._row

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    """Minimal async session: remembers inserted rows, replays for idempotency."""

    def __init__(self, all_rows: list[Any] | None = None) -> None:
        self.added: list[Any] = []
        self.all_rows = all_rows or []

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(row=self.added[0] if self.added else None, rows=self.all_rows)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _client(session: _Session) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override() -> Any:
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure salt + allowlist per test (hash needs the salt at call time)."""
    monkeypatch.setenv("POLY_ATTRIBUTION_SALT", SALT)
    allowlist = {"acme": {"campaigns": ["launch"], "key_hash": hash_identifier(PARTNER_KEY)}}
    monkeypatch.setenv("POLY_ATTRIBUTION_ALLOWLIST", json.dumps(allowlist))
    monkeypatch.delenv("POLY_ATTRIBUTION_RATES", raising=False)


def _body(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "partner": "acme",
        "campaign": "launch",
        "surface": "frontend",
        "event": "click",
        "market_id": "will-btc-100k",
        "wallet": "0xAbC0000000000000000000000000000000000001",
        "builder_code": "rmi-42",
        "value_usd": "12.5",
    }
    payload.update(over)
    return payload


def _headers(key: str = PARTNER_KEY, idem: str = IDEM) -> dict[str, str]:
    return {"X-Partner-Key": key, "Idempotency-Key": idem}


def test_ingest_hashes_wallet_and_never_echoes_raw() -> None:
    resp = _client(_Session()).post("/api/v1/poly/attribution/events", json=_body(), headers=_headers())
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["duplicate"] is False
    assert data["wallet_hash"] == hash_identifier("0xAbC0000000000000000000000000000000000001")
    assert "0xAbC" not in resp.text
    assert data["builder_code_hash"] is not None
    assert Decimal(data["value_usd"]) == Decimal("12.5")


def test_ingest_replay_is_idempotent() -> None:
    session = _Session()
    client = _client(session)
    first = client.post("/api/v1/poly/attribution/events", json=_body(), headers=_headers())
    second = client.post("/api/v1/poly/attribution/events", json=_body(), headers=_headers())
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["data"]["duplicate"] is True
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


def test_ingest_conflict_on_changed_payload() -> None:
    session = _Session()
    client = _client(session)
    client.post("/api/v1/poly/attribution/events", json=_body(), headers=_headers())
    changed = client.post(
        "/api/v1/poly/attribution/events",
        json=_body(value_usd="99"),
        headers=_headers(),
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_ingest_rejects_non_allowlisted_campaign() -> None:
    resp = _client(_Session()).post(
        "/api/v1/poly/attribution/events",
        json=_body(campaign="private"),
        headers=_headers(),
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "NOT_ALLOWED"


def test_ingest_rejects_bad_partner_key() -> None:
    resp = _client(_Session()).post("/api/v1/poly/attribution/events", json=_body(), headers=_headers(key="wrong"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "BAD_PARTNER_KEY"


def test_ingest_requires_idempotency_key() -> None:
    resp = _client(_Session()).post(
        "/api/v1/poly/attribution/events",
        json=_body(),
        headers={"X-Partner-Key": PARTNER_KEY},
    )
    assert resp.status_code == 422


def test_ingest_rejects_unknown_surface() -> None:
    resp = _client(_Session()).post(
        "/api/v1/poly/attribution/events",
        json=_body(surface="sidebar"),
        headers=_headers(),
    )
    assert resp.status_code == 422


def test_earnings_null_when_no_rate_card() -> None:
    session = _Session(all_rows=[("signup", 2, Decimal("100"))])
    resp = _client(session).get("/api/v1/poly/attribution/earnings/acme")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["earnings_usd"] is None
    assert data["reason"] == "no_rate_card_configured"
    assert data["events"] == 2
    assert data["gross_value_usd"] == "100.000000"


def test_earnings_computed_from_configured_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POLY_ATTRIBUTION_RATES",
        json.dumps({"acme": {"revshare_pct": 40, "cpa": {"signup": 2.5}}}),
    )
    session = _Session(all_rows=[("signup", 2, Decimal("100"))])
    resp = _client(session).get("/api/v1/poly/attribution/earnings/acme?days=7")
    data = resp.json()["data"]
    assert data["earnings_usd"] == "45.000000"
    assert data["reason"] is None
    assert data["window_days"] == 7


def test_earnings_rejects_non_allowlisted_partner() -> None:
    resp = _client(_Session()).get("/api/v1/poly/attribution/earnings/globex")
    assert resp.status_code == 403
