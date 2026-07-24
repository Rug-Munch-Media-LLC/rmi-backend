"""Wallet Monitor — Alert Engine."""
from __future__ import annotations

from collections.abc import Callable

from app.domains.wallet.monitor.config import CONFIG
from app.domains.wallet.monitor.models import WalletAlert, WalletTx


class AlertEngine:
    """Configurable rules engine that evaluates each incoming tx against wallet history."""

    def __init__(self, chain: str = "ethereum") -> None:
        self.chain = chain
        self.rules: list[Callable[[WalletTx, list[WalletTx]], WalletAlert | None]] = [
            self._rule_large_transfer, self._rule_scammer_interaction,
            self._rule_first_time_dex, self._rule_new_approval, self._rule_contract_creation,
        ]

    async def evaluate(self, tx: WalletTx, history: list[WalletTx]) -> list[WalletAlert]:
        alerts: list[WalletAlert] = []
        for rule in self.rules:
            alert = rule(tx, history)
            if alert: alerts.append(alert)
        return alerts

    def _rule_large_transfer(self, tx: WalletTx, _history: list[WalletTx]) -> WalletAlert | None:
        if tx.value > (CONFIG["LARGE_TRANSFER_USD"] / 3000.0):
            return WalletAlert(alert_type="large_transfer", wallet=tx.from_addr, chain=tx.chain,
                tx_hash=tx.hash, severity="warning",
                message=f"Transfer >${CONFIG['LARGE_TRANSFER_USD']:,.0f} equivalent detected",
                metadata={"value_eth": tx.value, "estimated_usd": tx.value * 3000}, timestamp=tx.timestamp)
        return None

    def _rule_scammer_interaction(self, tx: WalletTx, _history: list[WalletTx]) -> WalletAlert | None:
        if tx.is_contract_interaction and not tx.dex_swap:
            return WalletAlert(alert_type="scammer_interaction_heuristic", wallet=tx.from_addr,
                chain=tx.chain, tx_hash=tx.hash, severity="warning",
                message="Contract interaction with non-DEX contract - verify counterparty",
                metadata={"to": tx.to_addr}, timestamp=tx.timestamp)
        return None

    def _rule_first_time_dex(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.dex_swap: return None
        if not any(h.dex_swap for h in history):
            return WalletAlert(alert_type="first_time_dex", wallet=tx.from_addr, chain=tx.chain,
                tx_hash=tx.hash, severity="info", message="First-ever DEX trade detected for wallet",
                metadata={"dex": tx.dex_swap}, timestamp=tx.timestamp)
        return None

    def _rule_new_approval(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.is_approval: return None
        if not any(h.is_approval for h in history):
            return WalletAlert(alert_type="new_token_approval", wallet=tx.from_addr, chain=tx.chain,
                tx_hash=tx.hash, severity="warning", message="First token approval - review spender",
                metadata={"spender": tx.to_addr}, timestamp=tx.timestamp)
        return None

    def _rule_contract_creation(self, tx: WalletTx, history: list[WalletTx]) -> WalletAlert | None:
        if not tx.is_contract_creation: return None
        prior_creates = sum(1 for h in history if h.is_contract_creation)
        return WalletAlert(alert_type="contract_creation", wallet=tx.from_addr, chain=tx.chain,
            tx_hash=tx.hash, severity="info" if prior_creates > 0 else "warning",
            message="Contract deployed from watched wallet",
            metadata={"contract_address": tx.to_addr or "pending"}, timestamp=tx.timestamp)
