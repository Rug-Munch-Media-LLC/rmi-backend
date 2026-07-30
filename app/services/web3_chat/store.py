"""
Web3 Chat Store
===============
In-memory store for Web3 chat data (payments, tips, splits, gates, wallets).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.web3_chat.models import (
        PaymentRequest,
        PaymentStatus,
        SplitPayment,
        TipRecord,
        TokenGate,
    )

logger = get_logger("web3_chat")


# ── In-Memory Store (Redis/DB replacement for initial build) ──────


class Web3ChatStore:
    """In-memory + future Redis/Postgres store for Web3 chat data.

    Designed to be swapped to a persistent store by implementing
    the same interface methods.
    """

    def __init__(self) -> None:
        self._payments: dict[str, PaymentRequest] = {}
        self._tips: dict[str, TipRecord] = {}
        self._splits: dict[str, SplitPayment] = {}
        self._token_gates: dict[str, TokenGate] = {}
        self._nft_gates: dict[str, TokenGate] = {}
        self._connected_wallets: dict[str, dict[str, Any]] = {}
        self._nonces: dict[str, dict[str, Any]] = {}

    # ── Payments ──
    def save_payment(self, payment: PaymentRequest) -> None:
        self._payments[payment.id] = payment

    def get_payment(self, payment_id: str) -> PaymentRequest | None:
        return self._payments.get(payment_id)

    def get_user_payments(self, user_id: str) -> list[PaymentRequest]:
        return [p for p in self._payments.values() if p.sender_id == user_id or p.recipient_username == user_id]

    def update_payment_status(self, payment_id: str, status: PaymentStatus, tx_hash: str | None = None) -> None:
        if payment_id in self._payments:
            self._payments[payment_id].status = status
            if tx_hash:
                self._payments[payment_id].tx_hash = tx_hash

    # ── Tips ──
    def save_tip(self, tip: TipRecord) -> None:
        self._tips[tip.id] = tip

    def get_user_tips(self, user_id: str) -> list[TipRecord]:
        return [t for t in self._tips.values() if t.from_user == user_id or t.to_user == user_id]

    # ── Splits ──
    def save_split(self, split: SplitPayment) -> None:
        self._splits[split.id] = split

    # ── Token Gates ──
    def save_token_gate(self, gate: TokenGate) -> None:
        self._token_gates[gate.room_id] = gate

    def get_token_gate(self, room_id: str) -> TokenGate | None:
        return self._token_gates.get(room_id)

    def remove_gate(self, room_id: str) -> None:
        self._token_gates.pop(room_id, None)
        self._nft_gates.pop(room_id, None)

    # ── NFT Gates ──
    def save_nft_gate(self, gate: TokenGate) -> None:
        self._nft_gates[gate.room_id] = gate

    def get_nft_gate(self, room_id: str) -> TokenGate | None:
        return self._nft_gates.get(room_id)

    # ── Wallets ──
    def save_connection(self, user_id: str, connection: dict[str, Any]) -> None:
        self._connected_wallets[user_id] = connection

    def get_connection(self, user_id: str) -> dict[str, Any] | None:
        return self._connected_wallets.get(user_id)

    def remove_connection(self, user_id: str) -> None:
        self._connected_wallets.pop(user_id, None)


# ── Singleton ──
_store = Web3ChatStore()


def get_store() -> Web3ChatStore:
    """Get the Web3 chat store singleton."""
    return _store
