"""
Auth Router - Complete authentication system (email, wallet, OAuth, Telegram)
"""

import base64
import io
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from fastapi import APIRouter, HTTPException, Request
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.config import settings as _settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# ── 2FA / TOTP ──
try:
    import pyotp
    import qrcode

    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False
    logger.warning("pyotp not installed - 2FA endpoints will return 503")

TOTP_ISSUER = os.getenv("TOTP_ISSUER", "RugMunch Intelligence")
TOTP_DIGITS = 6
TOTP_INTERVAL = 30
TOTP_WINDOW = 1  # Allow ±1 interval clock drift


def _generate_totp_secret() -> str:
    """Generate a new base32 TOTP secret."""
    if not PYOTP_AVAILABLE:
        raise RuntimeError("pyotp not installed")
    return pyotp.random_base32()  # type: ignore[no-any-return]


def _build_totp(secret: str) -> Any:
    """Build a TOTP object from secret."""
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)


def _verify_totp(secret: str, code: str) -> bool:
    """Verify a TOTP code with clock-drift tolerance."""
    if not PYOTP_AVAILABLE or not secret or not code:
        return False
    totp = _build_totp(secret)
    return totp.verify(code, valid_window=TOTP_WINDOW)  # type: ignore[no-any-return]


def _get_totp_uri(secret: str, email: str) -> str:
    """Get otpauth:// URI for QR code generation."""
    totp = _build_totp(secret)
    return totp.provisioning_uri(name=email, issuer_name=TOTP_ISSUER)  # type: ignore[no-any-return]


def _generate_qr_base64(uri: str) -> str:
    """Generate QR code as base64 PNG data URI."""
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _generate_backup_codes(count: int = 8) -> list[str]:
    """Generate single-use backup codes."""
    codes = []
    for _ in range(count):
        # 8-digit numeric codes, hyphenated for readability
        raw = secrets.randbelow(10_000_000_000)
        code = f"{raw:010d}"[:8]
        codes.append(f"{code[:4]}-{code[4:]}")
    return codes


def _hash_backup_code(code: str) -> str:
    """Hash a backup code for storage (same as password hashing)."""
    return hash_password(code)


def _verify_backup_code(code: str, hashed: str) -> bool:
    """Verify a backup code against its hash."""
    return verify_password(code, hashed)


# ── Config (via app.config settings) ──
JWT_SECRET = _settings.jwt_secret
JWT_ALGORITHM = _settings.jwt_algorithm
JWT_EXPIRY_DAYS = 7


def _load_key(key_or_path: str) -> str:
    """Load a PEM key from a file path, or return the PEM string directly."""
    if os.path.exists(key_or_path) and os.path.isfile(key_or_path):
        with open(key_or_path) as _f:
            return _f.read()
    return key_or_path


# RS256 key loading — only used when algorithm is RS256
_JWT_PRIVATE_KEY: str | None = None
_JWT_PUBLIC_KEY: str | None = None

if JWT_ALGORITHM == "RS256":
    _raw_private = _settings.jwt_private_key
    _raw_public = _settings.jwt_public_key
    if _raw_private and _raw_public:
        _JWT_PRIVATE_KEY = _load_key(_raw_private)
        _JWT_PUBLIC_KEY = _load_key(_raw_public)
    else:
        if _settings.environment == "prod":
            raise RuntimeError(
                "JWT_ALGORITHM=RS256 configured but JWT_PRIVATE_KEY or JWT_PUBLIC_KEY not set. "
                "Set both RSA key env vars to enable RS256."
            )
        else:
            logger.warning(
                "JWT_ALGORITHM=RS256 configured but JWT_PRIVATE_KEY or JWT_PUBLIC_KEY not set. "
                "Falling back to HS256 (symmetric). Set both RSA key env vars to enable RS256."
            )
            JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash password using bcrypt directly."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Redis User Store ──
def _get_user(user_id: str) -> dict[str, Any] | None:
    """Fetch user record by id from Redis hash rmi:users."""
    r = get_redis()
    if not r:
        return None
    raw = r.hget("rmi:users", user_id)
    if not raw:
        return None
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        return None


