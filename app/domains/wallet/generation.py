"""
Wallet Generation Mixin — multi-chain key generation for WalletManagerV2.

Extracted from manager.py during god-file split (2026-07-23).
"""

import hashlib
import secrets

from app.core.logging import get_logger
from app.domains.wallet.models import CHAIN_REGISTRY, ChainMeta, WalletPurpose, WalletTier, WalletRecord

logger = get_logger("wallet_generation")

# Crypto availability flags — mirror the ones set in manager.py
_HAS_BIP_UTILS: bool = False
_HAS_BASE58: bool = False
_HAS_NACL: bool = False

try:
    from bip_utils import (
        Bip39MnemonicGenerator, Bip39SeedGenerator, Bip39WordsNum,
        Bip44, Bip44Coins, Bip49, Bip49Coins, Bip84, Bip84Coins,
    )
    _HAS_BIP_UTILS = True
except ImportError:
    pass

try:
    import base58
    _HAS_BASE58 = True
except ImportError:
    pass

try:
    from nacl.signing import SigningKey as NaClSigningKey
    _HAS_NACL = True
except ImportError:
    pass


class WalletGenerationMixin:
    """
    Mixin providing wallet generation for 95+ chains.

    Expects the host class to provide:
      - _wallets (dict[str, WalletRecord])
      - encryption_password (str)
      - _store_mnemonic(wallet_id, mnemonic) method
      - _save_vault() method
    """

    def generate_wallet(
        self,
        chain: str,
        name: str = "",
        purpose: str = WalletPurpose.OPERATIONS.value,
        tier: str = WalletTier.WARM.value,
        tags: list[str] | None = None,
        group: str = "default",
        created_by: str = "",
    ) -> WalletRecord:
        """Generate a new wallet for any supported chain."""
        chain_meta = CHAIN_REGISTRY.get(chain)
        if not chain_meta:
            raise ValueError(
                f"Unsupported chain: {chain}. Supported: {sorted(CHAIN_REGISTRY.keys())}"
            )

        import time
        from datetime import UTC, datetime

        wallet_id = f"wal_{chain}_{int(time.time())}_{secrets.token_hex(4)}"

        # Generate keys using chain-appropriate method
        if chain_meta.derivation in ("bip44", "bip49", "bip84") and chain_meta.curve == "secp256k1":
            address, public_key, mnemonic = self._generate_bip_wallet(chain_meta)
        elif chain_meta.derivation in ("bip44_ed25519", "bip44_ed25519_slip"):
            address, public_key, mnemonic = self._generate_ed25519_wallet(chain_meta)
        elif chain_meta.derivation == "bip44_cosmos":
            address, public_key, mnemonic = self._generate_cosmos_wallet(chain_meta)
        elif chain_meta.derivation == "monero":
            address, public_key, mnemonic = self._generate_monero_wallet(chain_meta)
        elif chain_meta.derivation == "bip44_secp256k1":
            address, public_key, mnemonic = self._generate_bip_wallet(chain_meta)
        elif chain_meta.derivation == "bip44_ed25519_blake2b":
            address, public_key, mnemonic = self._generate_ed25519_wallet(chain_meta)
        else:
            address, public_key, mnemonic = self._generate_bip_wallet(chain_meta)

        wallet = WalletRecord(
            wallet_id=wallet_id,
            chain=chain,
            address=address,
            public_key=public_key,
            name=name or f"{chain_meta.name} Wallet",
            purpose=purpose,
            tier=tier,
            tags=tags or [],
            group=group,
            created_at=datetime.now(UTC).isoformat(),
            created_by=created_by,
        )

        if mnemonic and self.encryption_password:
            self._store_mnemonic(wallet_id, mnemonic)
            wallet.labels["mnemonic"] = "encrypted"

        self._wallets[wallet_id] = wallet
        self._save_vault()
        logger.info(f"Wallet generated: {wallet_id} ({chain}) - {address}")
        return wallet

    def _generate_bip_wallet(self, chain_meta: ChainMeta) -> tuple[str, str, str]:
        """Generate BIP44/49/84 wallet."""
        if not _HAS_BIP_UTILS:
            return self._generate_fallback(chain_meta)
        mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24).ToStr()
        seed = Bip39SeedGenerator(mnemonic).Generate()
        if chain_meta.derivation == "bip49" and Bip49Coins:
            bip_ctx = Bip49.FromSeed(seed, Bip49Coins.BITCOIN)
        elif chain_meta.derivation == "bip84" and Bip84Coins:
            bip_ctx = Bip84.FromSeed(seed, Bip84Coins.BITCOIN)
        elif chain_meta.coin_class:
            bip_ctx = Bip44.FromSeed(seed, chain_meta.coin_class)
        else:
            bip_ctx = Bip44.FromSeed(seed, Bip44Coins.ETHEREUM)
        address = bip_ctx.PublicKey().ToAddress()
        pub_key = bip_ctx.PublicKey().RawCompressed().ToHex()
        return address, pub_key, mnemonic

    def _generate_ed25519_wallet(self, chain_meta: ChainMeta) -> tuple[str, str, str]:
        if not _HAS_BIP_UTILS:
            return self._generate_fallback(chain_meta)
        mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24).ToStr()
        seed = Bip39SeedGenerator(mnemonic).Generate()
        if chain_meta.coin_class:
            bip_ctx = Bip44.FromSeed(seed, chain_meta.coin_class)
        else:
            bip_ctx = Bip44.FromSeed(seed, Bip44Coins.SOLANA)
        address = bip_ctx.PublicKey().ToAddress()
        pub_key = bip_ctx.PublicKey().RawCompressed().ToHex()
        return address, pub_key, mnemonic

    def _generate_cosmos_wallet(self, chain_meta: ChainMeta) -> tuple[str, str, str]:
        if not _HAS_BIP_UTILS:
            return self._generate_fallback(chain_meta)
        mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24).ToStr()
        seed = Bip39SeedGenerator(mnemonic).Generate()
        if chain_meta.coin_class:
            bip_ctx = Bip44.FromSeed(seed, chain_meta.coin_class)
        else:
            bip_ctx = Bip44.FromSeed(seed, Bip44Coins.COSMOS)
        address = bip_ctx.PublicKey().ToAddress()
        pub_key = bip_ctx.PublicKey().RawCompressed().ToHex()
        return address, pub_key, mnemonic

    def _generate_monero_wallet(self, chain_meta: ChainMeta) -> tuple[str, str, str]:
        mnemonic = ""
        if _HAS_BIP_UTILS:
            try:
                mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24).ToStr()
                seed = Bip39SeedGenerator(mnemonic).Generate()
                monero_ctx = Bip44.FromSeed(seed, Bip44Coins.MONERO_ED25519_SLIP)
                address = monero_ctx.PublicKey().ToAddress()
                pub_key = monero_ctx.PublicKey().RawCompressed().ToHex()
                return address, pub_key, mnemonic
            except Exception as e:
                logger.warning(f"bip_utils Monero failed ({e}), trying fallback")

        if _HAS_NACL:
            sk = NaClSigningKey.generate()
            pub_key = sk.verify_key.encode().hex()
            h = hashlib.sha3_256()
            h.update(bytes.fromhex(pub_key))
            raw_addr = h.digest()
            address = (
                "4" + base58.b58encode(raw_addr[:64]).decode()[:94]
                if _HAS_BASE58
                else "4" + raw_addr.hex()[:94]
            )
            return address, pub_key, mnemonic

        priv = secrets.token_hex(32)
        pub = hashlib.sha256(priv.encode()).hexdigest()
        return "4" + pub[:94], pub, mnemonic

    def _generate_fallback(self, chain_meta: ChainMeta) -> tuple[str, str, str]:
        logger.warning(f"Using fallback keygen for {chain_meta.key} - NOT SECURE")
        priv = secrets.token_hex(32)
        pub = hashlib.sha256(priv.encode()).hexdigest()
        prefix_map = {
            "eth": "0x", "base": "0x", "polygon": "0x", "arbitrum": "0x",
            "optimism": "0x", "avalanche": "0x", "bsc": "0x", "fantom": "0x",
            "gnosis": "0x", "sol": "", "trx": "T", "xrp": "r",
        }
        prefix = prefix_map.get(chain_meta.key, chain_meta.key + "_")
        return prefix + pub[:40], pub, ""

    def generate_hd_wallet(
        self,
        chain: str,
        mnemonic: str = "",
        account_index: int = 0,
        address_index: int = 0,
        name: str = "",
        purpose: str = WalletPurpose.OPERATIONS.value,
        tier: str = WalletTier.WARM.value,
        tags: list[str] | None = None,
        group: str = "default",
        created_by: str = "",
    ) -> WalletRecord:
        if not mnemonic and _HAS_BIP_UTILS:
            mnemonic = Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_24).ToStr()

        wallet = self.generate_wallet(
            chain=chain, name=name, purpose=purpose, tier=tier,
            tags=tags, group=group, created_by=created_by,
        )
        if mnemonic:
            self._store_mnemonic(wallet.wallet_id, mnemonic)
        wallet.labels["hd_account"] = str(account_index)
        wallet.labels["hd_address"] = str(address_index)
        self._save_vault()
        return wallet

    def generate_batch(
        self,
        chains: list[str],
        name_prefix: str = "",
        purpose: str = WalletPurpose.PAYMENTS.value,
        tier: str = WalletTier.WARM.value,
        group: str = "x402",
        created_by: str = "",
    ) -> list[WalletRecord]:
        wallets = []
        for chain in chains:
            try:
                wallet = self.generate_wallet(
                    chain=chain,
                    name=f"{name_prefix} {chain.upper()}".strip() if name_prefix else f"{chain.upper()} Payment",
                    purpose=purpose, tier=tier, group=group, created_by=created_by,
                )
                wallets.append(wallet)
            except Exception as e:
                logger.error(f"Failed to generate {chain} wallet: {e}")
        return wallets


__all__ = ["WalletGenerationMixin"]
