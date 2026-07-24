"""
Wallet Analysis Mixin — statistics, alerts, sweeping, and revenue status.

Extracted from manager.py during god-file split (2026-07-23).
"""

import json
import os
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.domains.wallet.models import CHAIN_REGISTRY, WalletRecord, WalletRotationSchedule, WalletStatus

logger = get_logger("wallet_analysis")


class WalletAnalysisMixin:
    """
    Mixin providing analytics, alerts, sweep, and export operations.

    Expects the host class to provide:
      - _wallets (dict[str, WalletRecord])
      - _payments (list[PaymentRecord])
      - get_mnemonic(wallet_id) method
      - _save_vault() method
    """

    # ── Statistics ───────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get comprehensive wallet statistics."""
        total_balance_usd = sum(w.balance_usd for w in self._wallets.values())

        by_chain: dict[str, int] = {}
        by_purpose: dict[str, int] = {}
        by_tier: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_ecosystem: dict[str, int] = {}

        for w in self._wallets.values():
            by_chain[w.chain] = by_chain.get(w.chain, 0) + 1
            by_purpose[w.purpose] = by_purpose.get(w.purpose, 0) + 1
            by_tier[w.tier] = by_tier.get(w.tier, 0) + 1
            by_status[w.status] = by_status.get(w.status, 0) + 1

            meta = CHAIN_REGISTRY.get(w.chain)
            eco = meta.curve if meta else "unknown"
            by_ecosystem[eco] = by_ecosystem.get(eco, 0) + 1

        total_payments = len(self._payments)
        total_revenue = sum(p.amount_usd for p in self._payments if p.status == "confirmed")

        return {
            "total_wallets": len(self._wallets),
            "active_wallets": by_status.get(WalletStatus.ACTIVE.value, 0),
            "total_balance_usd": round(total_balance_usd, 2),
            "chains_supported": len(CHAIN_REGISTRY),
            "chains_in_use": len(by_chain),
            "by_chain": by_chain,
            "by_ecosystem": by_ecosystem,
            "by_purpose": by_purpose,
            "by_tier": by_tier,
            "by_status": by_status,
            "total_payments": total_payments,
            "total_revenue_usd": round(total_revenue, 2),
            "x402_wallets": sum(1 for w in self._wallets.values() if w.x402_enabled),
            "subscription_wallets": sum(
                1 for w in self._wallets.values() if w.subscription_enabled
            ),
            "rotated_wallets": sum(
                1 for w in self._wallets.values() if w.status == WalletStatus.ROTATED.value
            ),
            "last_updated": datetime.now(UTC).isoformat(),
        }

    # ── Alerts ────────────────────────────────────────────────

    def check_alerts(self) -> list[dict]:
        """Check all wallets for alert conditions."""
        alerts = []

        for w in self._wallets.values():
            if w.status != WalletStatus.ACTIVE.value:
                continue

            if w.low_balance_threshold > 0 and w.balance_usd < w.low_balance_threshold:
                alerts.append({
                    "type": "low_balance",
                    "wallet_id": w.wallet_id,
                    "address": w.address,
                    "chain": w.chain,
                    "balance_usd": w.balance_usd,
                    "threshold": w.low_balance_threshold,
                    "severity": "warning",
                })

            schedule_str = w.labels.get("rotation_schedule")
            if schedule_str:
                schedule = WalletRotationSchedule(**json.loads(schedule_str))
                if schedule.next_rotation:
                    next_rot = datetime.fromisoformat(schedule.next_rotation.replace("Z", "+00:00"))
                    days_until = (next_rot - datetime.now(UTC)).days

                    if days_until <= schedule.notify_before_days and days_until > 0:
                        alerts.append({
                            "type": "rotation_due",
                            "wallet_id": w.wallet_id,
                            "address": w.address,
                            "chain": w.chain,
                            "days_until": days_until,
                            "severity": "info",
                        })
                    elif days_until <= 0:
                        alerts.append({
                            "type": "rotation_overdue",
                            "wallet_id": w.wallet_id,
                            "address": w.address,
                            "chain": w.chain,
                            "days_overdue": abs(days_until),
                            "severity": "critical",
                        })

        return alerts

    # ── Export / Import ───────────────────────────────────────

    def export_safe(self) -> dict:
        """Export wallet data without keys."""
        return {
            "version": "3.0",
            "exported_at": datetime.now(UTC).isoformat(),
            "wallets": [w.to_safe_dict() for w in self._wallets.values()],
            "chain_registry": {
                k: {"name": v.name, "symbol": v.native_symbol}
                for k, v in CHAIN_REGISTRY.items()
            },
        }

    def export_for_chain(self, chain: str) -> list[dict]:
        """Export wallets for a specific chain."""
        return [w.to_safe_dict() for w in self._wallets.values() if w.chain == chain]

    # ── Auto-Sweep ───────────────────────────────────────────────

    async def sweep_to_owner(self, chain: str = "", min_usd_threshold: float = 5.0) -> dict:
        """
        Sweep USDC balances above threshold from x402 receiving wallets to owner wallets.

        EVM owner: 0x1E3AC01d0fdb976179790BDD02823196A92705C9
        Solana owner: Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv
        """
        owner_map = {
            "evm": "0x1E3AC01d0fdb976179790BDD02823196A92705C9",
            "sol": "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv",
        }
        evm_chains = {
            "eth", "base", "polygon", "arbitrum", "optimism",
            "avalanche", "bsc", "fantom", "gnosis",
        }

        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "chain_filter": chain or "all",
            "threshold_usd": min_usd_threshold,
            "swept": [],
            "skipped": [],
            "errors": [],
            "total_swept_usd": 0.0,
        }

        wallets_to_check = [
            w for w in self._wallets.values()
            if w.x402_enabled and (not chain or w.chain == chain)
        ]

        if not wallets_to_check:
            logger.info("sweep_to_owner: no x402-enabled wallets found")
            result["skipped"].append("no_x402_wallets")
            return result

        for wallet in wallets_to_check:
            try:
                balance = wallet.balance_usd
                if balance < min_usd_threshold:
                    result["skipped"].append({
                        "wallet_id": wallet.wallet_id,
                        "address": wallet.address,
                        "chain": wallet.chain,
                        "balance_usd": balance,
                        "reason": f"below_threshold ({balance} < {min_usd_threshold})",
                    })
                    continue

                if wallet.chain in evm_chains:
                    await self._sweep_evm(wallet, owner_map["evm"], result)
                elif wallet.chain == "sol":
                    await self._sweep_solana(wallet, owner_map["sol"], result)
                else:
                    result["skipped"].append({
                        "wallet_id": wallet.wallet_id,
                        "address": wallet.address,
                        "chain": wallet.chain,
                        "balance_usd": balance,
                        "reason": "unsupported_chain_for_sweep",
                    })
                    continue

            except Exception as e:  # noqa: BLE001
                logger.error(f"sweep_to_owner error for {wallet.wallet_id}: {e}")
                result["errors"].append({
                    "wallet_id": wallet.wallet_id,
                    "address": wallet.address,
                    "chain": wallet.chain,
                    "error": str(e),
                })

        self._save_vault()
        result["total_swept_usd"] = round(result["total_swept_usd"], 2)
        logger.info(
            f"sweep_to_owner complete: {len(result['swept'])} swept, "
            f"{len(result['skipped'])} skipped, {len(result['errors'])} errors"
        )
        return result

    async def _sweep_evm(self, wallet: WalletRecord, owner: str, result: dict) -> None:
        """Sweep native token from an EVM wallet to the owner address."""
        rpc_url = (
            os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
            if wallet.chain == "base"
            else os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
        )

        mnemonic = self.get_mnemonic(wallet.wallet_id)
        if not mnemonic:
            raise RuntimeError(
                f"CRITICAL: Cannot sweep {wallet.wallet_id}. Mnemonic is missing."
            )

        from eth_account import Account
        acct = Account.from_mnemonic(mnemonic)
        if acct.address.lower() != wallet.address.lower():
            raise RuntimeError(
                f"Derived address {acct.address} does not match wallet {wallet.address}"
            )

        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            raise RuntimeError(f"Failed to connect to RPC: {rpc_url}")

        on_chain_balance_wei = w3.eth.get_balance(wallet.address)
        if on_chain_balance_wei == 0:
            raise RuntimeError("On-chain balance is 0. Local ledger is out of sync.")

        gas_reserve_wei = w3.to_wei(0.0001, "ether")
        if on_chain_balance_wei <= gas_reserve_wei:
            raise RuntimeError("Balance too low to sweep after gas reserve.")

        sweep_amount_wei = on_chain_balance_wei - gas_reserve_wei
        nonce = w3.eth.get_transaction_count(wallet.address)
        gas_price = w3.eth.gas_price
        tx = {
            "nonce": nonce,
            "to": Web3.to_checksum_address(owner),
            "value": sweep_amount_wei,
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": w3.eth.chain_id,
        }
        signed_tx = w3.eth.account.sign_transaction(tx, acct.key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash_hex = tx_hash.hex()
        logger.info(f"SUCCESS: Broadcasted native sweep tx {tx_hash_hex} for {wallet.wallet_id}")

        # Update local ledger
        wallet.balance_raw = str(gas_reserve_wei)
        wallet.balance_decimal = float(w3.from_wei(gas_reserve_wei, "ether"))
        wallet.balance_usd = float(w3.from_wei(gas_reserve_wei, "ether")) * 2500.0
        wallet.total_sent += float(w3.from_wei(sweep_amount_wei, "ether"))

        result["swept"].append({
            "wallet_id": wallet.wallet_id,
            "address": wallet.address,
            "chain": wallet.chain,
            "amount_wei": sweep_amount_wei,
            "tx_hash": tx_hash_hex,
            "owner": owner,
            "status": "broadcasted",
        })
        result["total_swept_usd"] += float(w3.from_wei(sweep_amount_wei, "ether")) * 2500.0

    async def _sweep_solana(self, wallet: WalletRecord, owner: str, result: dict) -> None:
        """Sweep SOL from a Solana wallet to the owner address."""
        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

        mnemonic = self.get_mnemonic(wallet.wallet_id)
        if not mnemonic:
            raise RuntimeError(
                f"CRITICAL: Cannot sweep {wallet.wallet_id}. Mnemonic is missing."
            )

        from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins
        seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
        bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        bip44_def_ctx = bip44_mst_ctx.Purpose().Coin().Account(0).Change(0).AddressIndex(0)
        private_key_bytes = bip44_def_ctx.PrivateKey().Raw().ToBytes()

        from solders.keypair import Keypair as SoldersKeypair
        from solders.pubkey import Pubkey as SoldersPubkey
        keypair = SoldersKeypair.from_bytes(private_key_bytes)
        if str(keypair.pubkey()) != wallet.address:
            raise RuntimeError(
                f"Derived address {keypair.pubkey()} does not match wallet {wallet.address}"
            )

        from solana.rpc.async_api import AsyncClient
        from solana.rpc.commitment import Confirmed
        from solana.transaction import Transaction
        from solders.system_program import TransferParams, transfer

        owner_pubkey = SoldersPubkey.from_string(owner)

        async with AsyncClient(rpc_url) as client:
            resp = await client.get_balance(keypair.pubkey(), commitment=Confirmed)
            on_chain_balance_lamports = resp.value

            if on_chain_balance_lamports == 0:
                raise RuntimeError("On-chain balance is 0. Local ledger is out of sync.")

            gas_reserve_lamports = 1_000_000
            if on_chain_balance_lamports <= gas_reserve_lamports:
                raise RuntimeError("Balance too low to sweep after gas reserve.")

            sweep_amount_lamports = on_chain_balance_lamports - gas_reserve_lamports

            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=keypair.pubkey(),
                    to_pubkey=owner_pubkey,
                    lamports=sweep_amount_lamports,
                )
            )
            tx = Transaction().add(transfer_ix)
            tx.sign(keypair)

            result_tx = await client.send_transaction(tx, keypair)
            tx_sig = str(result_tx.value)
            logger.info(f"SUCCESS: Broadcasted Solana sweep tx {tx_sig} for {wallet.wallet_id}")

            wallet.balance_raw = str(gas_reserve_lamports)
            wallet.balance_decimal = gas_reserve_lamports / 1_000_000_000
            wallet.balance_usd = wallet.balance_decimal * 150.0
            wallet.total_sent += sweep_amount_lamports / 1_000_000_000

            result["swept"].append({
                "wallet_id": wallet.wallet_id,
                "address": wallet.address,
                "chain": wallet.chain,
                "amount_lamports": sweep_amount_lamports,
                "tx_signature": tx_sig,
                "owner": owner,
                "status": "broadcasted",
            })
            result["total_swept_usd"] += (sweep_amount_lamports / 1_000_000_000) * 150.0

    # ── Balance Monitoring ─────────────────────────────────────

    async def check_all_balances(self) -> list[dict]:
        """Check balances on all x402-enabled wallets."""
        alerts = []
        checked = 0
        below_threshold = 0

        for w in self._wallets.values():
            if not w.x402_enabled:
                continue
            checked += 1

            alert = {
                "wallet_id": w.wallet_id,
                "address": w.address,
                "chain": w.chain,
                "balance_usd": w.balance_usd,
                "low_balance_threshold": w.low_balance_threshold,
                "checked_at": datetime.now(UTC).isoformat(),
            }

            if w.low_balance_threshold > 0 and w.balance_usd < w.low_balance_threshold:
                alert["type"] = "low_balance"
                alert["severity"] = "warning"
                alert["message"] = (
                    f"Wallet {w.wallet_id} ({w.chain}) balance ${w.balance_usd:.2f} "
                    f"is below threshold ${w.low_balance_threshold:.2f}"
                )
                alerts.append(alert)
                below_threshold += 1
                logger.warning(alert["message"])
            elif w.balance_usd <= 0:
                alert["type"] = "zero_balance"
                alert["severity"] = "info"
                alert["message"] = f"Wallet {w.wallet_id} ({w.chain}) has zero balance"
                alerts.append(alert)
            else:
                alert["type"] = "ok"
                alert["severity"] = "info"
                alert["message"] = (
                    f"Wallet {w.wallet_id} ({w.chain}) balance ${w.balance_usd:.2f} OK"
                )
                alerts.append(alert)

        summary = {
            "checked": checked,
            "below_threshold": below_threshold,
            "total_alerts": len(alerts),
            "alerts": alerts,
            "checked_at": datetime.now(UTC).isoformat(),
        }

        logger.info(
            f"check_all_balances: checked {checked} x402 wallets, {below_threshold} below threshold"
        )
        return summary

    async def get_revenue_wallet_status(self) -> dict:
        """Return summary of all x402 receiving wallets with current balances."""
        wallets = []
        total_balance_usd = 0.0
        evm_chains = {
            "eth", "base", "polygon", "arbitrum", "optimism",
            "avalanche", "bsc", "fantom", "gnosis",
        }
        sweep_threshold = 5.0

        for w in self._wallets.values():
            if not w.x402_enabled:
                continue

            needs_sweep = w.balance_usd >= sweep_threshold
            if w.chain in evm_chains:
                owner = "0x1E3AC01d0fdb976179790BDD02823196A92705C9"
            elif w.chain == "sol":
                owner = "Gix4P9AmwcZRGzr2hCEME5m2QAvY86dBfm8c7e7MpFzv"
            else:
                owner = ""

            wallets.append({
                "wallet_id": w.wallet_id,
                "address": w.address,
                "chain": w.chain,
                "balance_usd": w.balance_usd,
                "balance_raw": w.balance_raw,
                "tier": w.tier,
                "purpose": w.purpose,
                "status": w.status,
                "x402_price_usd": w.x402_price_usd,
                "low_balance_threshold": w.low_balance_threshold,
                "needs_sweep": needs_sweep,
                "owner_address": owner,
                "last_used": w.last_used,
                "tx_count": w.tx_count,
            })
            total_balance_usd += w.balance_usd

        need_sweep_count = sum(1 for w in wallets if w["needs_sweep"])
        below_threshold_count = sum(
            1 for w in wallets
            if w["low_balance_threshold"] > 0 and w["balance_usd"] < w["low_balance_threshold"]
        )

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "total_x402_wallets": len(wallets),
            "total_balance_usd": round(total_balance_usd, 2),
            "need_sweep_count": need_sweep_count,
            "below_threshold_count": below_threshold_count,
            "sweep_threshold_usd": sweep_threshold,
            "wallets": wallets,
        }


__all__ = ["WalletAnalysisMixin"]