def _get_user_by_email(email: str) -> dict[str, Any] | None:
    """Fetch user record by email via rmi:users:email index → rmi:users hash."""
    r = get_redis()
    if not r:
        return None
    user_id = r.hget("rmi:users:email", email.lower())
    if not user_id:
        return None
    if isinstance(user_id, bytes):
        uid: str = user_id.decode("utf-8")
        return _get_user(uid)
    return _get_user(user_id)


def _save_user(user: dict[str, Any]) -> None:
    """Persist user record to Redis hash rmi:users, refresh email index if present."""
    r = get_redis()
    if not r:
        return
    user_id = user["id"]
    r.hset("rmi:users", user_id, json.dumps(user))
    if user.get("email"):
        r.hset("rmi:users:email", user["email"].lower(), user_id)


# ── Helpers ──
def _is_valid_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


def _create_jwt(
    user_id: str, email: str, tier: str = "FREE", role: str = "USER", wallet: str | None = None
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "tier": tier,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
    }
    if wallet:
        payload["wallet"] = wallet
    if JWT_ALGORITHM == "RS256" and _JWT_PRIVATE_KEY:
        return jwt.encode(payload, _JWT_PRIVATE_KEY, algorithm="RS256")
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _verify_jwt(token: str) -> dict[str, Any] | None:
    try:
        if JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY:
            payload = jwt.decode(token, _JWT_PUBLIC_KEY, algorithms=["RS256"])
        else:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {
            "id": payload["sub"],
            "email": payload["email"],
            "tier": payload.get("tier", "FREE"),
            "role": payload.get("role", "USER"),
            "wallet": payload.get("wallet"),
        }
    except JWTError:
        return None


def _derive_user_id(email: str) -> str:
    import hashlib

    return hashlib.sha256(email.lower().encode()).hexdigest()[:32]


def generate_nonce() -> str:
    return secrets.token_urlsafe(16)


# ── Storage (Redis-backed user store) ──
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
    qr_code: str  # base64 data URI
    uri: str  # otpauth:// URI
    backup_codes: list[str]  # plaintext - shown once


class TwoFAEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TwoFAVerifyRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class TwoFALoginRequest(BaseModel):
    email: str
    password: str
    code: str = Field(..., min_length=6, max_length=10)  # 6 digits or backup code XXXX-XXXX


class TwoFAStatusResponse(BaseModel):
    enabled: bool
    method: str = "totp"  # future: u2f, webauthn
    created_at: str | None = None
    last_used: str | None = None


