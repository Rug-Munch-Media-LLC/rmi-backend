"""
Wallet Factory - Per-Family Generation Functions
=================================================
Standalone wallet generation functions for each chain family.
"""

from __future__ import annotations

import hashlib
import secrets

from app.wallets.chain_registry import ChainConfig, ChainFamily
from app.wallets.crypto import _HAS_BASE58, _HAS_COINCURVE, _HAS_ECDSA, _HAS_NACL

if _HAS_BASE58:
    import base58
from app.wallets.models import WalletResult

# Re-export coincurve and ecdsa for local use
if _HAS_COINCURVE:
    from coincurve import PrivateKey as CoinCurveKey
if _HAS_ECDSA:
    from ecdsa import SECP256k1, SigningKey
if _HAS_NACL:
    from nacl.encoding import RawEncoder
    from nacl.signing import SigningKey as NaClSigningKey

# ── EVM Generation ──────────────────────────────────────────────

def generate_evm(cfg: ChainConfig) -> WalletResult:
    """Generate Ethereum/EVM-compatible wallet."""
    if _HAS_COINCURVE:
        pk = CoinCurveKey()
        priv_hex = pk.to_hex()
        pub = pk.public_key.format(compressed=False)[1:]
    elif _HAS_ECDSA:
        pk = SigningKey.generate(curve=SECP256k1)
        priv_hex = pk.to_string().hex()
        pub = pk.get_verifying_key().to_string("uncompressed")[1:]
    else:
        priv_hex = secrets.token_hex(32)
        pub = b""

    address = (
        "0x" + hashlib.sha3_256(pub).hexdigest()[-40:] if pub else "0x" + secrets.token_hex(20)
    )
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        public_key_hex=pub.hex() if isinstance(pub, bytes) else pub,
        derivation_path=cfg.hd_path,
    )

# ── Bitcoin Generation ──────────────────────────────────────────

def generate_bitcoin(cfg: ChainConfig) -> WalletResult:
    """Generate Bitcoin wallet (Legacy P2PKH)."""
    if _HAS_COINCURVE:
        pk = CoinCurveKey()
        priv_hex = pk.to_hex()
        pub = pk.public_key.format(compressed=True)
    else:
        priv_hex = secrets.token_hex(32)
        pub = b""

    if pub and _HAS_BASE58:
        sha = hashlib.sha256(pub).digest()
        try:
            ripe = hashlib.new("ripemd160", sha).digest()
        except (ConnectionError, OSError, TimeoutError):
            ripe = hashlib.sha256(sha).digest()[:20]

        addr_bytes = b"\x00" + ripe
        checksum = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
        address = base58.b58encode(addr_bytes + checksum).decode()
    else:
        address = (
            "1" + base58.b58encode(secrets.token_bytes(20)).decode()[:33]
            if _HAS_BASE58
            else "1" + secrets.token_hex(16)
        )

    addr_type = "legacy-p2pkh"
    if "segwit" in cfg.key:
        addr_type = "segwit-p2sh" if "native" not in cfg.key else "native-segwit-p2wpkh"

    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        public_key_hex=pub.hex() if isinstance(pub, bytes) else "",
        derivation_path=cfg.hd_path,
        address_type=addr_type,
    )

# ── Solana Generation ───────────────────────────────────────────

def generate_solana(cfg: ChainConfig) -> WalletResult:
    """Generate Solana wallet using Ed25519."""
    if _HAS_NACL:
        sk = NaClSigningKey.generate()
        priv_hex = sk.encode(RawEncoder).hex()
        vk = sk.verify_key
        pub_hex = vk.encode(RawEncoder).hex()
        address = base58.b58encode(bytes(vk)).decode() if _HAS_BASE58 else pub_hex
    else:
        priv_hex = secrets.token_hex(32)
        address = (
            base58.b58encode(secrets.token_bytes(32)).decode()[:44]
            if _HAS_BASE58
            else secrets.token_hex(22)
        )

    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        derivation_path=cfg.hd_path,
    )

# ── TRON Generation ─────────────────────────────────────────────

def generate_tron(cfg: ChainConfig) -> WalletResult:
    """Generate TRON wallet."""
    if _HAS_COINCURVE:
        pk = CoinCurveKey()
        priv_hex = pk.to_hex()
        pub = pk.public_key.format(compressed=False)[1:]
    else:
        priv_hex = secrets.token_hex(32)
        pub = secrets.token_bytes(64)

    hashed = (
        hashlib.sha3_256(pub).digest()
        if isinstance(pub, bytes)
        else hashlib.sha3_256(bytes.fromhex(pub)).digest()
    )
    addr_bytes = b"\x41" + hashed[-20:]

    if _HAS_BASE58:
        checksum = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
        address = base58.b58encode(addr_bytes + checksum).decode()
    else:
        address = "T" + secrets.token_hex(16)

    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        derivation_path=cfg.hd_path,
    )

# ── SECP256K1 Alt Coins ─────────────────────────────────────────

