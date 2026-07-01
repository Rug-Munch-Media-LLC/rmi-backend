"""
Alchemy API Router — NFT API, Enhanced API, Transaction API.
Endpoints for NFT discovery, whale tracking, token metadata, and contract analysis.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/alchemy", tags=["alchemy"])


# ── Models ───────────────────────────────────────────────────


class NftQuery(BaseModel):
    owner: str
    network: str = "eth"
    page_size: int = 50


class NftMetadataQuery(BaseModel):
    contract: str
    token_id: str
    network: str = "eth"


class CollectionOwnersQuery(BaseModel):
    contract: str
    network: str = "eth"
    page_size: int = 50


class TokenBalanceQuery(BaseModel):
    address: str
    network: str = "eth"


class AssetTransferQuery(BaseModel):
    from_address: str | None = None
    to_address: str | None = None
    network: str = "eth"
    category: list[str] = ["external", "internal", "erc20", "erc721"]
    max_count: int = 100


class ContractCallQuery(BaseModel):
    contract: str
    data: str
    network: str = "eth"
    from_address: str | None = None


# ── NFT API Endpoints ────────────────────────────────────────


@router.post("/nfts")
async def get_nfts(req: NftQuery):
    """Get all NFTs owned by an address."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_nfts(req.owner, req.network, req.page_size)
        return {"owner": req.owner, "network": req.network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/nft/metadata")
async def get_nft_metadata(contract: str, token_id: str, network: str = "eth"):
    """Get metadata for a specific NFT."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_nft_metadata(contract, token_id, network)
        return {"contract": contract, "token_id": token_id, "metadata": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/collection/owners")
async def get_collection_owners(contract: str, network: str = "eth", page_size: int = 50):
    """Get all owners of an NFT collection."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_owners_for_collection(contract, network, page_size)
        return {"contract": contract, "network": network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/contract/metadata")
async def get_contract_metadata(contract: str, network: str = "eth"):
    """Get NFT contract metadata (name, symbol, totalSupply)."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_contract_metadata(contract, network)
        # Unwrap contractMetadata if present
        if isinstance(result, dict) and "contractMetadata" in result:
            result = result["contractMetadata"]
        return {"contract": contract, "network": network, "metadata": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/nft-sales")
async def get_nft_sales(contract: str | None = None, network: str = "eth", limit: int = 50):
    """Get recent NFT sales."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_nft_sales(contract, network, limit)
        return {"contract": contract, "network": network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ── Enhanced API Endpoints ───────────────────────────────────


@router.post("/token/balances")
async def get_token_balances(req: TokenBalanceQuery):
    """Get all ERC-20 token balances for an address."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_token_balances(req.address, req.network)
        return {"address": req.address, "network": req.network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/token/metadata")
async def get_token_metadata(contract: str, network: str = "eth"):
    """Get ERC-20 token metadata."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_token_metadata(contract, network)
        return {"contract": contract, "network": network, "metadata": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/transfers")
async def get_asset_transfers(req: AssetTransferQuery):
    """Get asset transfers (tokens, NFTs, internal)."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_asset_transfers(
            from_address=req.from_address,
            to_address=req.to_address,
            network=req.network,
            category=req.category,
            max_count=req.max_count,
        )
        return {"network": req.network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ── Transaction API Endpoints ────────────────────────────────


@router.get("/tx/{tx_hash}/receipt")
async def get_transaction_receipt(tx_hash: str, network: str = "eth"):
    """Get transaction receipt with enhanced data."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_transaction_receipt(tx_hash, network)
        return {"tx_hash": tx_hash, "network": network, "receipt": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/block/{block_number}")
async def get_block(block_number: int, network: str = "eth", include_txs: bool = False):
    """Get block data."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_block_by_number(block_number, network, include_txs)
        return {"block_number": block_number, "network": network, "block": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/balance/{address}")
async def get_balance(address: str, network: str = "eth", block: str = "latest"):
    """Get native token balance."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.get_balance(address, network, block)
        # Convert hex wei to ETH
        try:
            eth = int(result, 16) / 1e18 if result.startswith("0x") else float(result)
        except Exception:
            eth = 0
        return {"address": address, "network": network, "balance_wei": result, "balance_eth": eth}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/contract/call")
async def contract_call(req: ContractCallQuery):
    """Call a contract read function."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        result = await ac.call_contract(req.contract, req.data, req.network, req.from_address)
        return {"contract": req.contract, "network": req.network, "result": result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Alchemy connector not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ── Health ────────────────────────────────────────────────────


@router.get("/health")
async def alchemy_health():
    """Alchemy connector status."""
    try:
        from app.alchemy_connector import get_alchemy_connector

        ac = get_alchemy_connector()
        return {"status": "ok", "service": "alchemy-connector", **ac.status()}
    except ImportError:
        return {"status": "ok", "service": "alchemy-connector", "api_key_set": False}
