"""Payment-entitlement verification for boost purchases.

A boost is only activated after the money side is confirmed. The default
verifier reads our ``payments`` ledger for a *settled* receipt matching the
presented reference — we never mark a purchase paid on the client's word, and
we never call a payment provider from this path (the ledger is the record).

Swap the verifier through FastAPI DI (``get_verifier``) to point at another
provider; the service only depends on this callable contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.payment import Payment

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Ledger states that mean "the money is ours".
SETTLED_STATUSES: frozenset[str] = frozenset({"settled", "confirmed", "completed", "paid"})


class EntitlementDenied(Exception):
    """The presented payment reference does not entitle the caller to a boost."""


@dataclass(frozen=True, slots=True)
class EntitlementCheck:
    """What the buyer claims, to be proven against the ledger."""

    payment_ref: str
    chain: str
    amount: Decimal
    currency: str
    provider: str


class EntitlementVerifier(Protocol):
    """Callable contract for entitlement proof."""

    async def __call__(self, session: AsyncSession, check: EntitlementCheck) -> None: ...


def _ledger_amount(row: Payment) -> Decimal | None:
    """Best-effort amount from the JSON receipt (``amount`` or ``value``)."""
    raw: Any = row.amount_paid
    if not isinstance(raw, dict):
        return None
    for key in ("amount", "value", "amount_usd"):
        candidate = raw.get(key)
        if candidate is None:
            continue
        try:
            return Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            return None
    return None


async def verify_entitlement(session: AsyncSession, check: EntitlementCheck) -> None:
    """Prove the payment reference or raise ``EntitlementDenied``.

    Checks, in order: the receipt exists, its status is settled, the chain
    matches, and — when the ledger recorded an amount — a short-paid receipt
    is rejected.
    """
    stmt = select(Payment).where(Payment.tx_hash == check.payment_ref)
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise EntitlementDenied(f"unknown payment reference {check.payment_ref!r}")

    status = str(row.status or "").strip().lower()
    if status not in SETTLED_STATUSES:
        raise EntitlementDenied(f"payment reference {check.payment_ref!r} is {status or 'unsettled'}, not settled")

    if row.chain and row.chain.strip().lower() != check.chain:
        raise EntitlementDenied(
            f"payment reference {check.payment_ref!r} is for chain {row.chain!r}, not {check.chain!r}"
        )

    paid = _ledger_amount(row)
    if paid is not None and paid < check.amount:
        raise EntitlementDenied(f"payment reference {check.payment_ref!r} paid {paid} < required {check.amount}")

    logger.info(
        "boost_entitlement_verified ref=%s chain=%s amount=%s currency=%s provider=%s",
        check.payment_ref,
        check.chain,
        check.amount,
        check.currency,
        check.provider,
    )
