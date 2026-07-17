"""x402 unit test fixtures — fakeredis, HTTP mocking, facilitator routing, request factory."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis import FakeStrictRedis
from fastapi import Request


@pytest.fixture(scope="session", autouse=True)
def _set_x402_env() -> None:
    os.environ.setdefault("X402_EVM_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9")
    os.environ.setdefault("X402_SOL_PAY_TO", "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv")
    os.environ["ENVIRONMENT"] = "test"


@pytest.fixture
def fake_redis() -> FakeStrictRedis:
    return FakeStrictRedis()


@pytest.fixture
def empty_redis() -> FakeStrictRedis:
    return FakeStrictRedis()


@pytest.fixture
def redis_with_trial_used() -> FakeStrictRedis:
    r = FakeStrictRedis()
    r.setex("x402:trial:test_client:wallet_scan", 86400, "3")
    r.setex("x402:trial:test_client:audit", 86400, "1")
    r.set("x402:spent_tx:0xabc123def456", "1", ex=86400)
    r.set("x402:spent_tx:0xdef456abc789", "1", ex=86400)
    return r


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    mock_response = AsyncMock(spec=["status_code", "json"])
    mock_response.status_code = 200
    mock_response.json = AsyncMock(return_value={"status": "ok", "hash": "0xabc123def456"})
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__.return_value = mock_client
    return mock_client


@pytest.fixture
def mock_facilitator_router() -> MagicMock:
    from app.domains.billing.facilitators.base import VerificationResult

    router = MagicMock()
    router.verify = AsyncMock(
        return_value=VerificationResult(
            verified=True,
            reason="Mock verification ok",
            tx_hash="0xmock",
            payer="0xMockPayer",
            amount="10000",
            chain="base",
            token="USDC",
            facilitator="mock",
        )
    )
    return router


@pytest.fixture
def make_request() -> Callable[..., Request]:
    def _build(
        method: str = "POST",
        path: str = "/api/v1/x402-tools/wallet_scan",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Request:
        hdrs = headers or {}
        scope: dict[str, Any] = {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 54321),
            "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
        }
        return Request(scope, receive=lambda: {"type": "http.request", "body": body})

    return _build
