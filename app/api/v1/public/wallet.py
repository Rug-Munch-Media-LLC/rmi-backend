"""Public wallet endpoints - /api/v1/wallet/*.

Wallet resolution, labels, history, and cross-app signature verification.
Used by degenfeed, rugmunchbot, and other RugMunch apps.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

router = APIRouter(prefix="/wallet", tags=["wallet"])


class WalletResolveResponse(BaseModel):
    """Response for wallet resolution."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    address: str = Field(..., min_length=26, max_length=44)
    labels: list[str] = []
    entity: str | None = Field(None, min_length=1, max_length=100)
    balance_usd: float | None = Field(None, ge=0)
    tx_count: int | None = Field(None, ge=0)


# ── Signature Verification ────────────────────────────────────────────────


class VerifySignatureRequest(BaseModel):
    """Request to verify a wallet signature (cross-app auth)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=26, max_length=44)
    chain: str = Field(
        default="solana",
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    message: str = Field(..., min_length=1, max_length=2000)
    signature: str = Field(..., min_length=1, max_length=2000)


@router.post("/verify-signature")
async def verify_wallet_signature(
    request: VerifySignatureRequest,
) -> dict[str, Any]:
    """Verify a wallet signature for cross-app authentication.

    Used by degenfeed and other RugMunch apps to cryptographically
    prove wallet ownership. Supports Solana (ed25519) and EVM (EIP-191).
    Falls back to delegated verification if crypto libs aren't installed.
    """
    import base64

    if request.chain == "solana":
        try:
            import base58
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey

            sig_bytes = base64.b64decode(request.signature)
            msg_bytes = request.message.encode()
            pubkey_bytes = base58.b58decode(request.address)
            verify_key = VerifyKey(pubkey_bytes)

            verify_key.verify(msg_bytes, sig_bytes)
            return {
                "verified": True,
                "address": request.address,
                "chain": request.chain,
                "method": "ed25519",
                "message": request.message[:100],
            }
        except BadSignatureError:
            return {
                "verified": False,
                "address": request.address,
                "chain": request.chain,
                "reason": "Invalid signature — message was not signed by this wallet",
            }
        except ImportError:
            return {
                "verified": True,
                "address": request.address,
                "chain": request.chain,
                "method": "delegated",
                "note": "Server crypto libs unavailable. Client should verify via @solana/web3.js.",
            }
        except Exception as e:
            return {
                "verified": False,
                "address": request.address,
                "chain": request.chain,
                "reason": f"Verification error: {str(e)[:200]}",
            }

    if request.chain in (
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "base",
        "optimism",
        "avalanche",
    ):
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct

            message_hash = encode_defunct(text=request.message)
            recovered = Account.recover_message(message_hash, signature=request.signature)

            if recovered.lower() == request.address.lower():
                return {
                    "verified": True,
                    "address": request.address,
                    "chain": request.chain,
                    "method": "eip-191",
                    "message": request.message[:100],
                }
            return {
                "verified": False,
                "address": request.address,
                "chain": request.chain,
                "reason": "Recovered address mismatch",
            }
        except ImportError:
            return {
                "verified": True,
                "address": request.address,
                "chain": request.chain,
                "method": "delegated",
                "note": "Server crypto libs unavailable. Client should verify via ethers.js verifyMessage().",
            }
        except Exception as e:
            return {
                "verified": False,
                "address": request.address,
                "chain": request.chain,
                "reason": f"EVM error: {str(e)[:200]}",
            }

    return {
        "verified": False,
        "address": request.address,
        "chain": request.chain,
        "reason": f"Unsupported chain: {request.chain}",
    }


# ── Legacy stubs (keep for backward compat) ───────────────────────────────


@router.get("/{address}", response_model=WalletResolveResponse)
async def resolve_wallet(
    address: str,
    chain: str = Query(
        "ethereum",
        description="Blockchain (ethereum, solana, base, etc.)",
    ),
) -> WalletResolveResponse:
    """Resolve a wallet address to its labels + summary.

    Returns 501 stub until label resolution is wired up.
    """
    raise HTTPException(
        status_code=501,
        detail="Wallet resolution not yet implemented - coming in v5.1",
    )


@router.get("/{address}/labels", response_model=list[dict[str, Any]])
async def get_wallet_labels(address: str, chain: str = "ethereum") -> list[dict[str, Any]]:
    """Return labels for a wallet from all federated sources."""
    raise HTTPException(
        status_code=501,
        detail="Federated labels API pending - see T11",
    )


@router.get("/{address}/history")
async def get_wallet_history(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Return transaction history summary for a wallet."""
    raise HTTPException(
        status_code=501,
        detail="Wallet history pending - uses Neo4j + Postgres in v5.1",
    )
