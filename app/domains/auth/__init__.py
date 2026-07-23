"""
Auth domain - Authentication system.
======================================
Backward-compatible re-exports from focused submodules.
"""

from app.domains.auth.jwt import (
    JWT_ALGORITHM,
    JWT_EXPIRY_DAYS,
    JWT_SECRET,
    _JWT_PRIVATE_KEY,
    _JWT_PUBLIC_KEY,
    _create_jwt,
    _derive_user_id,
    _load_key,
    _verify_jwt,
    generate_nonce,
)
from app.domains.auth.passwords import (
    hash_password,
    verify_password,
)
from app.domains.auth.store import (
    _get_user,
    _get_user_by_email,
    _is_valid_email,
    _save_user,
)
from app.domains.auth.schemas import (
    EmailLoginRequest,
    EmailRegisterRequest,
    GoogleAuthResponse,
    NonceResponse,
    TelegramAuthRequest,
    TwoFAEnableRequest,
    TwoFALoginRequest,
    TwoFASetupResponse,
    TwoFAStatusResponse,
    TwoFAVerifyRequest,
    UserResponse,
    WalletAuthResponse,
    WalletNonceRequest,
    WalletVerifyRequest,
)
from app.domains.auth.cookie import (
    _clear_auth_cookie,
    _is_localhost,
    _set_auth_cookie,
    _set_csrf_cookie,
)
from app.domains.auth.totp import (
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
    twofa_disable,
    twofa_enable,
    twofa_login,
    twofa_setup,
    twofa_status,
    twofa_verify,
)
from app.domains.auth.oauth import (
    github_auth_url,
    github_callback,
    google_auth_url,
    google_callback,
    google_callback_post,
    x_auth_url,
    x_callback,
)
from app.domains.auth.deps import (
    _extract_token,
    get_current_user,
    require_auth,
    require_public_profile,
)
from app.domains.auth.router import router
from app.domains.auth.routes_email import (
    login_email,
    register_email,
)
from app.domains.auth.routes_telegram import (
    telegram_auth,
)
from app.domains.auth.routes_profile import (
    cookie_token,
    logout,
    user_profile_me,
)
from app.domains.auth.wallet import (
    get_or_create_wallet_user,
    verify_wallet_signature,
)

__all__ = [
    "JWT_ALGORITHM", "JWT_EXPIRY_DAYS", "JWT_SECRET",
    "_JWT_PRIVATE_KEY", "_JWT_PUBLIC_KEY",
    "_create_jwt", "_derive_user_id", "_load_key", "_verify_jwt", "generate_nonce",
    "hash_password", "verify_password",
    "_get_user", "_get_user_by_email", "_is_valid_email", "_save_user",
    "EmailLoginRequest", "EmailRegisterRequest", "GoogleAuthResponse",
    "NonceResponse", "TelegramAuthRequest",
    "TwoFAEnableRequest", "TwoFALoginRequest", "TwoFASetupResponse",
    "TwoFAStatusResponse", "TwoFAVerifyRequest",
    "UserResponse", "WalletAuthResponse", "WalletNonceRequest", "WalletVerifyRequest",
    "_clear_auth_cookie", "_is_localhost", "_set_auth_cookie", "_set_csrf_cookie",
    "PYOTP_AVAILABLE", "TOTP_DIGITS", "TOTP_INTERVAL", "TOTP_ISSUER", "TOTP_WINDOW",
    "_build_totp", "_generate_backup_codes", "_generate_qr_base64",
    "_generate_totp_secret", "_get_totp_uri", "_hash_backup_code",
    "_verify_backup_code", "_verify_totp",
    "twofa_disable", "twofa_enable", "twofa_login",
    "twofa_setup", "twofa_status", "twofa_verify",
    "github_auth_url", "github_callback",
    "google_auth_url", "google_callback", "google_callback_post",
    "x_auth_url", "x_callback",
    "_extract_token", "get_current_user", "require_auth", "require_public_profile",
    "router",
    "login_email", "register_email",
    "telegram_auth",
    "cookie_token", "logout", "user_profile_me",
    "get_or_create_wallet_user", "verify_wallet_signature",
]
