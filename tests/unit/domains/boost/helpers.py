"""Shared constants and fakes for the paid boost tests.

Not mocks of the code under test: ``RecordingVerifier`` implements the exact
provider contract (async callable that raises on refusal), so a test that
passes here passes against the real seam.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.domains.boost.entitlement import EntitlementCheck, EntitlementDenied
from app.models.boost import BoostPurchase

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

EVM_TOKEN = "0x" + "ab" * 20
SOL_TOKEN = "So11111111111111111111111111111111111111112"
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)


class RecordingVerifier:
    """Fake payment-provider seam that records every check and can refuse."""

    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[EntitlementCheck] = []

    async def __call__(self, session: AsyncSession, check: EntitlementCheck) -> None:
        self.calls.append(check)
        if self.deny:
            raise EntitlementDenied("receipt not settled")


def make_purchase(**overrides: Any) -> BoostPurchase:
    """An active purchase with a valid window, for listing/expiry tests."""
    defaults: dict[str, Any] = {
        "chain": "solana",
        "token_address": SOL_TOKEN,
        "plan_id": "24h",
        "duration_hours": 24,
        "price_amount": Decimal("49.00"),
        "currency": "USD",
        "payment_provider": "x402",
        "payment_ref": "ref-" + "a" * 20,
        "status": "active",
        "entitlement_verified": True,
        "starts_at": NOW - timedelta(hours=1),
        "ends_at": NOW + timedelta(hours=23),
    }
    defaults.update(overrides)
    return BoostPurchase(**defaults)