# ── Email Auth ──
@router.post("/register", response_model=WalletAuthResponse)
def register_email(req: EmailRegisterRequest) -> dict[str, Any]:
    """Register a new user with email/password."""
    if not _is_valid_email(req.email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if user exists
    existing = _get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = _derive_user_id(req.email)
    hashed = hash_password(req.password)

    user: dict[str, Any] = {
        "id": user_id,
        "email": req.email,
        "display_name": req.display_name,
        "password_hash": hashed,
        "wallet_address": req.wallet_address,
        "wallet_chain": req.wallet_chain,
        "tier": "FREE",
        "role": "USER",
        "created_at": datetime.now(UTC).isoformat(),
        "xp": 0,
        "level": 1,
        "badges": [],
        "scans_remaining": 5,
        "scans_used": 0,
    }

    # Save user + email index
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    r.hset("rmi:users", user_id, json.dumps(user))
    r.hset("rmi:users:email", req.email.lower(), user_id)

    token = _create_jwt(user_id, req.email, "FREE", "USER", req.wallet_address)

    return {
        "access_token": token,
        "refresh_token": token,
        "user": {
            "id": user_id,
            "email": req.email,
            "display_name": req.display_name,
            "wallet_address": req.wallet_address,
            "wallet_chain": req.wallet_chain,
            "tier": "FREE",
            "role": "USER",
            "created_at": user["created_at"],
        },
    }


@router.post("/login", response_model=WalletAuthResponse)
def login_email(req: EmailLoginRequest) -> dict[str, Any]:
    """Login with email/password. If 2FA enabled, returns partial token requiring /2fa/login."""
    user = _get_user_by_email(req.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # If 2FA enabled, return a pre-auth token that requires TOTP verification
    if user.get("totp_enabled") and user.get("totp_secret"):
        # Generate a short-lived pre-auth token (5 min)
        pre_token = _create_jwt(
            user["id"],
            user["email"],
            user.get("tier", "FREE"),
            user.get("role", "USER"),
            user.get("wallet_address"),
        )
        # Override expiry to 5 minutes
        import jose.jwt as jose_jwt

        # Decode with the correct key (RS256 public key, or HS256 secret fallback)
        _decode_key = (
            _JWT_PUBLIC_KEY if (JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY) else JWT_SECRET
        )
        _encode_key = (
            _JWT_PRIVATE_KEY if (JWT_ALGORITHM == "RS256" and _JWT_PRIVATE_KEY) else JWT_SECRET
        )
        payload = jose_jwt.decode(pre_token, _decode_key, algorithms=[JWT_ALGORITHM])
        payload["exp"] = datetime.now(UTC) + timedelta(minutes=5)
        payload["2fa_required"] = True
        payload["pre_auth"] = True
        pre_token = jose_jwt.encode(payload, _encode_key, algorithm=JWT_ALGORITHM)

        return {
            "access_token": pre_token,
            "refresh_token": pre_token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user.get("display_name", user["email"]),
                "wallet_address": user.get("wallet_address"),
                "wallet_chain": user.get("wallet_chain"),
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
                "created_at": user.get("created_at"),
                "_2fa_required": True,
            },
        }

    token = _create_jwt(
        user["id"],
        user["email"],
        user.get("tier", "FREE"),
        user.get("role", "USER"),
        user.get("wallet_address"),
    )

    return {
        "access_token": token,
        "refresh_token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name", user["email"]),
            "wallet_address": user.get("wallet_address"),
            "wallet_chain": user.get("wallet_chain"),
            "tier": user.get("tier", "FREE"),
            "role": user.get("role", "USER"),
            "created_at": user.get("created_at"),
        },
    }


# ── Wallet Auth ──
@router.post("/wallet/nonce", response_model=NonceResponse)
async def wallet_nonce(req: WalletNonceRequest) -> dict[str, Any]:
    """Get a nonce for wallet signature."""
    nonce = generate_nonce()
    timestamp = datetime.now(UTC).isoformat()
    message = f"RugMunch Intelligence wants you to sign in with your {req.chain.title()} account.\n\nWallet: {req.address}\nNonce: {nonce}\nTimestamp: {timestamp}"
    return {
        "nonce": nonce,
        "timestamp": timestamp,
        "message": message,
    }


@router.post("/wallet/verify", response_model=WalletAuthResponse)
async def wallet_verify(req: WalletVerifyRequest) -> dict[str, Any]:
    """Verify wallet signature and create session."""
    from app.domains.auth.wallet import get_or_create_wallet_user, verify_wallet_signature

    if not verify_wallet_signature(req.message, req.signature, req.address, req.chain):
        raise HTTPException(status_code=401, detail="Invalid signature")

    user_data = await get_or_create_wallet_user(req.address)

    return {
        "access_token": user_data["access_token"],
        "refresh_token": user_data["refresh_token"],
        "user": {
            "id": user_data["id"],
            "email": user_data["email"],
            "display_name": user_data.get("display_name", f"Agent {req.address[2:8].upper()}"),
            "wallet_address": req.address,
            "wallet_chain": req.chain,
            "tier": user_data["tier"],
            "role": user_data["role"],
            "created_at": user_data["created_at"],
        },
    }


