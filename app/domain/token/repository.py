"""Repository — async data fetch for token info via Databus."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domain.token.models import (
    TokenDetail,
    TokenHolder,
    TokenLiquidity,
)

log = get_logger(__name__)


class TokenRepository:
    """Async data access for token info via Databus."""

    async def get_detail(self, address: str, chain: str) -> TokenDetail:
        """Fetch token metadata + supply."""
        try:
            from app.databus.client import get_databus_client

            client = get_databus_client()
            raw = await client.get_token_info(address, chain=chain)
            return self._parse_detail(address, chain, raw or {})
        except Exception as e:
            log.warning("token_detail_fetch_failed", address=address[:12], error=str(e))
            return TokenDetail(address=address, chain=chain)

    async def get_holders(
        self,
        address: str,
        chain: str,
        limit: int = 100,
    ) -> list[TokenHolder]:
        """Fetch top holders."""
        try:
            from app.databus.client import get_databus_client

            client = get_databus_client()
            raw = await client.get_token_holders(address, chain=chain, limit=limit)
            return [self._parse_holder(h) for h in (raw or [])]
        except Exception as e:
            log.warning("token_holders_fetch_failed", address=address[:12], error=str(e))
            return []

    async def get_liquidity(self, address: str, chain: str) -> TokenLiquidity:
        """Fetch liquidity + pool info."""
        try:
            from app.databus.client import get_databus_client

            client = get_databus_client()
            raw = await client.get_token_liquidity(address, chain=chain)
            return self._parse_liquidity(address, chain, raw or {})
        except Exception as e:
            log.warning("token_liquidity_fetch_failed", address=address[:12], error=str(e))
            return TokenLiquidity(address=address, chain=chain)

    @staticmethod
    def _parse_detail(address: str, chain: str, raw: dict[str, Any]) -> TokenDetail:
        return TokenDetail(
            address=address,
            chain=chain,
            name=raw.get("name", ""),
            symbol=raw.get("symbol", ""),
            decimals=int(raw.get("decimals", 9) or 9),
            total_supply=float(raw.get("total_supply", 0) or 0),
            circulating_supply=float(raw.get("circulating_supply", 0) or 0),
            holders=int(raw.get("holders", 0) or 0),
            verified=bool(raw.get("verified", False)),
            metadata=raw.get("metadata", {}) or {},
        )

    @staticmethod
    def _parse_holder(raw: dict[str, Any]) -> TokenHolder:
        return TokenHolder(
            address=raw.get("address", ""),
            balance=float(raw.get("balance", 0) or 0),
            percentage=float(raw.get("percentage", raw.get("pct", 0)) or 0),
            label=raw.get("label"),
            is_contract=bool(raw.get("is_contract", False)),
            is_locked=bool(raw.get("is_locked", False)),
        )

    @staticmethod
    def _parse_liquidity(address: str, chain: str, raw: dict[str, Any]) -> TokenLiquidity:
        return TokenLiquidity(
            address=address,
            chain=chain,
            total_liquidity_usd=float(raw.get("total_usd", 0) or 0),
            pools=raw.get("pools", []) or [],
            locked_percentage=float(raw.get("locked_percentage", 0) or 0),
        )
