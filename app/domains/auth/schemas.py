"""Pydantic models for auth API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmailLoginRequest(BaseModel):
    email: str
    password: str

class EmailRegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    wallet_address: str | None = None
    wallet_chain: str | None = None

class WalletNonceRequest(BaseModel):
    address: str
    chain: str

class WalletVerifyRequest(BaseModel):
    address: str
    chain: str
    nonce: str
    signature: str
    message: str

class NonceResponse(BaseModel):
    nonce: str
    timestamp: str
    message: str | None = None

class WalletAuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict[str, Any]

class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    wallet_address: str | None
    wallet_chain: str | None
    tier: str
    role: str
    created_at: str
    xp: int = 0
    level: int = 1
    badges: list[str] = []

class GoogleAuthResponse(BaseModel):
    url: str

class TelegramAuthRequest(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str

# ── 2FA Models ──
class TwoFASetupResponse(BaseModel):
    secret: str
    qr_code: str
    uri: str
    backup_codes: list[str]

class TwoFAEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")

class TwoFAVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")

class TwoFALoginRequest(BaseModel):
    email: str
    password: str
    code: str = Field(..., min_length=6, max_length=10)

class TwoFAStatusResponse(BaseModel):
    enabled: bool
    method: str = "totp"
    created_at: str | None = None
    last_used: str | None = None
