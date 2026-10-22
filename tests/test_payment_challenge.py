"""Every 402 must carry a payable x402 challenge."""

from __future__ import annotations

import base64
import json

from app.core.payment_challenge import payment_challenge


class _Req:
    class url:  # noqa: N801 — minimal request stand-in
        path = "/api/v1/x402/rug_probability"


def test_challenge_has_every_required_field():
    extra, _headers = payment_challenge(
        _Req(), type("E", (), {"context": {"price_usd": 0.05, "tool_id": "rug_probability"}})()
    )
    accept = extra["accepts"][0]
    assert accept["scheme"] == "exact"
    assert accept["network"] == "eip155:8453"
    assert accept["asset"].lower().startswith("0x833589")
    assert accept["payTo"].startswith("0x")
    assert accept["maxAmountRequired"] == "50000"
    assert accept["resource"].endswith("/api/v1/x402/rug_probability")
    assert extra["x402Version"] == 1


def test_challenge_header_is_base64_json():
    _, headers = payment_challenge(_Req(), type("E", (), {"context": {"price_atoms": 10000}})())
    decoded = json.loads(base64.b64decode(headers["PAYMENT-REQUIRED"]))
    assert decoded["accepts"][0]["maxAmountRequired"] == "10000"
