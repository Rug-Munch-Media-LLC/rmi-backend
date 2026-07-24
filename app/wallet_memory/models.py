"""
Normalized transaction model for wallet memory ingestion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


class NormalizedTransaction:
    """Common transaction representation across Solana and EVM."""

    __slots__ = (
        "address",
        "amount",
        "block_number",
        "chain_id",
        "counterparty",
        "is_contract_creation",
        "program",
        "timestamp",
        "token_address",
        "token_value_decimal",
        "token_value_raw",
        "transfer_type",
        "tx_hash",
        "tx_type",
    )

    def __init__(
        self,
        address: str,
        counterparty: str,
        timestamp: datetime,
        amount: float = 0.0,
        chain_id: str = "",
        program: str = "",
        tx_hash: str = "",
        tx_type: str = "transfer",
        token_address: str = "",
        token_value_raw: str = "0",
        token_value_decimal: float = 0.0,
        transfer_type: str = "native",
        block_number: int = 0,
        is_contract_creation: bool = False,
    ) -> None:
        self.address = address
        self.counterparty = counterparty
        self.timestamp = timestamp
        self.amount = amount
        self.chain_id = chain_id
        self.program = program
        self.tx_hash = tx_hash
        self.tx_type = tx_type
        self.token_address = token_address
        self.token_value_raw = token_value_raw
        self.token_value_decimal = token_value_decimal
        self.transfer_type = transfer_type
        self.block_number = block_number
        self.is_contract_creation = is_contract_creation

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "counterparty": self.counterparty,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "amount": self.amount,
            "chain_id": self.chain_id,
            "program": self.program,
            "tx_hash": self.tx_hash,
            "tx_type": self.tx_type,
            "token_address": self.token_address,
            "token_value_raw": self.token_value_raw,
            "token_value_decimal": self.token_value_decimal,
            "transfer_type": self.transfer_type,
            "block_number": self.block_number,
            "is_contract_creation": self.is_contract_creation,
        }

    def to_clustering_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "counterparty": self.counterparty,
            "timestamp": self.timestamp,
            "amount": self.amount,
            "program": self.program,
        }

    def to_transfer_row(self) -> dict[str, Any] | None:
        if not self.tx_hash and not self.token_address:
            return None
        return {
            "chain_id": self.chain_id,
            "block_number": self.block_number,
            "block_timestamp": self.timestamp,
            "transaction_hash": self.tx_hash,
            "log_index": 0,
            "from_address": self.address if self.tx_type == "send" else self.counterparty,
            "to_address": self.counterparty if self.tx_type == "send" else self.address,
            "token_address": self.token_address or ("native" if not self.token_address else ""),
            "value_raw": self.token_value_raw,
            "value_decimal": self.token_value_decimal,
            "transfer_type": self.transfer_type,
        }
