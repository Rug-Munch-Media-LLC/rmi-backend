"""Public API contract: tiers, quota enforcement, cache headers, redaction."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.public_api.quota import PublicQuotaMiddleware
from app.domains.public_api.router import get_scan_source, router
from app.domains.public_api.tiers import TIERS, resolve_tier

if TYPE_CHECKING:
    from collections.abc import Mapping

SCAN = "/api/v1/public/scans/solana/So11111111111111111111111111111111111111112"
CACHED: dict[str, Any] = {
    "address": "So11111111111111111111111111111111111111112",
    "chain": "solana",
    "safety_score": 41,
    "risk_level": "medium",
    "flags": [{"name": "mint_authority_active", "evidence": {"wallets": ["secret"]}}],
    "holders": [{"wallet": "leaky", "pct": 12.5}],
    "metrics": {"holders": 1543, "raw_payload": {"nested": True}},
}


class FakeSource:
    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.payload: Mapping[str, Any] | None = CACHED if payload is None else payload
        self.calls: list[tuple[str, str]] = []

    async def read(self, chain: str, address: str) -> Mapping[str, Any] | None:
        self.calls.append((chain, address))
        return self.payload


class EmptySource(FakeSource):
    """No cached scan for the token."""

    def __init__(self) -> None:
        super().__init__(payload={})
        self.payload = None


# Each test gets its own source address, so one test's quota spend never
# throttles the next one without inventing a production bypass header.
_ips = iter(range(10, 250))


def _client(source: FakeSource) -> TestClient:
    app = FastAPI()
    app.add_middleware(PublicQuotaMiddleware)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_scan_source] = lambda: source
    return TestClient(app, client=(f"203.0.113.{next(_ips)}", 12345))


def test_tier_table_is_ordered_and_complete() -> None:
    assert set(TIERS) == {"anonymous", "free", "paid"}
    limits = [TIERS[name].requests_per_min for name in ("anonymous", "free", "paid")]
    assert limits == sorted(limits)
    assert TIERS["anonymous"].scan_depth == "summary"
    assert TIERS["paid"].scan_depth == "full"


def test_resolve_tier_maps_plans_and_never_upgrades_anonymous() -> None:
    assert resolve_tier("paid", authenticated=False).name == "anonymous"
    assert resolve_tier("paid", authenticated=True).name == "paid"
    assert resolve_tier(None, authenticated=True).name == "free"
    assert resolve_tier("mystery", authenticated=True).name == "free"


def test_public_read_is_redacted() -> None:
    body = _client(FakeSource()).get(SCAN).json()

    assert body["safety_score"] == 41
    assert body["flags"] == ["mint_authority_active"]
    assert "holders" not in body
    assert "evidence" not in json.dumps(body)
    assert "secret" not in json.dumps(body)


def test_cache_headers_follow_the_tier() -> None:
    response = _client(FakeSource()).get(SCAN)
    assert response.status_code == 200
    assert "max-age=30" in response.headers["cache-control"]
    assert "stale-while-revalidate=300" in response.headers["cache-control"]
    assert response.headers["etag"].startswith('W/"')


def test_fresh_bypasses_the_client_cache() -> None:
    response = _client(FakeSource()).get(SCAN, params={"fresh": "true"})
    assert response.headers["cache-control"] == "no-store"


def test_matching_etag_returns_304() -> None:
    client = _client(FakeSource())
    etag = client.get(SCAN).headers["etag"]

    cached = client.get(SCAN, headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.headers["etag"] == etag


def test_missing_scan_is_404() -> None:
    assert _client(EmptySource()).get(SCAN).status_code == 404


def test_anonymous_quota_is_enforced_with_typed_429() -> None:
    client = _client(FakeSource())
    limit = TIERS["anonymous"].requests_per_min
    responses = [client.get("/api/v1/public/tiers") for _ in range(limit + 3)]

    assert [r.status_code for r in responses[:limit]] == [200] * limit
    response = responses[-1]
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["x-ratelimit-tier"] == "anonymous"
    assert "x-ratelimit-remaining" in response.headers
    assert response.json()["status"] == 429
    assert response.json()["title"] == "Too Many Requests"


def test_paid_key_gets_a_larger_allowance() -> None:
    client = _client(FakeSource())
    client.headers.update({"X-API-Key": "paid-key-1", "X-Plan": "paid"})
    anonymous_limit = TIERS["anonymous"].requests_per_min

    statuses = [client.get("/api/v1/public/tiers").status_code for _ in range(anonymous_limit + 5)]
    assert set(statuses) == {200}
    assert client.get("/api/v1/public/tiers").headers["x-ratelimit-tier"] == "paid"


def test_tiers_endpoint_publishes_the_table() -> None:
    body = _client(FakeSource()).get("/api/v1/public/tiers").json()
    assert body["paid"]["requests_per_min"] == TIERS["paid"].requests_per_min


def test_non_public_routes_are_untouched() -> None:
    app = FastAPI()
    app.add_middleware(PublicQuotaMiddleware)

    @app.get("/api/v1/other/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get("/api/v1/other/ping")
    assert response.status_code == 200
    assert "x-ratelimit-limit" not in response.headers
