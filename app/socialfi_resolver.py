"""
SocialFi Resolver - Farcaster, ENS, Lens profile resolution.
All free/public APIs, no API keys required.
Uses Warpcast v2 API (free, no auth needed for public data).
"""

from __future__ import annotations
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Warpcast public API v2 (free, no key)
WARPCAST_API = "https://api.warpcast.com/v2"

# ENS public resolver
ENS_RPC_URLS = [
    "https://api.etherscan.io/api",  # via Etherscan proxy
]

# Etherscan key from env
import os as _os  # noqa: E402

_ETHERSCAN_KEY = _os.getenv("ETHERSCAN_API_KEY", "")

async def resolve_ens_name(address: str) -> str | None:
    """Resolve an Ethereum address to an ENS name via Etherscan API."""
    import httpx

    if not _ETHERSCAN_KEY:
        logger.warning("No ETHERSCAN_API_KEY set, ENS resolution disabled")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Etherscan V2 API for eth_call
            data = "0x691f3431" + address[2:].lower().zfill(64)
            resp = await client.get(
                "https://api.etherscan.io/v2/api",
                params={
                    "chainid": "1",
                    "module": "proxy",
                    "action": "eth_call",
                    "to": "0x3671aE578E63FdF66ad4F3E12CC0c0d71Ac7510C",
                    "data": data,
                    "apikey": _ETHERSCAN_KEY,
                },
            )
            if resp.status_code == 200:
                result = resp.json().get("result", "")
                if isinstance(result, str) and result != "0x" and len(result) > 138:
                    name_bytes = bytes.fromhex(result[138:])
                    name = name_bytes.decode("utf-8").rstrip("\x00")
                    if "." in name:
                        return name
    except Exception as e:
        logger.debug(f"ENS resolution failed: {e}")

    return None

async def fetch_farcaster_profile(fid: int) -> dict[str, Any] | None:
    """Fetch Farcaster profile by FID using Warpcast v2 API (free, no auth)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{WARPCAST_API}/user", params={"fid": fid})
            if resp.status_code != 200:
                logger.warning(f"Warpcast API returned {resp.status_code}")
                return None

            data = resp.json()
            user_data = data.get("result", {}).get("user", {})
            if not user_data:
                return None

            profile_obj = user_data.get("profile", {})
            bio_obj = profile_obj.get("bio", {})

            return {
                "fid": fid,
                "username": user_data.get("username"),
                "display_name": user_data.get("displayName"),
                "bio": bio_obj.get("text") if isinstance(bio_obj, dict) else None,
                "avatar_url": user_data.get("pfp", {}).get("url"),
                "banner_url": profile_obj.get("bannerImageUrl"),
                "website": profile_obj.get("url"),
                "followers": user_data.get("followerCount", 0),
                "following": user_data.get("followingCount", 0),
                "verified": user_data.get("pfp", {}).get("verified", False),
                "farcaster_url": f"https://warpcast.com/{user_data.get('username')}"
                if user_data.get("username")
                else None,
                "connected_accounts": user_data.get("connectedAccounts", []),
                "eth_wallets": user_data.get("extras", {}).get("ethWallets", []),
                "solana_wallets": user_data.get("extras", {}).get("solanaWallets", []),
            }

    except Exception as e:
        logger.warning(f"Farcaster fetch failed for FID {fid}: {e}")
        return None

async def resolve_farcaster_handle(handle: str) -> int | None:
    """Resolve a Farcaster username to FID using Warpcast v2 API."""
    import httpx

    handle = handle.lstrip("@")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{WARPCAST_API}/user-by-username", params={"username": handle})
            if resp.status_code == 200:
                data = resp.json()
                fid = data.get("result", {}).get("user", {}).get("fid")
                if fid:
                    return int(fid)
    except Exception as e:
        logger.warning(f"Farcaster handle resolve failed for {handle}: {e}")

    return None
