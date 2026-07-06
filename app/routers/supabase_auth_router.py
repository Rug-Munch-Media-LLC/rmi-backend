"""
Supabase Auth Integration - Web3 wallet authentication.
Uses Moralis for SIWE/SIWS challenges, Supabase for user storage.
Free alternative to paid Moralis auth for acquired users.

Flow:
1. Client requests challenge from /auth/challenge/{evm|solana}
2. User signs with wallet (MetaMask/Phantom)
3. Client submits signature to /auth/verify
4. We verify with Moralis, then create/update Supabase user
5. Return Supabase JWT for authenticated session
"""

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Models ───────────────────────────────────────────────────


class VerifyRequest(BaseModel):
    chain: str  # evm or solana
    message: str
    signature: str
    wallet_address: str


# ── Supabase Integration ─────────────────────────────────────


async def _create_or_update_user(wallet: str, chain: str, profile: dict) -> dict:
    """Create or update user in Supabase."""
    try:
        import os

        from supabase import Client, create_client

        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = (
            os.environ.get("SUPABASE_KEY", "")
            or os.environ.get("SUPABASE_SERVICE_KEY", "")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        )

        if not supabase_url or not supabase_key:
            logger.warning("Supabase credentials not configured in environment")
            return {}

        supabase: Client = create_client(supabase_url, supabase_key)

        # Generate user ID from wallet
        user_id = hashlib.sha256(f"{chain}:{wallet}".encode()).hexdigest()[:32]

        # Check if user exists
        existing = supabase.table("users").select("*").eq("wallet_address", wallet).eq("chain", chain).execute()

        if existing.data and len(existing.data) > 0:
            # Update
            result = (
                supabase.table("users")
                .update(
                    {
                        "last_login": datetime.now(UTC).isoformat(),
                        "profile": profile,
                    }
                )
                .eq("wallet_address", wallet)
                .eq("chain", chain)
                .execute()
            )
            return result.data[0] if result.data else {}
        else:
            # Create
            result = (
                supabase.table("users")
                .insert(
                    {
                        "id": user_id,
                        "wallet_address": wallet,
                        "chain": chain,
                        "created_at": datetime.now(UTC).isoformat(),
                        "last_login": datetime.now(UTC).isoformat(),
                        "profile": profile,
                    }
                )
                .execute()
            )
            return result.data[0] if result.data else {}
    except ImportError:
        logger.debug("Supabase not installed")
        return {}
    except Exception as e:
        logger.debug(f"Supabase user creation failed: {e}")
        return {}


async def _generate_supabase_jwt(user_id: str, wallet: str) -> str:
    """Generate JWT token for Supabase auth."""
    # This is a simplified version - use supabase.auth for production
    import os

    import jwt

    jwt_secret = os.getenv("SUPABASE_JWT_SECRET", "fallback-secret-change-me")

    payload = {
        "sub": user_id,
        "wallet": wallet,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(days=7),
    }

    return jwt.encode(payload, jwt_secret, algorithm="HS256")


# ── Endpoints ────────────────────────────────────────────────


@router.post("/verify/evm")
async def verify_evm_and_create_user(req: VerifyRequest):
    """Verify EVM signature and create Supabase user."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()

        # Verify with Moralis
        result = await mc.verify_evm_signature(req.message, req.signature)
        if not result:
            raise HTTPException(status_code=401, detail="Signature verification failed")

        # Extract profile data
        profile_id = result.get("profileId", "")
        profile = {
            "profile_id": profile_id,
            "verified_at": datetime.now(UTC).isoformat(),
        }

        # Create/update Supabase user
        user = await _create_or_update_user(req.wallet_address, "evm", profile)

        # Generate JWT
        user_id = user.get("id", hashlib.sha256(f"evm:{req.wallet_address}".encode()).hexdigest()[:32])
        jwt_token = await _generate_supabase_jwt(user_id, req.wallet_address)

        return {
            "status": "ok",
            "user": user,
            "jwt": jwt_token,
            "wallet": req.wallet_address,
            "chain": "evm",
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/verify/solana")
async def verify_solana_and_create_user(req: VerifyRequest):
    """Verify Solana signature and create Supabase user."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()

        # Verify with Moralis
        result = await mc.verify_solana_signature(req.message, req.signature)
        if not result:
            raise HTTPException(status_code=401, detail="Signature verification failed")

        # Extract profile data
        profile_id = result.get("profileId", "")
        profile = {
            "profile_id": profile_id,
            "verified_at": datetime.now(UTC).isoformat(),
        }

        # Create/update Supabase user
        user = await _create_or_update_user(req.wallet_address, "solana", profile)

        # Generate JWT
        user_id = user.get("id", hashlib.sha256(f"solana:{req.wallet_address}".encode()).hexdigest()[:32])
        jwt_token = await _generate_supabase_jwt(user_id, req.wallet_address)

        return {
            "status": "ok",
            "user": user,
            "jwt": jwt_token,
            "wallet": req.wallet_address,
            "chain": "solana",
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/health")
async def auth_health():
    """Auth service health check."""
    return {
        "status": "ok",
        "service": "supabase-auth-integration",
        "providers": ["moralis", "supabase"],
    }
