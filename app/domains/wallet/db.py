"""
Wallet Database Mixin — persistence operations for WalletManagerV2.

Extracted from manager.py during god-file split (2026-07-23).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from app.domains.wallet.models import WalletRecord

logger = get_logger("wallet_db")


class WalletDBMixin:
    """
    Mixin providing persistence operations for WalletManagerV2.

    Expects the host class to provide:
      - VAULT_PATH, KEYSTORE_PATH, PAYMENTS_PATH, SHARES_PATH (class-level paths)
      - encryption_password (str)
      - _wallets (dict[str, WalletRecord])
      - _payments (list[PaymentRecord])
    """

    # Declared on the host (WalletManagerV2); annotations keep mypy happy
    # for mixin usage. Instantiated via the host __init__.
    VAULT_PATH: str
    KEYSTORE_PATH: str
    PAYMENTS_PATH: str
    SHARES_PATH: str
    encryption_password: str
    _wallets: dict[str, WalletRecord]
    _payments: list[Any]

    # ── Vault Persistence ────────────────────────────────────

    def _load_vault(self) -> None:
        """Load wallet vault from disk."""
        if os.path.exists(self.VAULT_PATH):
            try:
                with open(self.VAULT_PATH) as f:
                    data = json.load(f)
                for wdata in data.get("wallets", []):
                    wr = WalletRecord(**wdata)
                    self._wallets[wr.wallet_id] = wr
                logger.info(f"Vault loaded: {len(self._wallets)} wallets")
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.error(f"Vault load error: {e}")
        else:
            logger.info("No vault found - starting fresh")

    def _save_vault(self) -> None:
        """Save wallet vault to disk."""
        data = {
            "version": "3.0",
            "saved_at": datetime.now(UTC).isoformat(),
            "wallet_count": len(self._wallets),
            "chains": sorted({w.chain for w in self._wallets.values()}),
            "wallets": [w.to_dict() for w in self._wallets.values()],
        }
        try:
            with open(self.VAULT_PATH, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(self.VAULT_PATH, 0o600)
        except json.JSONDecodeError as e:
            logger.debug("_save_vault_error", exc_info=True)
            logger.error(f"Vault save error: {e}")

    # ── Payment Persistence ──────────────────────────────────

    def _load_payments(self) -> None:
        """Load payment records from disk."""
        from app.domains.wallet.models import PaymentRecord

        if os.path.exists(self.PAYMENTS_PATH):
            try:
                with open(self.PAYMENTS_PATH) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._payments.append(PaymentRecord(**json.loads(line)))
            except json.JSONDecodeError as e:
                logger.debug("_load_payments_error", exc_info=True)
                logger.error(f"Payments load error: {e}")

    # ── Key Storage ──────────────────────────────────────────

    def _store_mnemonic(self, wallet_id: str, mnemonic: str) -> None:
        """Store encrypted mnemonic in keystore."""
        from app.domains.wallet.crypto import WalletEncryption

        if not self.encryption_password:
            logger.warning("No encryption password - mnemonic not stored securely")
            return

        encrypted = WalletEncryption.encrypt(mnemonic, self.encryption_password)

        keystore = {}
        if os.path.exists(self.KEYSTORE_PATH):
            try:
                with open(self.KEYSTORE_PATH) as f:
                    keystore = json.load(f)
            except (AttributeError, ImportError):
                pass

        keystore[wallet_id] = {
            "encrypted_mnemonic": encrypted,
            "stored_at": datetime.now(UTC).isoformat(),
            "encryption_version": "V2_Argon2id",
        }

        with open(self.KEYSTORE_PATH, "w") as f:
            json.dump(keystore, f, indent=2)
        os.chmod(self.KEYSTORE_PATH, 0o600)

    def get_mnemonic(self, wallet_id: str) -> str | None:
        """Retrieve and decrypt mnemonic."""
        from app.domains.wallet.crypto import WalletEncryption

        if not os.path.exists(self.KEYSTORE_PATH):
            return None

        try:
            with open(self.KEYSTORE_PATH) as f:
                keystore = json.load(f)
        except (AttributeError, ImportError):
            return None

        entry = keystore.get(wallet_id)
        if not entry:
            return None

        return WalletEncryption.decrypt(entry["encrypted_mnemonic"], self.encryption_password)

    # ── Shamir Backup ────────────────────────────────────────

    def create_shamir_backup(self, wallet_id: str, threshold: int = 3, total: int = 5) -> list[str]:
        """Create Shamir's Secret Sharing backup shares for a wallet's mnemonic."""
        from app.domains.wallet.crypto import ShamirSecretSharing, WalletEncryption

        mnemonic = self.get_mnemonic(wallet_id)
        if not mnemonic:
            raise ValueError("No mnemonic stored for this wallet")

        shares = ShamirSecretSharing.split(mnemonic.encode(), threshold, total)

        share_dir = os.path.join(self.SHARES_PATH, wallet_id)
        os.makedirs(share_dir, mode=0o700, exist_ok=True)

        share_paths = []
        for x, share_bytes in shares:
            path = os.path.join(share_dir, f"share_{x}_of_{total}.enc")
            enc_share = WalletEncryption.encrypt(share_bytes.hex(), self.encryption_password)
            with open(path, "w") as f:
                json.dump(
                    {
                        "wallet_id": wallet_id,
                        "share_index": x,
                        "total_shares": total,
                        "threshold": threshold,
                        "encrypted_share": enc_share,
                        "created_at": datetime.now(UTC).isoformat(),
                    },
                    f,
                    indent=2,
                )
            os.chmod(path, 0o600)
            share_paths.append(path)

        # Mark in wallet labels
        wallet = self._wallets.get(wallet_id)
        if wallet:
            wallet.labels["shamir_backup"] = f"{threshold}-of-{total}"
            wallet.labels["shamir_path"] = share_dir
            self._save_vault()

        logger.info(f"Shamir backup created for {wallet_id}: {threshold}-of-{total}")
        return share_paths

    def recover_from_shamir(self, wallet_id: str, share_paths: list[str]) -> str:
        """Recover mnemonic from Shamir shares."""
        from app.domains.wallet.crypto import ShamirSecretSharing, WalletEncryption

        shares = []
        for path in share_paths:
            with open(path) as f:
                data = json.load(f)
            share_hex = WalletEncryption.decrypt(data["encrypted_share"], self.encryption_password)
            shares.append((data["share_index"], bytes.fromhex(share_hex)))

        mnemonic = ShamirSecretSharing.combine(shares).decode()
        self._store_mnemonic(wallet_id, mnemonic)
        return mnemonic


__all__ = ["WalletDBMixin"]
