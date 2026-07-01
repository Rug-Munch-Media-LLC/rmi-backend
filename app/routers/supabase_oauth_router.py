"""
Supabase OAuth Router — Social + Web3 Auth.
Supports: GitHub, Google (Gmail), Discord, X (Twitter), + Wallet (EVM/Solana).
Account linking: Connect multiple auth methods to one user account.

Supabase OAuth Providers (free tier):
- GitHub: Built-in, no approval needed
- Google: Requires OAuth consent screen
- Discord: Built-in, instant approval
- X/Twitter: Requires Twitter developer account
- Others: GitLab, Bitbucket, Slack, LinkedIn, etc.

Flow:
1. GET /oauth/{provider} → Returns Supabase OAuth URL
2. User authenticates with provider
3. Provider redirects to /oauth/callback/{provider}
4. We get Supabase session + create profile
5. Return JWT + user data
"""

import hashlib
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/oauth", tags=["oauth"])

# ── OAuth Providers ─────────────────────────────────────────

OAUTH_PROVIDERS = {
    "github": {"name": "GitHub", "icon": "github", "enabled": True},
    "google": {"name": "Google", "icon": "google", "enabled": True},
    "discord": {"name": "Discord", "icon": "discord", "enabled": True},
    "twitter": {"name": "X (Twitter)", "icon": "twitter", "enabled": True},
    "gitlab": {"name": "GitLab", "icon": "gitlab", "enabled": True},
    "bitbucket": {"name": "Bitbucket", "icon": "bitbucket", "enabled": True},
    # Web3-focused providers (no Slack/LinkedIn - not web3 relevant)
}


# ── Models ──────────────────────────────────────────────────


class LinkAccountRequest(BaseModel):
    """Link additional auth provider to existing account."""

    provider: str
    access_token: str
    provider_user_id: str
    email: str | None = None


class UserProfile(BaseModel):
    """Unified user profile."""

    id: str
    email: str | None = None
    wallet_evm: str | None = None
    wallet_solana: str | None = None
    providers: list[str] = []
    created_at: str
    last_login: str
    metadata: dict = {}


# ── Supabase Client ─────────────────────────────────────────


def _get_supabase():
    """Get Supabase client."""
    try:
        import os

        from supabase import Client, create_client

        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")

        if not url or not key:
            return None

        return create_client(url, key)
    except ImportError:
        return None


async def _get_user_by_wallet(wallet: str, chain: str) -> dict | None:
    """Get user by wallet address."""
    supabase = _get_supabase()
    if not supabase:
        return None

    result = supabase.table("users").select("*").eq("wallet_address", wallet).eq("chain", chain).execute()
    return result.data[0] if result.data else None


async def _get_user_by_oauth(provider: str, provider_id: str) -> dict | None:
    """Get user by OAuth provider ID."""
    supabase = _get_supabase()
    if not supabase:
        return None

    result = (
        supabase.table("user_providers")
        .select("user_id")
        .eq("provider", provider)
        .eq("provider_user_id", provider_id)
        .execute()
    )
    if not result.data:
        return None

    user_id = result.data[0]["user_id"]
    user_result = supabase.table("users").select("*").eq("id", user_id).execute()
    return user_result.data[0] if user_result.data else None


async def _create_user(
    email: str | None = None,
    wallet: str | None = None,
    chain: str | None = None,
    provider: str | None = None,
    provider_id: str | None = None,
) -> dict:
    """Create new user with optional provider linkage."""
    supabase = _get_supabase()
    if not supabase:
        return {}

    user_id = hashlib.sha256(f"{email or wallet or provider_id}".encode()).hexdigest()[:32]
    now = datetime.now(UTC).isoformat()

    # Create user
    user_data = {
        "id": user_id,
        "email": email,
        "wallet_address": wallet,
        "chain": chain,
        "created_at": now,
        "last_login": now,
        "metadata": {},
    }

    result = supabase.table("users").insert(user_data).execute()
    user = result.data[0] if result.data else user_data

    # Link provider if provided
    if provider and provider_id:
        supabase.table("user_providers").insert(
            {
                "user_id": user_id,
                "provider": provider,
                "provider_user_id": provider_id,
                "linked_at": now,
            }
        ).execute()

    return user


async def _link_provider(user_id: str, provider: str, provider_id: str, email: str | None = None) -> bool:
    """Link additional provider to existing user."""
    supabase = _get_supabase()
    if not supabase:
        return False

    # Check if already linked
    existing = (
        supabase.table("user_providers")
        .select("*")
        .eq("provider", provider)
        .eq("provider_user_id", provider_id)
        .execute()
    )
    if existing.data:
        return True  # Already linked

    # Link
    result = (
        supabase.table("user_providers")
        .insert(
            {
                "user_id": user_id,
                "provider": provider,
                "provider_user_id": provider_id,
                "email": email,
                "linked_at": datetime.now(UTC).isoformat(),
            }
        )
        .execute()
    )

    # Update user email if provided
    if email:
        supabase.table("users").update({"email": email}).eq("id", user_id).execute()

    return bool(result.data)


async def _get_user_providers(user_id: str) -> list[str]:
    """Get list of linked providers for user."""
    supabase = _get_supabase()
    if not supabase:
        return []

    result = supabase.table("user_providers").select("provider").eq("user_id", user_id).execute()
    return [r["provider"] for r in result.data] if result.data else []


# ── Health & Info (must be BEFORE dynamic routes) ───────────


@router.get("/health")
async def oauth_health():
    """OAuth service health check."""
    supabase = _get_supabase()
    return {
        "status": "ok",
        "service": "supabase-oauth",
        "supabase_connected": supabase is not None,
        "providers_available": len([p for p in OAUTH_PROVIDERS.values() if p["enabled"]]),
        "supported_providers": list(OAUTH_PROVIDERS.keys()),
    }