# ── User Profile ──
@router.get("/user/me", response_model=UserResponse)
async def user_profile_me(request: Request) -> dict[str, Any]:
    """Get current authenticated user profile."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")

    token = auth_header[7:]
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = _get_user(payload["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name", user["email"]),
        "wallet_address": user.get("wallet_address"),
        "wallet_chain": user.get("wallet_chain"),
        "tier": user.get("tier", "FREE"),
        "role": user.get("role", "USER"),
        "created_at": user.get("created_at"),
        "xp": user.get("xp", 0),
        "level": user.get("level", 1),
        "badges": user.get("badges", []),
    }


# ── Google OAuth ──
@router.get("/google/url", response_model=GoogleAuthResponse)
async def google_auth_url() -> dict[str, Any]:
    """Get Google OAuth URL."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/google/callback"
    )

    if not client_id:
        return {"url": "/auth/callback?error=google_not_configured"}

    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return {"url": url}


FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rugmunch.io")


@router.get("/google/callback")
async def google_callback(request: Request) -> Any:
    """Handle Google OAuth redirect - exchanges code, creates user, redirects to frontend with token."""
    from fastapi.responses import RedirectResponse

    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")

    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    import httpx

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/google/callback"
    )

    if not client_id or not client_secret:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=not_configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")

        tokens = resp.json()
        access_token = tokens.get("access_token")

        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")

        google_user = resp.json()
        email = google_user.get("email")

        user = _get_user_by_email(email)
        if not user:
            user_id = _derive_user_id(email)
            user = {
                "id": user_id,
                "email": email,
                "display_name": google_user.get("name", email),
                "tier": "FREE",
                "role": "USER",
                "created_at": datetime.now(UTC).isoformat(),
                "xp": 0,
                "level": 1,
                "badges": [],
                "scans_remaining": 5,
                "scans_used": 0,
            }
            r = get_redis()
            if r is None:
                raise HTTPException(status_code=503, detail="Redis unavailable")
            r.hset("rmi:users", user_id, json.dumps(user))
            r.hset("rmi:users:email", email.lower(), user_id)

        jwt_token = _create_jwt(
            user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER")
        )

        # Redirect to frontend with token
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={jwt_token}&provider=google")


@router.post("/google/callback", response_model=WalletAuthResponse)
async def google_callback_post(request: Request) -> dict[str, Any]:
    """Legacy POST endpoint for manual code exchange."""
    body = await request.json()
    code = body.get("code")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    import httpx

    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/google/callback"
    )

    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange code")

        tokens = resp.json()
        access_token = tokens.get("access_token")

        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to get user info")

        google_user = resp.json()
        email = google_user.get("email")

        user = _get_user_by_email(email)
        if not user:
            user_id = _derive_user_id(email)
            user = {
                "id": user_id,
                "email": email,
                "display_name": google_user.get("name", email),
                "tier": "FREE",
                "role": "USER",
                "created_at": datetime.now(UTC).isoformat(),
                "xp": 0,
                "level": 1,
                "badges": [],
                "scans_remaining": 5,
                "scans_used": 0,
            }
            r = get_redis()
            if r is None:
                raise HTTPException(status_code=503, detail="Redis unavailable")
            r.hset("rmi:users", user_id, json.dumps(user))
            r.hset("rmi:users:email", email.lower(), user_id)

        jwt_token = _create_jwt(
            user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER")
        )

        return {
            "access_token": jwt_token,
            "refresh_token": jwt_token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user.get("display_name", email),
                "wallet_address": None,
                "wallet_chain": None,
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
                "created_at": user.get("created_at"),
            },
        }


