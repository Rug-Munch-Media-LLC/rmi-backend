"""Purchase, duplicate, entitlement and expiry behaviour against real tables."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.domains.boost.create import DuplicateBoost, create_boost
from app.domains.boost.entitlement import (
    EntitlementCheck,
    EntitlementDenied,
    verify_entitlement,
)
from app.domains.boost.expiry import expire_due_boosts
from app.domains.boost.schemas import BoostCreateRequest
from app.models.boost import BoostEvent, BoostPurchase
from tests.unit.domains.boost.helpers import EVM_TOKEN, NOW, SOL_TOKEN, RecordingVerifier


def _request(**overrides):
    payload = {
        "chain": "solana",
        "token_address": SOL_TOKEN,
        "plan_id": "24h",
        "payment_ref": "ref-" + "x" * 20,
    }
    payload.update(overrides)
    return BoostCreateRequest(**payload)


async def test_create_activates_24h_window_and_audits(session, verifier) -> None:
    boost = await create_boost(session, _request(), verifier, now=NOW)

    assert boost.status == "active"
    assert boost.entitlement_verified is True
    assert boost.starts_at == NOW
    assert boost.ends_at == NOW + timedelta(hours=24)
    assert boost.currency == "USD"
    assert boost.price_amount == 49

    events = (
        (await session.execute(select(BoostEvent.event).where(BoostEvent.boost_id == boost.id).order_by(BoostEvent.id)))
        .scalars()
        .all()
    )
    assert events == ["created", "activated"]
    assert verifier.calls[0].amount == 49


async def test_denied_entitlement_persists_nothing(session, denying_verifier) -> None:
    with pytest.raises(EntitlementDenied):
        await create_boost(session, _request(), denying_verifier, now=NOW)
    rows = (await session.execute(select(BoostPurchase))).scalars().all()
    assert rows == []


async def test_duplicate_payment_ref_rejected(session, verifier) -> None:
    await create_boost(session, _request(), verifier, now=NOW)
    with pytest.raises(DuplicateBoost):
        await create_boost(
            session,
            _request(chain="base", token_address=EVM_TOKEN),
            verifier,
            now=NOW,
        )


async def test_duplicate_live_token_rejected(session, verifier) -> None:
    await create_boost(session, _request(), verifier, now=NOW)
    with pytest.raises(DuplicateBoost):
        await create_boost(
            session,
            _request(plan_id="72h", payment_ref="ref-" + "y" * 20),
            verifier,
            now=NOW,
        )


async def test_expiry_flips_status_once(session, verifier) -> None:
    boost = await create_boost(session, _request(), verifier, now=NOW)
    later = NOW + timedelta(hours=25)

    assert await expire_due_boosts(session, now=later) == 1
    await session.refresh(boost)
    assert boost.status == "expired"

    # Idempotent: a second sweep changes nothing and writes no extra event.
    assert await expire_due_boosts(session, now=later) == 0
    expired_events = (await session.execute(select(BoostEvent).where(BoostEvent.event == "expired"))).scalars().all()
    assert len(expired_events) == 1


async def test_expiry_leaves_live_boost_alone(session, verifier) -> None:
    boost = await create_boost(session, _request(), verifier, now=NOW)
    assert await expire_due_boosts(session, now=NOW + timedelta(hours=23)) == 0
    await session.refresh(boost)
    assert boost.status == "active"


class _Receipt:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class _FakeSession:
    """Minimal execute() shape the ledger verifier uses."""

    def __init__(self, row) -> None:
        self._row = row

    async def execute(self, stmt):
        row = self._row

        class _Result:
            def scalars(self):
                class _Scalars:
                    def first(self):
                        return row

                return _Scalars()

        return _Result()


def _check(**kw) -> EntitlementCheck:
    base = {
        "payment_ref": "5" * 64,
        "chain": "solana",
        "amount": 49,
        "currency": "USD",
        "provider": "x402",
    }
    base.update(kw)
    return EntitlementCheck(**base)


async def test_ledger_verifier_accepts_settled_receipt() -> None:
    receipt = _Receipt(tx_hash="5" * 64, status="settled", chain="solana", amount_paid={"amount": "49.00"})
    await verify_entitlement(_FakeSession(receipt), _check())


@pytest.mark.parametrize(
    ("receipt", "check"),
    [
        (None, _check()),
        (_Receipt(tx_hash="5" * 64, status="pending", chain="solana", amount_paid={}), _check()),
        (_Receipt(tx_hash="5" * 64, status="settled", chain="base", amount_paid={}), _check()),
        (
            _Receipt(tx_hash="5" * 64, status="settled", chain="solana", amount_paid={"amount": "1"}),
            _check(),
        ),
    ],
)
async def test_ledger_verifier_rejects(receipt, check) -> None:
    with pytest.raises(EntitlementDenied):
        await verify_entitlement(_FakeSession(receipt), check)


async def test_fake_verifier_records_chain(session, verifier: RecordingVerifier) -> None:
    await create_boost(session, _request(chain="base", token_address=EVM_TOKEN), verifier, now=NOW)
    assert verifier.calls[0].chain == "base"
    assert verifier.calls[0].provider == "x402"
