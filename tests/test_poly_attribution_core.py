"""Unit tests: hashing, allowlist, rate limiting, ledger idempotency, earnings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.domains.poly_attribution.allowlist import is_allowed, load_allowlist
from app.domains.poly_attribution.contract import EventInput
from app.domains.poly_attribution.earnings import estimate_earnings
from app.domains.poly_attribution.hashing import MissingSaltError, hash_identifier
from app.domains.poly_attribution.ledger import IdempotencyConflict, record_event
from app.domains.poly_attribution.rate_limit import SlidingWindowLimiter


def _event(**over: Any) -> EventInput:
    base: dict[str, Any] = {
        "partner": "acme",
        "campaign": "launch",
        "surface": "scan",
        "event": "click",
        "market_id": "0xabc",
        "wallet_hash": "a" * 64,
        "builder_code_hash": None,
        "idem_key": "idem-1",
        "ts": datetime(2026, 10, 24, tzinfo=UTC),
        "value_usd": Decimal("10"),
    }
    base.update(over)
    return EventInput(**base)


# ── hashing ──────────────────────────────────────────────────────────
def test_hash_identifier_is_salted_and_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLY_ATTRIBUTION_SALT", "salt-a")
    first = hash_identifier("0xABC")
    assert first == hash_identifier("0xabc")
    assert len(first) == 64
    monkeypatch.setenv("POLY_ATTRIBUTION_SALT", "salt-b")
    assert hash_identifier("0xabc") != first


def test_hash_identifier_requires_salt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLY_ATTRIBUTION_SALT", raising=False)
    with pytest.raises(MissingSaltError):
        hash_identifier("0xabc")


# ── allowlist ────────────────────────────────────────────────────────
def test_allowlist_denies_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLY_ATTRIBUTION_ALLOWLIST", raising=False)
    assert load_allowlist() == {}
    assert is_allowed("acme", "launch") is False


def test_allowlist_denies_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLY_ATTRIBUTION_ALLOWLIST", "{not-json")
    assert is_allowed("acme", "launch") is False


def test_allowlist_scopes_campaigns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POLY_ATTRIBUTION_ALLOWLIST",
        json.dumps({"acme": {"campaigns": ["launch"], "key_hash": "k"}}),
    )
    assert is_allowed("acme", "launch") is True
    assert is_allowed("acme", "other") is False
    assert is_allowed("globex", "launch") is False


# ── rate limit ───────────────────────────────────────────────────────
def test_limiter_allows_then_blocks() -> None:
    limiter = SlidingWindowLimiter(limit=2, window_s=60)
    assert limiter.check("acme")[0] is True
    assert limiter.check("acme")[0] is True
    allowed, remaining, retry_after = limiter.check("acme")
    assert allowed is False
    assert remaining == 0
    assert retry_after >= 1


def test_limiter_is_per_key() -> None:
    limiter = SlidingWindowLimiter(limit=1, window_s=60)
    assert limiter.check("acme")[0] is True
    assert limiter.check("globex")[0] is True


# ── earnings ─────────────────────────────────────────────────────────
def test_earnings_null_without_rate_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLY_ATTRIBUTION_RATES", raising=False)
    out = estimate_earnings("acme", {"signup": 3}, Decimal("100"))
    assert out["earnings_usd"] is None
    assert out["reason"] == "no_rate_card_configured"


def test_earnings_null_when_card_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLY_ATTRIBUTION_RATES", json.dumps({"acme": {"note": "tbd"}}))
    out = estimate_earnings("acme", {"signup": 3}, Decimal("100"))
    assert out["earnings_usd"] is None
    assert out["reason"] == "rate_card_incomplete"


def test_earnings_from_configured_revshare_and_cpa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "POLY_ATTRIBUTION_RATES",
        json.dumps({"acme": {"revshare_pct": 40, "cpa": {"signup": 2.5}}}),
    )
    out = estimate_earnings("acme", {"signup": 2}, Decimal("100"))
    assert out["earnings_usd"] == "45.000000"
    assert out["reason"] is None


# ── ledger idempotency (fake session, no database) ───────────────────
class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.rolled_back = False

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(getattr(self, "existing", None))

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_record_event_creates_once() -> None:
    session = _FakeSession()
    row, status = await record_event(session, _event())  # type: ignore[arg-type]
    assert status == "created"
    assert row.wallet_hash == "a" * 64
    assert row.value_usd == Decimal("10")
    assert len(session.rows) == 1


@pytest.mark.asyncio
async def test_record_event_replays_as_duplicate() -> None:
    existing = type("Row", (), {"fingerprint": _event().fingerprint()})()
    session = _FakeSession()
    session.existing = existing
    row, status = await record_event(session, _event())  # type: ignore[arg-type]
    assert status == "duplicate"
    assert row is existing
    assert session.rows == []


@pytest.mark.asyncio
async def test_record_event_conflict_on_payload_change() -> None:
    existing = type("Row", (), {"fingerprint": _event(value_usd=Decimal("99")).fingerprint()})()
    session = _FakeSession()
    session.existing = existing
    with pytest.raises(IdempotencyConflict):
        await record_event(session, _event())  # type: ignore[arg-type]


def test_event_fingerprint_covers_payload_not_idempotency_key() -> None:
    assert _event().fingerprint() == _event().fingerprint()
    assert _event().fingerprint() == _event(idem_key="idem-2").fingerprint()
    assert _event().fingerprint() != _event(value_usd=Decimal("99")).fingerprint()
