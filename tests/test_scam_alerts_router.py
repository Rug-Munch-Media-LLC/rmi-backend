"""Tests for the scam-alerts router (isolated, no app startup, no network).

The router is mounted on a bare FastAPI app. Evidence reads hit the bot's
``shill_evidence`` DB read-only bridge (absent in CI → empty) and the scanner
Redis cache is monkeypatched so the enrichment path is exercised
deterministically. Honesty contract: no fabricated rows — empty evidence →
empty alerts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import scam_alerts as sa
from app.routers.scam_alerts import router

app = FastAPI()
app.include_router(router)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _evidence_row(
    token: str = "So11111111111111111111111111111111111111112",  # noqa: S107 — test fixture, not a secret
    account: str = "shiller_one",
    hours_ago: float = 1.0,
    image_url: str = "/api/v1/media/image/shot1.png",
    ocr: str = "ape in now, 100x incoming",
) -> dict[str, Any]:
    return {
        "id": 1,
        "token": token,
        "chain": "solana",
        "account": account,
        "x_url": "https://x.com/shiller_one/status/1",
        "image_path": "/srv/shots/shot1.png",
        "image_url": image_url,
        "ocr_text": ocr,
        "captured_at": (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(),
    }


def test_active_shape_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/scam-alerts/active")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"alerts", "total", "window_hours", "source"}
    assert isinstance(body["alerts"], list)
    assert body["window_hours"] == 24
    assert body["source"] == "shill_evidence+scanner_cache"


def test_active_groups_evidence_and_links(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _evidence_row(account="shiller_one"),
        _evidence_row(account="shiller_two", hours_ago=2),
        # Old row outside the 24h window → excluded.
        _evidence_row(account="old_shiller", hours_ago=48),
    ]
    monkeypatch.setattr(sa, "read_shill_evidence", lambda **_: rows)
    monkeypatch.setattr(sa, "_scanner_risk", _fake_scanner)

    resp = client.get("/api/v1/scam-alerts/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    alert = body["alerts"][0]
    assert alert["token"] == "So11111111111111111111111111111111111111112"
    assert alert["chain"] == "solana"
    assert alert["accounts_count"] == 2
    assert alert["mentions_count"] == 2
    handles = {k["handle"] for k in alert["shilling_kols"]}
    assert handles == {"shiller_one", "shiller_two"}
    # Scanner risk flags surface as scam reasons + the coordination reason.
    assert "honeypot_detected" in alert["scam_reasons"]
    assert any("coordinated shill: 2 accounts" in r for r in alert["scam_reasons"])
    assert alert["risk_score"] == 92.0
    assert alert["screenshot_urls"] == ["/api/v1/media/image/shot1.png"]
    assert alert["ocr_excerpt"]
    assert "100x" in alert["ocr_excerpt"]
    assert alert["links"]["kol_profile"] == "/kol/shiller_one"
    assert alert["links"]["campaign"] == "/wall-of-shame"
    assert alert["links"]["token"] == "/scanner"


def test_active_derived_score_without_scanner(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa, "read_shill_evidence", lambda **_: [_evidence_row()])
    monkeypatch.setattr(sa, "_scanner_risk", _fake_scanner_none)

    resp = client.get("/api/v1/scam-alerts/active")
    alert = resp.json()["alerts"][0]
    # Derived score: 1 account * 10 + 1 mention = 11 → low band.
    assert alert["risk_score"] == 11.0
    assert alert["risk_level"] == "low"
    # Only the honest coordination reason (no fabricated scanner flags).
    assert len(alert["scam_reasons"]) == 1
    assert "coordinated shill" in alert["scam_reasons"][0]


def test_active_ticker_token_skips_scanner_cache(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sa, "read_shill_evidence", lambda **_: [_evidence_row(token="$SCAM")])
    called: list[tuple[str, str]] = []

    async def _spy(chain: str, address: str) -> None:
        called.append((chain, address))
        return None

    import app.routers.scanner_data_router as sdr

    monkeypatch.setattr(sdr, "_read_cache", _spy)
    resp = client.get("/api/v1/scam-alerts/active")
    assert resp.status_code == 200
    assert called == []  # ticker → scanner cache never consulted


async def _fake_scanner(chain: str, token: str) -> dict[str, Any] | None:
    return {
        "risk_score": 92.0,
        "risk_level": "high",
        "risk_flags": ["honeypot_detected", "mint_authority_enabled"],
        "scanned_at": "2026-08-31T00:00:00+00:00",
    }


async def _fake_scanner_none(chain: str, token: str) -> dict[str, Any] | None:
    return None
