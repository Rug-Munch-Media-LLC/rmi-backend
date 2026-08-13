"""
Wallet Factory - Cryptographic Utilities
=========================================
Library availability flags and AES-256-GCM encryption utilities.
"""

from __future__ import annotations

import base64
import secrets

from app.core.logging import get_logger

logger = get_logger("wallet_factory")

# ── Crypto library availability flags ────────────────────────────
try:
    from coincurve import PrivateKey as CoinCurveKey  # noqa: F401 — availability probe

    _HAS_COINCURVE = True
except ImportError:
    _HAS_COINCURVE = False

try:
    from ecdsa import NIST256p, NIST384p, NIST521p, SECP256k1, SigningKey  # noqa: F401 — availability probe

    _HAS_ECDSA = True
except ImportError:
    _HAS_ECDSA = False

try:
    import base58  # noqa: F401 — availability probe

    _HAS_BASE58 = True
except ImportError:
    _HAS_BASE58 = False

try:
    from nacl.encoding import RawEncoder  # noqa: F401 — availability probe
    from nacl.signing import SigningKey as NaClSigningKey  # noqa: F401 — availability probe

    _HAS_NACL = True
except ImportError:
    _HAS_NACL = False

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    _has_cryptography = True
except ImportError:
    _has_cryptography = False


def encrypt_key(plaintext: str, password: str) -> str:
    """AES-256-GCM encrypt with Argon2 key derivation."""
    if not _has_cryptography or not password:
        return plaintext

    salt = secrets.token_bytes(16)
    kdf = Argon2id(salt=salt, length=32, memory_cost=65536, time_cost=3, parallelism=4)  # type: ignore[call-arg]  # cryptography 49 rust stub hides Argon2id kwargs
    key = kdf.derive(password.encode())

    aesgcm = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)

    return base64.b64encode(salt + nonce + ciphertext).decode()


def decrypt_key(encrypted: str, password: str) -> str:
    """Decrypt an AES-256-GCM encrypted private key."""
    if not _has_cryptography or not password:
        return encrypted

    data = base64.b64decode(encrypted)
    salt, nonce, ciphertext = data[:16], data[16:28], data[28:]

    kdf = Argon2id(salt=salt, length=32, memory_cost=65536, time_cost=3, parallelism=4)  # type: ignore[call-arg]  # cryptography 49 rust stub hides Argon2id kwargs
    key = kdf.derive(password.encode())

    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