@router.get("/")
async def oauth_root():
    """OAuth service info."""
    return {
        "service": "RMI OAuth",
        "version": "1.0",
        "providers": len(OAUTH_PROVIDERS),
        "wallet_auth": True,
        "account_linking": True,
        "docs": "/api/v1/oauth/providers",
    }


# ── Endpoints ────────────────────────────────────────────────


@router.get("/providers")
async def list_oauth_providers():
    """List available OAuth providers."""
    return {
        "providers": [
            {
                "id": pid,
                "name": info["name"],
                "icon": info["icon"],
                "enabled": info["enabled"],
            }
            for pid, info in OAUTH_PROVIDERS.items()
        ],
        "wallet_auth": {
            "evm": True,
            "solana": True,
        },
    }


@router.get("/{provider}")
async def oauth_start(provider: str, redirect_uri: str | None = None):
    """Get Supabase OAuth URL for provider.

    Frontend should redirect user to this URL.
    After auth, Supabase redirects to your callback URL.
    """
    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    if not OAUTH_PROVIDERS[provider]["enabled"]:
        raise HTTPException(status_code=400, detail=f"Provider {provider} is not enabled")

    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        # Get OAuth URL from Supabase
        import os

        base_url = os.getenv("SUPABASE_URL", "").replace(".supabase.co", "")
        project_ref = base_url.split("//")[1].split(".")[0] if base_url else ""

        oauth_url = f"https://{project_ref}.supabase.co/auth/v1/authorize?provider={provider}"
        if redirect_uri:
            oauth_url += f"&redirect_to={redirect_uri}"

        return {
            "provider": provider,
            "oauth_url": oauth_url,
            "instructions": "Redirect user to oauth_url. After auth, they'll be redirected to your callback.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/callback/{provider}")
async def oauth_callback(provider: str, request: Request):
    """Handle OAuth callback from Supabase.

    Supabase redirects here after user authenticates with provider.
    Exchange code for session, create/update user.
    """
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)

    code = body.get("code")
    error = body.get("error")

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code")

    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        # Exchange code for session
        session = supabase.auth.exchange_code_for_session(code)

        if not session or not session.user:
            raise HTTPException(status_code=400, detail="Invalid session")

        user = session.user
        provider_id = user.identities[0].identity_id if user.identities else user.id

        # Check if user exists
        existing = await _get_user_by_oauth(provider, provider_id)

        if existing:
            # Update last login
            supabase.table("users").update({"last_login": datetime.now(UTC).isoformat()}).eq(
                "id", existing["id"]
            ).execute()

            user_data = existing
        else:
            # Create new user
            user_data = await _create_user(
                email=user.email,
                provider=provider,
                provider_id=provider_id,
            )

        # Get linked providers
        providers = await _get_user_providers(user_data["id"])

        return {
            "status": "ok",
            "user": {
                "id": user_data["id"],
                "email": user_data.get("email"),
                "providers": providers,
                "created_at": user_data.get("created_at"),
                "last_login": user_data.get("last_login"),
            },
            "session": {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_in": session.expires_in,
            },
            "provider": provider,
        }
    except Exception as e:
        logger.error(f"OAuth callback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/link")
async def link_provider(req: LinkAccountRequest):
    """Link additional OAuth provider to existing account.

    Requires authenticated session (JWT from /auth/verify or OAuth).
    """
    # TODO: Add JWT verification middleware
    # For now, assume user is authenticated

    if req.provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    # Get user from session (TODO: implement JWT extraction)
    user_id = "temp-user-id"  # Extract from JWT header

    success = await _link_provider(user_id, req.provider, req.provider_user_id, req.email)

    if success:
        return {"status": "ok", "message": f"Linked {req.provider} to your account"}
    else:
        raise HTTPException(status_code=500, detail="Failed to link provider")


@router.get("/me")
async def get_current_user(access_token: str = Query(None, description="Supabase access token")):
    """Get current authenticated user profile."""
    if not access_token:
        raise HTTPException(status_code=401, detail="No access token provided")

    supabase = _get_supabase()
    if not supabase:
        raise HTTPException(status_code=503, detail="Supabase not configured")

    try:
        # Get user from token
        user_response = supabase.auth.get_user(access_token)
        user = user_response.user

        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Get our user record
        db_user = await _get_user_by_oauth("google", user.id)  # Try any provider

        if not db_user:
            # Create on the fly
            db_user = await _create_user(email=user.email)

        providers = await _get_user_providers(db_user["id"])

        return {
            "id": db_user["id"],
            "email": db_user.get("email"),
            "wallet_evm": db_user.get("wallet_address") if db_user.get("chain") == "evm" else None,
            "wallet_solana": db_user.get("wallet_address") if db_user.get("chain") == "solana" else None,
            "providers": providers,
            "created_at": db_user.get("created_at"),
            "last_login": db_user.get("last_login"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/health")
async def oauth_health():
    """OAuth service health check."""
    supabase = _get_supabase()
    return {
        "status": "ok",
        "service": "supabase-oauth",
        "supabase_connected": supabase is not None,
        "providers_available": len([p for p in OAUTH_PROVIDERS.values() if p["enabled"]]),
        "supported_providers": list(OAUTH_PROVIDERS.keys()),
    }


@router.get("/")
async def oauth_root():
    """OAuth service info."""
    return {
        "service": "RMI OAuth",
        "version": "1.0",
        "providers": len(OAUTH_PROVIDERS),
        "wallet_auth": True,
        "account_linking": True,
        "docs": "/api/v1/oauth/providers",
    }
