"""TOTP / 2FA helpers - re-exports from app.domains.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- _generate_totp_secret, _build_totp, _verify_totp, _get_totp_uri
- _generate_qr_base64, _generate_backup_codes, _hash_backup_code, _verify_backup_code
- TOTP_ISSUER, PYOTP_AVAILABLE
"""

from app.domains.auth import (  # noqa: F401
    PYOTP_AVAILABLE,
    TOTP_DIGITS,
    TOTP_INTERVAL,
    TOTP_ISSUER,
    TOTP_WINDOW,
    _build_totp,
    _generate_backup_codes,
    _generate_qr_base64,
    _generate_totp_secret,
    _get_totp_uri,
    _hash_backup_code,
    _verify_backup_code,
    _verify_totp,
)