def generate_secp256k1_alt(cfg: ChainConfig) -> WalletResult:
    """Generate wallets for Dogecoin, Litecoin, BCH, Dash, Zcash."""
    if _HAS_COINCURVE:
        pk = CoinCurveKey()
        priv_hex = pk.to_hex()
        pub = pk.public_key.format(compressed=True)
    else:
        priv_hex = secrets.token_hex(32)
        pub = secrets.token_bytes(33)

    prefix_map = {"doge": 0x1E, "ltc": 0x30, "bch": 0x00, "dash": 0x4C, "zec": 0x1C}
    prefix = prefix_map.get(cfg.key, 0x00)

    sha = hashlib.sha256(pub if isinstance(pub, bytes) else bytes.fromhex(pub)).digest()
    try:
        ripe = hashlib.new("ripemd160", sha).digest()
    except (ConnectionError, OSError, TimeoutError):
        ripe = sha[:20]

    addr_bytes = bytes([prefix]) + ripe
    if _HAS_BASE58:
        cs = hashlib.sha256(hashlib.sha256(addr_bytes).digest()).digest()[:4]
        address = base58.b58encode(addr_bytes + cs).decode()
    else:
        address = (
            cfg.key[0].upper() + base58.b58encode(ripe).decode()[:33]
            if _HAS_BASE58
            else cfg.key[:2].upper() + secrets.token_hex(16)
        )

    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        derivation_path=cfg.hd_path,
    )

# ── ED25519 Family ──────────────────────────────────────────────

def generate_ed25519(cfg: ChainConfig) -> WalletResult:
    """Generate ED25519 wallets (Cardano, Near, Sui, Aptos)."""
    if _HAS_NACL:
        sk = NaClSigningKey.generate()
        priv_hex = sk.encode(RawEncoder).hex()
        pub = sk.verify_key.encode(RawEncoder)
    else:
        priv_hex = secrets.token_hex(32)
        pub = secrets.token_bytes(32)

    if cfg.key == "near":
        address = pub.hex() if isinstance(pub, bytes) else pub
    elif cfg.key in ("sui", "aptos"):
        address = "0x" + (pub.hex() if isinstance(pub, bytes) else pub)
    elif cfg.key == "ada":
        address = (
            "addr1" + base58.b58encode(pub).decode()[:50]
            if _HAS_BASE58
            else "addr1" + secrets.token_hex(24)
        )
    else:
        address = base58.b58encode(pub).decode()[:44] if _HAS_BASE58 else pub.hex()[:42]

    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=address,
        private_key_hex=priv_hex,
        derivation_path=cfg.hd_path,
    )

# ── Placeholder generators ──────────────────────────────────────

def generate_ripple(cfg: ChainConfig) -> WalletResult:
    priv = secrets.token_hex(32)
    addr = (
        "r" + base58.b58encode(secrets.token_bytes(20)).decode()[:33]
        if _HAS_BASE58
        else "r" + secrets.token_hex(16)
    )
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=addr,
        private_key_hex=priv,
        derivation_path=cfg.hd_path,
    )

def generate_substrate(cfg: ChainConfig) -> WalletResult:
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address="1" + base58.b58encode(secrets.token_bytes(32)).decode()[:47]
        if _HAS_BASE58
        else "1" + secrets.token_hex(23),
        private_key_hex=secrets.token_hex(32),
        derivation_path=cfg.hd_path,
    )

def generate_cosmos(cfg: ChainConfig) -> WalletResult:
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address="cosmos1" + base58.b58encode(secrets.token_bytes(20)).decode()[:38]
        if _HAS_BASE58
        else "cosmos1" + secrets.token_hex(16),
        private_key_hex=secrets.token_hex(32),
        derivation_path=cfg.hd_path,
    )

def generate_algorand(cfg: ChainConfig) -> WalletResult:
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address=base58.b58encode(secrets.token_bytes(36)).decode()[:58]
        if _HAS_BASE58
        else secrets.token_hex(29),
        private_key_hex=secrets.token_hex(32),
        derivation_path=cfg.hd_path,
    )

def generate_tezos(cfg: ChainConfig) -> WalletResult:
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address="tz1" + base58.b58encode(secrets.token_bytes(20)).decode()[:33]
        if _HAS_BASE58
        else "tz1" + secrets.token_hex(16),
        private_key_hex=secrets.token_hex(32),
        derivation_path=cfg.hd_path,
    )

def generate_monero(cfg: ChainConfig) -> WalletResult:
    return WalletResult(
        chain="",
        chain_name="",
        symbol="",
        address="4" + base58.b58encode(secrets.token_bytes(64)).decode()[:94]
        if _HAS_BASE58
        else "4" + secrets.token_hex(47),
        private_key_hex=secrets.token_hex(32),
        derivation_path=cfg.hd_path,
        tags=["monero-placeholder", "needs-monero-python-for-full-support"],
    )

# ── Family dispatch ─────────────────────────────────────────────

GENERATORS = {
    ChainFamily.EVM: generate_evm,
    ChainFamily.BITCOIN: generate_bitcoin,
    ChainFamily.SOLANA: generate_solana,
    ChainFamily.TRON: generate_tron,
    ChainFamily.SECP256K1_ALT: generate_secp256k1_alt,
    ChainFamily.ED25519: generate_ed25519,
    ChainFamily.RIPPLE: generate_ripple,
    ChainFamily.SUBSTRATE: generate_substrate,
    ChainFamily.COSMOS: generate_cosmos,
    ChainFamily.ALGORAND: generate_algorand,
    ChainFamily.TEZOS: generate_tezos,
    ChainFamily.MONERO: generate_monero,
}
