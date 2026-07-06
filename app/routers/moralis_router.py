"""
Moralis API Router - Wallet auth (SIWE/SIWS) + Data endpoints.
Key 1: Data API (wallets, tokens, NFTs, whale tracking, streams)
Key 2: Auth API (Sign-In With Ethereum/Solana - Phantom, MetaMask, etc.)
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/moralis", tags=["moralis"])


# ── Models ───────────────────────────────────────────────────


class AuthChallengeRequest(BaseModel):
    address: str
    chain: str = "eth"  # eth, polygon, bsc, avalanche, arbitrum, optimism, base
    domain: str = "rugmunch.io"


class SolanaChallengeRequest(BaseModel):
    address: str
    domain: str = "rugmunch.io"


class VerifySignatureRequest(BaseModel):
    message: str
    signature: str


class WalletQuery(BaseModel):
    address: str
    chain: str = "eth"
    limit: int = 20


class StreamCreateRequest(BaseModel):
    webhook_url: str
    description: str
    chains: list[str] = ["0x1"]
    address: str | None = None
    topic0: list[str] | None = None


# ── Auth Endpoints (Key 2 - Wallet Login) ────────────────────


@router.post("/auth/challenge/evm")
async def evm_challenge(req: AuthChallengeRequest):
    """Request EVM Sign-In challenge (MetaMask, etc)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        result = await mc.request_evm_challenge(
            address=req.address,
            chain=req.chain,
            domain=req.domain,
        )
        if result:
            return {"status": "ok", "challenge": result}
        raise HTTPException(status_code=502, detail="Moralis challenge failed")
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None


@router.post("/auth/challenge/solana")
async def solana_challenge(req: SolanaChallengeRequest):
    """Request Solana Sign-In challenge (Phantom, etc)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        result = await mc.request_solana_challenge(
            address=req.address,
            domain=req.domain,
        )
        if result:
            return {"status": "ok", "challenge": result}
        raise HTTPException(status_code=502, detail="Moralis challenge failed")
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None


@router.post("/auth/verify/evm")
async def verify_evm(req: VerifySignatureRequest):
    """Verify EVM wallet signature - returns JWT token for authenticated session."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        result = await mc.verify_evm_signature(
            message=req.message,
            signature=req.signature,
        )
        if result:
            return {"status": "ok", "auth": result}
        raise HTTPException(status_code=401, detail="Signature verification failed")
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None


@router.post("/auth/verify/solana")
async def verify_solana(req: VerifySignatureRequest):
    """Verify Solana wallet signature - returns JWT token for authenticated session."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        result = await mc.verify_solana_signature(
            message=req.message,
            signature=req.signature,
        )
        if result:
            return {"status": "ok", "auth": result}
        raise HTTPException(status_code=401, detail="Signature verification failed")
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None


# ── Data Endpoints (Key 1 - Wallet Intelligence) ─────────────


@router.post("/wallet/tokens")
async def wallet_tokens(req: WalletQuery):
    """Get ERC-20 tokens held by wallet."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        tokens = await mc.get_wallet_tokens(req.address, req.chain)
        return {"address": req.address, "chain": req.chain, "tokens": tokens[: req.limit]}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/wallet/nfts")
async def wallet_nfts(req: WalletQuery):
    """Get NFTs held by wallet."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        nfts = await mc.get_wallet_nfts(req.address, req.chain, req.limit)
        return {"address": req.address, "chain": req.chain, "nfts": nfts}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/wallet/balance")
async def wallet_balance(req: WalletQuery):
    """Get native balance (ETH/MATIC/BNB) for wallet."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        balance = await mc.get_wallet_native_balance(req.address, req.chain)
        return {"address": req.address, "chain": req.chain, "balance": balance}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/wallet/transfers")
async def wallet_transfers(req: WalletQuery):
    """Get token transfer history (whale tracking)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        transfers = await mc.get_wallet_token_transfers(req.address, req.chain, req.limit)
        return {"address": req.address, "chain": req.chain, "transfers": transfers}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/token/{token_address}/price")
async def token_price(token_address: str, chain: str = "eth"):
    """Get token price in USD."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        price = await mc.get_token_price(token_address, chain)
        return {"token": token_address, "chain": chain, "price": price}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/token/{token_address}/metadata")
async def token_metadata(token_address: str, chain: str = "eth"):
    """Get token metadata (name, symbol, decimals, logo)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        metadata = await mc.get_token_metadata(token_address, chain)
        return {"token": token_address, "chain": chain, "metadata": metadata}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/token/{token_address}/holders")
async def token_holders(token_address: str, chain: str = "eth", limit: int = 20):
    """Get top token holders (EVM chains)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        holders = await mc.get_token_holders(token_address, chain, limit)
        return {"token": token_address, "chain": chain, "holders": holders}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Streams Management ───────────────────────────────────────


@router.get("/streams")
async def list_streams():
    """List all Moralis streams (EVM webhooks)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        streams = await mc.list_streams()
        return {"streams": streams}
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.post("/streams/create")
async def create_stream(req: StreamCreateRequest):
    """Create a Moralis stream (webhook for EVM events)."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        stream = await mc.create_stream(
            webhook_url=req.webhook_url,
            description=req.description,
            chains=req.chains,
            address=req.address,
            topic0=req.topic0,
        )
        if stream:
            return {"status": "created", "stream": stream}
        raise HTTPException(status_code=502, detail="Stream creation failed")
    except ImportError:
        raise HTTPException(status_code=503, detail="Moralis connector not available") from None


# ── Health ────────────────────────────────────────────────────


@router.get("/health")
async def moralis_health():
    """Moralis connector status."""
    try:
        from app.moralis_connector import get_moralis_connector

        mc = get_moralis_connector()
        return {"status": "ok", "service": "moralis-connector", **mc.status()}
    except ImportError:
        return {
            "status": "ok",
            "service": "moralis-connector",
            "data_api": False,
            "auth_api": False,
        }
