"""Tests for the KOL social-intelligence router (isolated, no DB).

The router is mounted on a bare FastAPI app — no app startup, no database,
no network. Pure databus functions run against the bundled JSON files; the
only thing mocked is nothing (Redis writes are avoided via ``persist=False``
inside the router itself).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.databus import kol_scoring as ks
from app.routers.kol_intel import router

if TYPE_CHECKING:
    from collections.abc import Generator

app = FastAPI()
app.include_router(router)


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    """Keep the in-process KOL scoring registry hermetic between tests."""
    ks.clear_registry()
    yield
    ks.clear_registry()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_health_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "scammers", "wallets"}
    assert body["status"] == "ok"
    assert isinstance(body["scammers"], int)
    assert body["scammers"] > 0
    assert isinstance(body["wallets"], int)
    assert body["wallets"] > 0


def test_scammers_list_non_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/scammers")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["scammers"], list)
    assert len(body["scammers"]) > 0
    assert body["total"] == len(body["scammers"])
    assert body["source"] == "known_scammers"
    # Every record carries a handle
    assert all(r.get("handle") for r in body["scammers"])


def test_scammers_severity_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/scammers", params={"severity": "high"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["scammers"]) > 0
    assert all(r.get("severity") == "high" for r in body["scammers"])

    # Case-insensitive
    resp = client.get("/api/v1/kol/scammers", params={"severity": "HIGH"})
    assert resp.status_code == 200
    assert resp.json()["total"] == body["total"]


def test_scammers_unknown_severity_returns_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/scammers", params={"severity": "critical"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scammers"] == []
    assert body["total"] == 0


def test_unknown_handle_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/kol/definitely_not_a_real_kol_xyz")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"error", "detail"}
    assert body["error"] == "not_found"
    assert "not found" in body["detail"]


def test_empty_handle_returns_400(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/kol/@")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "invalid_handle"


def test_kol_profile_known_wallet_handle(client: TestClient) -> None:
    # "profit" exists in verified_kol_wallets.json (bundled data, no network)
    resp = client.get("/api/v1/kol/kol/profit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["handle"] == "profit"
    assert body["found"] is True
    # score composed by kol_scoring.score_kol
    assert isinstance(body["score"]["score"], (int, float))
    assert body["score"]["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
    # rug history composed by kol_scoring.get_kol_rug_history
    assert isinstance(body["rug_history"]["rugs"], list)
    # wallets composed from the verified wallet list
    assert isinstance(body["wallets"], list)
    assert len(body["wallets"]) > 0


def test_kol_profile_registry_handle(client: TestClient) -> None:
    # "SahilArora0" is a known scammer (known_scammers.json) — a flagged
    # scammer is still a known KOL, so the profile resolves with a floored score.
    resp = client.get("/api/v1/kol/kol/SahilArora0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["handle"] == "sahilarora0"
    assert body["found"] is True
    assert isinstance(body["score"]["score"], (int, float))
    assert body["score"]["score"] <= 50  # scammer penalty floors the score


def test_leaderboard_returns_scored_kols(client: TestClient) -> None:
    ks.register_known_scammer("ScamLord")
    ks.score_kol("ScamLord", persist=False)
    resp = client.get("/api/v1/kol/leaderboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_tracked"] >= 1
    assert any(entry["handle"] == "scamlord" for entry in body["leaderboard"])

    # limit respected and clamped by Query(ge=1, le=200)
    resp = client.get("/api/v1/kol/leaderboard", params={"limit": 5})
    assert resp.status_code == 200
    assert len(resp.json()["leaderboard"]) <= 5


def test_wallets_known_and_unknown(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/wallets/profit")
    assert resp.status_code == 200
    assert resp.json()["total"] > 0

    # Unknown handle → 200 with empty list (graceful, not 404)
    resp = client.get("/api/v1/kol/wallets/definitely_not_a_real_kol_xyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wallets"] == []
    assert body["total"] == 0


def test_cross_chain_shape(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/cross-chain/profit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["handle"] == "profit"
    assert set(body.keys()) == {"handle", "links", "total", "source"}
    assert isinstance(body["links"], list)
    assert body["total"] == len(body["links"])


def test_shill_signals_safe_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/shill-signals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["alerts"] == []
    assert body["source"] == "shill_detector"


def test_profiles_list_shape(client: TestClient) -> None:
    """Enriched profile list: total + rated counts, entries carry the summary shape."""
    resp = client.get("/api/v1/kol/profiles", params={"limit": 25})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 100  # expanded dataset (registry + wallet handles + curated)
    assert body["rated"] > 0  # rug-history/scammer figures have computed ratings
    assert len(body["profiles"]) == 25
    for p in body["profiles"]:
        assert p["handle"]
        assert p["display_name"]
        assert "avatar_url" in p
        assert "wallet_count" in p
        assert "calls_count" in p
        assert "rating" in p  # null when insufficient data — never omitted
        assert "rating_grade" in p


def test_profiles_category_filter(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/profiles", params={"category": "trader", "limit": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert body["category"] == "trader"
    assert all(p["category"] == "trader" for p in body["profiles"])


def test_profiles_detail_curated(client: TestClient) -> None:
    """Curated famous figure resolves with calls, performance and risk."""
    resp = client.get("/api/v1/kol/profiles/blknoiz06")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["profile"]["display_name"] == "Ansem"
    assert len(body["profile"]["calls"]) >= 1
    perf = body["profile"]["performance"]
    assert isinstance(perf["score"], (int, float))
    assert perf["grade"] in {"A", "B", "C", "D", "F"}
    assert body["profile"]["risk"]["known_scammer"] is True
    assert "wallet_count" in body


def test_profiles_detail_wallet_only_fallback(client: TestClient) -> None:
    """Handles present only in the wallet list resolve with a minimal profile."""
    resp = client.get("/api/v1/kol/profiles/profit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["wallet_count"] > 0
    assert body["profile"]["performance"]["score"] is None  # withheld, not guessed


def test_profiles_detail_unknown_404(client: TestClient) -> None:
    resp = client.get("/api/v1/kol/profiles/definitely_not_a_real_kol_xyz")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "not_found"


def test_leaderboard_entries_enriched(client: TestClient) -> None:
    """Leaderboard entries gain additive profile fields (avatar/category/wallet_count)."""
    ks.register_known_scammer("ProfiledScam")
    ks.score_kol("ProfiledScam", persist=False)
    resp = client.get("/api/v1/kol/leaderboard")
    assert resp.status_code == 200
    entries = resp.json()["leaderboard"]
    assert any(e["handle"] == "profiledscam" for e in entries)
    for e in entries:
        assert "wallet_count" in e


def test_kol_profile_has_profile_subobject(client: TestClient) -> None:
    """The existing /kol/{handle} profile now also carries enriched metadata."""
    resp = client.get("/api/v1/kol/kol/blknoiz06")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"] is not None
    assert body["profile"]["display_name"] == "Ansem"
    assert "wallet_count" in body["profile"]