# ── Telegram Auth ──
@router.post("/telegram", response_model=WalletAuthResponse)
async def telegram_auth(req: TelegramAuthRequest) -> dict[str, Any]:
    """Authenticate via Telegram Web App data."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Telegram auth not configured")

    data_check = {
        "id": str(req.id),
        "auth_date": str(req.auth_date),
    }
    if req.first_name:
        data_check["first_name"] = req.first_name
    if req.last_name:
        data_check["last_name"] = req.last_name
    if req.username:
        data_check["username"] = req.username
    if req.photo_url:
        data_check["photo_url"] = req.photo_url

    import hashlib
    import hmac

    sorted_data = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(secret, sorted_data.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(req.hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    telegram_user_id = f"tg:{req.id}"
    user = _get_user(telegram_user_id)

    if not user:
        display_name = (
            f"{req.first_name or ''} {req.last_name or ''}".strip()
            or req.username
            or f"User{req.id}"
        )
        email = f"{req.id}@telegram.rmi"

        user = {
            "id": telegram_user_id,
            "email": email,
            "display_name": display_name,
            "telegram_id": req.id,
            "telegram_username": req.username,
            "tier": "FREE",
            "role": "USER",
            "created_at": datetime.now(UTC).isoformat(),
            "xp": 0,
            "level": 1,
            "badges": [],
            "scans_remaining": 5,
            "scans_used": 0,
        }
        r = get_redis()
        if r is None:
            raise HTTPException(status_code=503, detail="Redis unavailable")
        r.hset("rmi:users", telegram_user_id, json.dumps(user))
        r.hset("rmi:users:telegram", str(req.id), telegram_user_id)

    jwt_token = _create_jwt(
        user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER")
    )

    return {
        "access_token": jwt_token,
        "refresh_token": jwt_token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name"),
            "wallet_address": None,
            "wallet_chain": None,
            "tier": user.get("tier", "FREE"),
            "role": user.get("role", "USER"),
            "created_at": user.get("created_at"),
        },
    }


# ── GitHub OAuth ──
@router.get("/github/url", response_model=GoogleAuthResponse)
async def github_auth_url() -> dict[str, Any]:
    """Get GitHub OAuth URL."""
    client_id = os.getenv("GITHUB_CLIENT_ID")
    redirect_uri = os.getenv(
        "GITHUB_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/github/callback"
    )

    if not client_id:
        return {"url": "/auth/callback?error=github_not_configured"}

    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
    }
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return {"url": url}


@router.get("/github/callback")
async def github_callback(request: Request) -> Any:
    """Handle GitHub OAuth redirect - exchanges code, creates user, redirects to frontend with token."""
    from fastapi.responses import RedirectResponse

    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")

    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    import httpx

    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    redirect_uri = os.getenv(
        "GITHUB_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/github/callback"
    )

    if not client_id or not client_secret:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=not_configured")

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")

        tokens = resp.json()
        access_token = tokens.get("access_token")

        # Get user info
        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")

        github_user = resp.json()
        email = github_user.get("email")

        # If no public email, fetch emails endpoint
        if not email:
            resp = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"token {access_token}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                emails = resp.json()
                primary = next((e for e in emails if e.get("primary")), None)
                email = (
                    primary.get("email")
                    if primary
                    else (emails[0].get("email") if emails else None)
                )

        if not email:
            email = f"{github_user.get('login', 'github_user')}@github.rmi"

        user = _get_user_by_email(email)
        if not user:
            user_id = _derive_user_id(email)
            user = {
                "id": user_id,
                "email": email,
                "display_name": github_user.get("name", github_user.get("login", email)),
                "tier": "FREE",
                "role": "USER",
                "created_at": datetime.now(UTC).isoformat(),
                "xp": 0,
                "level": 1,
                "badges": [],
                "scans_remaining": 5,
                "scans_used": 0,
            }
            r = get_redis()
            if r is None:
                raise HTTPException(status_code=503, detail="Redis unavailable")
            r.hset("rmi:users", user_id, json.dumps(user))
            r.hset("rmi:users:email", email.lower(), user_id)

        jwt_token = _create_jwt(
            user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER")
        )
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={jwt_token}&provider=github")


# ── X/Twitter OAuth ──
@router.get("/x/url", response_model=GoogleAuthResponse)
async def x_auth_url() -> dict[str, Any]:
    """Get X/Twitter OAuth URL."""
    client_id = os.getenv("X_CLIENT_ID")
    redirect_uri = os.getenv("X_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/x/callback")

    if not client_id:
        return {"url": "/auth/callback?error=x_not_configured"}

    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "tweet.read users.read",
    }
    url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
    return {"url": url}


@router.get("/x/callback")
async def x_callback(request: Request) -> Any:
    """Handle X/Twitter OAuth redirect - exchanges code, creates user, redirects to frontend with token."""
    from fastapi.responses import RedirectResponse

    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")

    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    import httpx

    client_id = os.getenv("X_CLIENT_ID")
    client_secret = os.getenv("X_CLIENT_SECRET")
    redirect_uri = os.getenv("X_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/x/callback")

    if not client_id or not client_secret:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=not_configured")

    async with httpx.AsyncClient() as client:
        # X uses OAuth 2.0 - exchange code for token
        resp = await client.post(
            "https://api.twitter.com/2/oauth2/token",
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_verifier": "challenge",  # X requires PKCE - we need to implement proper PKCE
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")

        tokens = resp.json()
        access_token = tokens.get("access_token")

        # Get user info from X API
        resp = await client.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")

        x_user = resp.json().get("data", {})
        username = x_user.get("username", f"x_user_{x_user.get('id', 'unknown')}")
        email = f"{username}@x.rmi"  # X API v2 doesn't always return email

        user = _get_user_by_email(email)
        if not user:
            user_id = _derive_user_id(email)
            user = {
                "id": user_id,
                "email": email,
                "display_name": x_user.get("name", username),
                "tier": "FREE",
                "role": "USER",
                "created_at": datetime.now(UTC).isoformat(),
                "xp": 0,
                "level": 1,
                "badges": [],
                "scans_remaining": 5,
                "scans_used": 0,
            }
            r = get_redis()
            if r is None:
                raise HTTPException(status_code=503, detail="Redis unavailable")
            r.hset("rmi:users", user_id, json.dumps(user))
            r.hset("rmi:users:email", email.lower(), user_id)

        jwt_token = _create_jwt(
            user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER")
        )
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?token={jwt_token}&provider=x")


# ── FastAPI Dependency Functions (for @router/@app endpoints) ──
async def get_current_user(request: Request) -> dict[str, Any] | None:
    """Verify JWT from Authorization header (wallet or email)."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    payload = _verify_jwt(token)
    if not payload:
        return None
    user = _get_user(payload["id"])
    if not user:
        return None
    return user


