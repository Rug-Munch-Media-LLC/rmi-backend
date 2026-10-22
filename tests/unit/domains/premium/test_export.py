"""Export: one schema, one signature, one body for download and webhook.

The contract is that ``GET /scan/{token}.json`` and the webhook body are the
same object, so these tests pin the parts an integrator codes against: the
schema version string, the required key set, canonical encoding, and the exact
``sha256=<hex>`` signature over the bytes that are actually sent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.domains.premium.export import (
    REQUIRED_KEYS,
    SCHEMA_VERSION,
    SIGNATURE_HEADER,
    encode,
    export_payload,
    sign_body,
    webhook_contract,
)


def _scan() -> dict[str, Any]:
    return {
        "token": "MintA",
        "chain": "solana",
        "safety_score": 42,
        "confidence": 0.61,
        "risk_level": "high",
        "risk_flags": ["mint_authority_active"],
        "price_usd": 0.00012,
        "liquidity_usd": 41_000.0,
        "holder_count": 812,
        "scanned_at": "2026-10-23T00:00:00+00:00",
        "modules_run": ["bundle_graph"],
    }


def _payload() -> dict[str, Any]:
    return export_payload(
        _scan(),
        bundle_graph={"token": "MintA", "confidence": 0},
        rollup={"cohorts": {"team": {"count": 0}}, "kol": []},
        snapshot={"token": "MintA", "chain": "solana"},
        watchers_n=3,
    )


def test_schema_version_is_part_of_the_payload_and_first() -> None:
    payload = _payload()

    assert SCHEMA_VERSION == "premium.scan.v1"
    assert next(iter(payload)) == "schema_version"
    assert payload["schema_version"] == SCHEMA_VERSION


def test_every_required_key_is_present() -> None:
    payload = _payload()

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    assert missing == []


def test_identity_and_risk_are_taken_from_the_scan() -> None:
    payload = _payload()

    assert payload["token"] == "MintA"
    assert payload["chain"] == "solana"
    assert payload["risk"]["safety_score"] == 42
    assert payload["risk"]["risk_flags"] == ["mint_authority_active"]
    assert payload["market"]["liquidity_usd"] == 41_000.0
    assert payload["watch"] == {"watchers_n": 3}


def test_missing_scan_fields_stay_none() -> None:
    payload = export_payload(
        {"token": "MintB"},
        bundle_graph={},
        rollup={},
        snapshot={"token": "MintB", "chain": "solana"},
    )

    assert payload["risk"]["safety_score"] is None
    assert payload["market"]["market_cap_usd"] is None
    assert payload["cohorts"] == {}
    assert payload["kol"] == []
    assert payload["risk"]["risk_flags"] == []


def test_signature_matches_a_reference_hmac_and_is_sha256_prefixed() -> None:
    body = encode(_payload())
    secret = "subscriber-secret"

    signature = sign_body(secret, body)

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"
    assert signature == sign_body(secret, body)


def test_signature_changes_with_body_and_secret() -> None:
    body = encode(_payload())

    assert sign_body("secret-a", body) != sign_body("secret-b", body)
    assert sign_body("secret-a", body) != sign_body("secret-a", body + b" ")


def test_encode_is_canonical_compact_json_and_round_trips() -> None:
    payload = _payload()

    body = encode(payload)

    assert b", " not in body
    assert b'": ' not in body
    assert json.loads(body) == payload


def test_encode_serialises_non_json_native_values_instead_of_raising() -> None:
    body = encode({"schema_version": SCHEMA_VERSION, "when": object})

    assert b"<class 'object'>" in body


def test_webhook_contract_describes_the_same_schema_and_keys() -> None:
    contract = webhook_contract()

    assert contract["schema_version"] == SCHEMA_VERSION
    assert contract["required_keys"] == list(REQUIRED_KEYS)
    assert SIGNATURE_HEADER in contract["headers"]
    assert contract["headers"][SIGNATURE_HEADER].startswith("sha256=")
    assert contract["method"] == "POST"
