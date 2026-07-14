"""Pydantic schemas - re-exports from app.domains.auth.

Phase 3B of AUDIT-2026-Q3.md.

Public API:
- EmailLoginRequest, EmailRegisterRequest
- WalletNonceRequest, WalletVerifyRequest, WalletAuthResponse
- NonceResponse, UserResponse, GoogleAuthResponse
- TelegramAuthRequest, TwoFAEnableRequest, TwoFALoginRequest,
  TwoFASetupResponse, TwoFAStatusResponse, TwoFAVerifyRequest
"""

from app.domains.auth import (  # noqa: F401
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