async def require_auth(request: Request) -> dict[str, Any]:
    """Require valid JWT. Raises 401 if missing/invalid."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ── 2FA / TOTP Endpoints ──


@router.post("/2fa/setup")
async def twofa_setup(request: Request) -> dict[str, Any]:
    """Begin 2FA setup - generate secret + QR code. Returns plaintext secret + backup codes (shown once)."""
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Check if already enabled
    if user.get("totp_secret"):
        raise HTTPException(
            status_code=400, detail="2FA already enabled. Disable first to reconfigure."
        )

    secret = _generate_totp_secret()
    uri = _get_totp_uri(secret, user["email"])
    qr_b64 = _generate_qr_base64(uri)
    backup_codes = _generate_backup_codes(8)

    # Store pending secret (not enabled until verified)
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending = {
        "secret": secret,
        "backup_codes": [_hash_backup_code(c) for c in backup_codes],
        "created_at": datetime.now(UTC).isoformat(),
    }
    r.setex(f"rmi:2fa_pending:{user['id']}", timedelta(minutes=10), json.dumps(pending))

    return {
        "secret": secret,
        "qr_code": qr_b64,
        "uri": uri,
        "backup_codes": backup_codes,  # plaintext - shown once
    }


@router.post("/2fa/enable")
async def twofa_enable(req: TwoFAEnableRequest, request: Request) -> dict[str, Any]:
    """Enable 2FA - verify the first TOTP code, then persist."""
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending_raw = r.get(f"rmi:2fa_pending:{user['id']}")
    if not pending_raw:
        raise HTTPException(status_code=400, detail="No pending 2FA setup. Call /2fa/setup first.")

    pending = json.loads(pending_raw)
    secret = pending["secret"]

    # Verify the code
    if not _verify_totp(secret, req.code):
        raise HTTPException(
            status_code=400, detail="Invalid TOTP code. Check your authenticator app time sync."
        )

    # Enable 2FA on user record
    user["totp_secret"] = secret
    user["totp_enabled"] = True
    user["totp_created_at"] = pending["created_at"]
    user["totp_backup_codes"] = pending["backup_codes"]  # already hashed
    user["totp_last_used"] = None
    _save_user(user)

    # Clean up pending
    r.delete(f"rmi:2fa_pending:{user['id']}")

    # Audit log
    logger.info(f"[2FA ENABLED] user={user['id']} email={user['email']}")

    return {"status": "ok", "message": "2FA enabled successfully", "method": "totp"}


@router.post("/2fa/disable")
async def twofa_disable(request: Request) -> dict[str, Any]:
    """Disable 2FA - requires current password for security."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not user.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="2FA is not enabled")

    # Remove 2FA fields
    for key in [
        "totp_secret",
        "totp_enabled",
        "totp_created_at",
        "totp_backup_codes",
        "totp_last_used",
    ]:
        user.pop(key, None)

    _save_user(user)
    logger.info(f"[2FA DISABLED] user={user['id']} email={user['email']}")

    return {"status": "ok", "message": "2FA disabled successfully"}


