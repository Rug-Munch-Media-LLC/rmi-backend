"""Repository — async data fetch for wallet info.

Backed by:
  - Databus (chain-agnostic on-chain data) for balances + transactions
  - Redis (cached labels) for known wallet labels

This is a thin async wrapper. No business logic.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domain.wallet.models import (
    Balance,
    TokenHolding,
    Transaction,
    Wallet,
)

log = get_logger(__name__)

LABELS_HASH_KEY = "rmi:wallet_labels"


class WalletRepository:
    """Async data access for wallet info."""

    async def get_balance(self, wallet: Wallet) -> Balance:
        """Fetch native + token balances for a wallet.

        Backed by Databus. Falls back to empty balance on error.
        """
        try:
            from app.databus.client import get_databus_client  # local import to avoid cycles

            client = get_databus_client()
            holdings = await client.get_wallet_balance(wallet.address, chain=wallet.chain)
            return self._parse_balance(wallet, holdings)
        except Exception as e:
            log.warning("wallet_balance_fetch_failed", address=wallet.address[:12], error=str(e))
            return Balance(address=wallet.address, chain=wallet.chain)

    async def get_transactions(
        self,
        wallet: Wallet,
        limit: int = 50,
    ) -> list[Transaction]:
        """Fetch recent transactions for a wallet."""
        try:
            from app.databus.client import get_databus_client

            client = get_databus_client()
            txs = await client.get_wallet_transactions(wallet.address, chain=wallet.chain, limit=limit)
            return [self._parse_tx(tx) for tx in (txs or [])]
        except Exception as e:
            log.warning("wallet_tx_fetch_failed", address=wallet.address[:12], error=str(e))
            return []

    async def get_labels(self, wallet: Wallet) -> list[str]:
        """Look up known labels for a wallet from Redis."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            raw: str | None = await r.hget(LABELS_HASH_KEY, wallet.address.lower())
            if raw is None:
                return []
            import json
            data = json.loads(raw)
            return data.get("labels", []) if isinstance(data, dict) else []
        except Exception as e:
            log.warning("wallet_label_fetch_failed", address=wallet.address[:12], error=str(e))
            return []

    @staticmethod
    def _parse_balance(wallet: Wallet, raw: dict[str, Any]) -> Balance:
        tokens: list[TokenHolding] = []
        total = 0.0
        for h in (raw or {}).get("tokens", [])[:20]:
            token_info = h.get("token", {}) or {}
            decimals = int(token_info.get("decimals", 0) or 0) or 1
            amount = float(h.get("amount", 0) or 0) / (10**decimals)
            price = float(h.get("priceUsdt", h.get("price_usd", 0)) or 0)
            value = amount * price
            total += value
            tokens.append(TokenHolding(
                symbol=token_info.get("symbol", "?"),
                address=token_info.get("address", ""),
                amount=round(amount, 4),
                price_usd=price,
                value_usd=round(value, 2),
            ))
        return Balance(
            address=wallet.address,
            chain=wallet.chain,
            native_balance=float((raw or {}).get("native_balance", 0) or 0),
            native_symbol=(raw or {}).get("native_symbol", ""),
            total_value_usd=round(total, 2),
            tokens=tokens,
        )

    @staticmethod
    def _parse_tx(tx: dict[str, Any]) -> Transaction:
        block_time = tx.get("block_time")
        return Transaction(
            hash=str(tx.get("trans_id") or tx.get("tx_hash") or tx.get("hash", ""))[:64],
            type=str(tx.get("flow") or tx.get("type") or "transfer"),
            amount_usd=float(tx.get("change_amount") or tx.get("amount_usd", 0) or 0),
            timestamp=int(block_time or 0),
            direction=tx.get("direction"),
        )
