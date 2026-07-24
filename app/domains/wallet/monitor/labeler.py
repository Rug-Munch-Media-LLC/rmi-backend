"""Wallet Monitor — Auto Labeler."""
from __future__ import annotations

from datetime import UTC, datetime

from app.domains.wallet.monitor.config import LABEL_PATTERNS, get_redis
from app.domains.wallet.monitor.models import LabelSuggestion, WalletTx


class AutoLabeler:
    """Suggests entity labels by pattern matching on wallet history."""

    @staticmethod
    def _counter_key(wallet: str, chain: str) -> str:
        return f"wallet:counters:{chain}:{wallet.lower()}"

    @classmethod
    async def update_counters(cls, tx: WalletTx) -> None:
        r = await get_redis()
        if not r:
            return
        key = cls._counter_key(tx.from_addr, tx.chain)
        pipe = r.pipeline()
        pipe.hincrby(key, "total_txs", 1)
        if tx.dex_swap:
            pipe.hincrby(key, "dex_txs", 1)
        if tx.is_approval:
            pipe.hincrby(key, "approvals", 1)
        if tx.is_contract_creation:
            pipe.hincrby(key, "contract_creates", 1)
        if tx.token_transfers:
            pipe.hincrby(key, "token_transfers", len(tx.token_transfers))
        pipe.expire(key, 86400 * 7)
        await pipe.execute()

    @classmethod
    async def suggest_label(cls, wallet: str, chain: str) -> LabelSuggestion | None:
        r = await get_redis()
        if not r:
            return None
        key = cls._counter_key(wallet, chain)
        counters = await r.hgetall(key)
        if not counters:
            return None
        total = int(counters.get("total_txs", 0))
        if total < 3:
            return None
        dex = int(counters.get("dex_txs", 0))
        creates = int(counters.get("contract_creates", 0))
        tokens = int(counters.get("token_transfers", 0))
        if dex >= LABEL_PATTERNS["dex_trader"]["min_dex_txs"]:
            return LabelSuggestion(wallet=wallet, chain=chain, suggested_label="dex_trader",
                confidence=min(dex / 20, 0.95), reasoning=f"{dex} DEX trades in recent window",
                pattern_summary={"dex_txs": dex, "total_txs": total}, timestamp=datetime.now(UTC).isoformat())
        if creates >= LABEL_PATTERNS["contract_deployer"]["min_contract_creates"]:
            return LabelSuggestion(wallet=wallet, chain=chain, suggested_label="contract_deployer",
                confidence=min(creates / 10, 0.95), reasoning=f"{creates} contracts deployed",
                pattern_summary={"contract_creates": creates, "total_txs": total}, timestamp=datetime.now(UTC).isoformat())
        if tokens >= LABEL_PATTERNS["airdrop_farmer"]["min_token_receives"]:
            return LabelSuggestion(wallet=wallet, chain=chain, suggested_label="airdrop_farmer",
                confidence=min(tokens / 30, 0.95), reasoning=f"{tokens} token transfer events",
                pattern_summary={"token_transfers": tokens, "total_txs": total}, timestamp=datetime.now(UTC).isoformat())
        return None
