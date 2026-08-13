"""Regression tests: x402 payment enforcement MUST fail closed (audit F-01/F-03).

Covers:
1. DataBus ``verify_x402_payment``: garbage / missing / erroring payment
   proofs are rejected (PaymentRequiredError / 402) — never ``paid: True``.
2. Valid payment flow still works.
3. Advanced + premium routers at /api/v1/x402/* reject unauthenticated
   requests (fail-closed gate), and the explicit preview/admin escapes work.

Pure tests: the verifier is monkeypatched; no network or Redis required.
"""

from __future__ import annotations

import base64
import json

import pytest
from starlette.requests import Request

from app.config import settings
from app.core.errors import PaymentRequiredError
from app.routers.x402_advanced_tools import gate as advanced_gate, router as advanced_router
from app.routers.x402_databus_tools import auth as databus_auth
from app.routers.x402_premium_tools import router as premium_router

# ── helpers ──────────────────────────────────────────────────────────


def _make_request(headers: dict[str, str], path: str = "/api/v1/x402-databus/token-price") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def _base64_payload() -> str:
    """A structurally-valid x402 v2 payload (base64 JSON)."""
    payload = {
        "x402Version": 2,
        "accepted": {
            "network": "eip155:8453",
            "amount": "10000",
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "payTo": "0x1E3AC01d0fdb976179790BDD02823196A92705C9",
        },
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _patch_verifier(monkeypatch, fn):
    """Replace the lazily-imported verify_payment used by databus auth."""
    monkeypatch.setattr("app.routers.x402_enforcement.verify_payment", fn)


# ── DataBus fail-closed (F-01) ───────────────────────────────────────


@pytest.mark.anyio
async def test_databus_garbage_payment_header_rejected():
    """X-PAYMENT: garbage must NOT buy access (old code: paid=True)."""
    req = _make_request({"X-PAYMENT": "garbage"})
    with pytest.raises(PaymentRequiredError):
        await databus_auth.verify_x402_payment(req, "token_price", 0.01)


@pytest.mark.anyio
async def test_databus_verifier_exception_fails_closed(monkeypatch):
    """Verifier raising (network failure / TypeError) must NOT yield paid=True."""

    async def _boom(payload: dict) -> dict:
        raise RuntimeError("verifier down")

    _patch_verifier(monkeypatch, _boom)
    req = _make_request({"X-PAYMENT": _base64_payload()})
    with pytest.raises(PaymentRequiredError):
        await databus_auth.verify_x402_payment(req, "token_price", 0.01)


@pytest.mark.anyio
async def test_databus_verifier_unverified_fails_closed(monkeypatch):
    """Verifier returning verified=False must be rejected (402)."""

    async def _unverified(payload: dict) -> dict:
        return {"verified": False, "reason": "not paid on-chain"}

    _patch_verifier(monkeypatch, _unverified)
    req = _make_request({"X-PAYMENT": _base64_payload()})
    with pytest.raises(PaymentRequiredError):
        await databus_auth.verify_x402_payment(req, "token_price", 0.01)


@pytest.mark.anyio
async def test_databus_missing_payment_header_rejected(monkeypatch):
    """No X-PAYMENT and no trial left -> 402."""

    async def _no_trial(*args, **kwargs):
        return {"allowed": False, "remaining": 0, "reason": "trials_exhausted"}

    monkeypatch.setattr(databus_auth, "check_trial", _no_trial)
    req = _make_request({})
    with pytest.raises(PaymentRequiredError):
        await databus_auth.verify_x402_payment(req, "token_price", 0.01)


@pytest.mark.anyio
async def test_databus_valid_payment_still_works(monkeypatch):
    """A verified proof still returns paid=True (valid flow preserved)."""

    async def _verified(payload: dict) -> dict:
        return {"verified": True, "tx_hash": "0xdeadbeef"}

    _patch_verifier(monkeypatch, _verified)
    req = _make_request({"X-PAYMENT": _base64_payload()})
    result = await databus_auth.verify_x402_payment(req, "token_price", 0.01)
    assert result["paid"] is True
    assert result["tx"] == "0xdeadbeef"
    assert result["amount"] == 0.01


@pytest.mark.anyio
async def test_databus_offline_mode_only_when_explicit(monkeypatch):
    """pry_x402_offline is the ONLY documented path that bypasses verification."""
    monkeypatch.setattr(settings, "pry_x402_offline", True)
    try:
        req = _make_request({"X-PAYMENT": "anything"})
        result = await databus_auth.verify_x402_payment(req, "token_price", 0.01)
        assert result["paid"] is True
        assert result["tx"] == "x402_offline_mode"
    finally:
        monkeypatch.setattr(settings, "pry_x402_offline", False)


# ── Advanced/premium gate (F-03) ─────────────────────────────────────


@pytest.mark.anyio
async def test_advanced_gate_rejects_unauthenticated(monkeypatch):
    """No payment, no admin key -> 402."""
    monkeypatch.setattr(advanced_gate, "X402_ADVANCED_TOOLS_OPEN", False)
    req = _make_request({}, path="/api/v1/x402/rug_probability")
    with pytest.raises(PaymentRequiredError):
        await advanced_gate.require_x402_advanced_payment(req)


@pytest.mark.anyio
async def test_advanced_gate_rejects_garbage_header(monkeypatch):
    """A garbage X-PAYMENT header must not pass the gate."""
    monkeypatch.setattr(advanced_gate, "X402_ADVANCED_TOOLS_OPEN", False)
    req = _make_request({"X-PAYMENT": "garbage"}, path="/api/v1/x402/rug_probability")
    with pytest.raises(PaymentRequiredError):
        await advanced_gate.require_x402_advanced_payment(req)


@pytest.mark.anyio
async def test_advanced_gate_accepts_admin_key(monkeypatch):
    """Explicit admin API key auth is honored."""
    monkeypatch.setattr(advanced_gate, "X402_ADVANCED_TOOLS_OPEN", False)
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key-32-chars-long!!!")
    req = _make_request({"X-Api-Key": "test-admin-key-32-chars-long!!!"}, path="/api/v1/x402/cache/clear")
    result = await advanced_gate.require_x402_advanced_payment(req)
    assert result["tier"] == "admin"
    assert result["tool"] == "clear"


@pytest.mark.anyio
async def test_advanced_gate_admin_key_wrong_rejected(monkeypatch):
    """A wrong admin key must NOT grant access."""
    monkeypatch.setattr(advanced_gate, "X402_ADVANCED_TOOLS_OPEN", False)
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key-32-chars-long!!!")
    req = _make_request({"X-Api-Key": "not-the-key"}, path="/api/v1/x402/rug_probability")
    with pytest.raises(PaymentRequiredError):
        await advanced_gate.require_x402_advanced_payment(req)


@pytest.mark.anyio
async def test_advanced_gate_preview_mode_is_explicit_constant(monkeypatch):
    """X402_ADVANCED_TOOLS_OPEN=1 is the documented free/preview decision."""
    monkeypatch.setattr(advanced_gate, "X402_ADVANCED_TOOLS_OPEN", True)
    req = _make_request({}, path="/api/v1/x402/rug_probability")
    result = await advanced_gate.require_x402_advanced_payment(req)
    assert result["tier"] == "preview"


def test_advanced_and_premium_routers_are_gated():
    """Both routers carry the fail-closed dependency at router level."""
    for router in (advanced_router, premium_router):
        assert router.dependencies, f"router missing payment gate: {router}"
        assert any(
            getattr(d, "dependency", None) is advanced_gate.require_x402_advanced_payment for d in router.dependencies
        )


# ── HTTP-level integration (wiring proof) ────────────────────────────


def _mini_app():
    from fastapi import FastAPI

    from app.error_handlers import register_error_handlers

    app = FastAPI()
    app.include_router(advanced_router, prefix="/api/v1/x402")
    app.include_router(premium_router, prefix="/api/v1/x402")
    app.include_router(databus_auth_router())
    register_error_handlers(app)
    return app


def databus_auth_router():
    # Local import to avoid pulling the full databus tool modules into the
    # mini-app for the databus HTTP test.
    from app.routers.x402_databus_tools import router as dbus_router

    return dbus_router


def test_http_advanced_tools_reject_unauthenticated():
    from fastapi.testclient import TestClient

    client = TestClient(_mini_app(), raise_server_exceptions=False)
    resp = client.post("/api/v1/x402/rug_probability", json={"address": "0x123", "chain": "base"})
    assert resp.status_code == 402


def test_http_premium_tools_reject_unauthenticated():
    from fastapi.testclient import TestClient

    client = TestClient(_mini_app(), raise_server_exceptions=False)
    resp = client.post("/api/v1/x402/reputation_score", json={"address": "0x123", "chain": "base"})
    assert resp.status_code == 402


def test_http_databus_garbage_header_returns_402():
    from fastapi.testclient import TestClient

    client = TestClient(_mini_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/x402-databus/token-price",
        json={"mint": "0x123"},
        headers={"X-PAYMENT": "garbage"},
    )
    assert resp.status_code == 402