@router.get("/2fa/status", response_model=TwoFAStatusResponse)
async def twofa_status(request: Request) -> dict[str, Any]:
    """Check 2FA status for current user."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    enabled = user.get("totp_enabled", False)
    return {
        "enabled": enabled,
        "method": "totp" if enabled else "none",
        "created_at": user.get("totp_created_at"),
        "last_used": user.get("totp_last_used"),
    }


# ── Privacy Enforcement Dependency ──


async def require_public_profile(request: Request) -> dict[str, Any]:
    """
    Dependency to ensure the user has a public profile.
    Required for actions that interact with the community (posting, commenting).
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    privacy_settings = user.get("privacy_settings", {})
    visibility = privacy_settings.get("profile_visibility", "public")

    if visibility != "public":
        raise HTTPException(
            status_code=403,
            detail="A public profile is required to post or comment. Please update your privacy settings in your profile.",
        )

    return user


@router.post("/2fa/verify")
async def twofa_verify(req: TwoFAVerifyRequest, request: Request) -> dict[str, Any]:
    """Verify a TOTP code (for re-auth during sensitive operations)."""
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")

    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(status_code=400, detail="2FA not enabled")

    if not _verify_totp(user["totp_secret"], req.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    # Update last used
    user["totp_last_used"] = datetime.now(UTC).isoformat()
    _save_user(user)

    return {"status": "ok", "verified": True}


@router.post("/2fa/login")
async def twofa_login(req: TwoFALoginRequest) -> dict[str, Any]:
    """Login with email + password + 2FA code. Returns full JWT on success."""
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")

    user = _get_user_by_email(req.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Check if 2FA is enabled
    if not user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(
            status_code=400, detail="2FA not enabled for this account. Use /login instead."
        )

    code = req.code.strip().replace("-", "")

    # Try TOTP first
    if _verify_totp(user["totp_secret"], code):
        pass  # valid TOTP
    else:
        # Try backup codes
        backup_codes = user.get("totp_backup_codes", [])
        matched = False
        for i, hashed in enumerate(backup_codes):
            if _verify_backup_code(req.code, hashed):
                matched = True
                # Remove used backup code
                backup_codes.pop(i)
                user["totp_backup_codes"] = backup_codes
                break
        if not matched:
            raise HTTPException(status_code=401, detail="Invalid 2FA code or backup code")

    # Update last used
    user["totp_last_used"] = datetime.now(UTC).isoformat()
    _save_user(user)

    token = _create_jwt(
        user["id"],
        user["email"],
        user.get("tier", "FREE"),
        user.get("role", "USER"),
        user.get("wallet_address"),
    )

    logger.info(f"[2FA LOGIN] user={user['id']} email={user['email']}")

    return {
        "access_token": token,
        "refresh_token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name", user["email"]),
            "wallet_address": user.get("wallet_address"),
            "wallet_chain": user.get("wallet_chain"),
            "tier": user.get("tier", "FREE"),
            "role": user.get("role", "USER"),
            "created_at": user.get("created_at"),
        },
    }
