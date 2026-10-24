"""Pricing tiers and request-contract validation."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domains.boost.pricing import PLANS, InvalidPlanError, get_plan
from app.domains.boost.schemas import (
    BoostCreateRequest,
    normalize_chain,
    plan_catalog,
)
from tests.unit.domains.boost.helpers import EVM_TOKEN, SOL_TOKEN


def test_three_published_tiers_with_explicit_currency() -> None:
    assert set(PLANS) == {"24h", "72h", "7d"}
    durations = [p.duration_hours for p in PLANS.values()]
    assert durations == [24, 72, 168]
    for plan in PLANS.values():
        assert plan.currency == "USD"
        assert plan.price_amount > Decimal("0")
    # Longer windows must cost more than shorter ones.
    ordered = sorted(PLANS.values(), key=lambda p: p.duration_hours)
    prices = [p.price_amount for p in ordered]
    assert prices == sorted(prices)


def test_unknown_plan_is_rejected() -> None:
    with pytest.raises(InvalidPlanError):
        get_plan("1y")
    with pytest.raises(ValidationError):
        BoostCreateRequest(
            chain="solana",
            token_address=SOL_TOKEN,
            plan_id="1y",
            payment_ref="ref-" + "b" * 20,
        )


def test_plan_catalog_reports_currency() -> None:
    catalog = plan_catalog()
    assert catalog.currency == "USD"
    assert [p.plan_id for p in catalog.plans] == ["24h", "72h", "7d"]


@pytest.mark.parametrize(("alias", "canonical"), [("SOL", "solana"), ("eth", "ethereum"), ("bnb", "bsc")])
def test_chain_aliases_normalise(alias: str, canonical: str) -> None:
    assert normalize_chain(alias) == canonical


def test_invalid_chain_rejected() -> None:
    with pytest.raises(ValidationError):
        BoostCreateRequest(
            chain="dogechain",
            token_address=SOL_TOKEN,
            plan_id="24h",
            payment_ref="ref-" + "c" * 20,
        )


def test_invalid_token_address_rejected_per_chain() -> None:
    with pytest.raises(ValidationError):
        BoostCreateRequest(
            chain="solana",
            token_address=EVM_TOKEN,
            plan_id="24h",
            payment_ref="ref-" + "d" * 20,
        )
    with pytest.raises(ValidationError):
        BoostCreateRequest(
            chain="base",
            token_address="not-an-address",
            plan_id="24h",
            payment_ref="ref-" + "e" * 20,
        )


def test_unknown_payment_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        BoostCreateRequest(
            chain="base",
            token_address=EVM_TOKEN,
            plan_id="24h",
            payment_ref="ref-" + "f" * 20,
            payment_provider="carrier-pigeon",
        )


def test_valid_request_normalises() -> None:
    req = BoostCreateRequest(
        chain="SOL",
        token_address=SOL_TOKEN,
        plan_id="7D",
        payment_ref="ref-" + "g" * 20,
    )
    assert (req.chain, req.plan_id, req.payment_provider) == ("solana", "7d", "x402")
