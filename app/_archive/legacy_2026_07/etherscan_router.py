"""
Etherscan Family API Router - EVM Block Explorers.
Single API for: Ethereum, BSC, Polygon, Avalanche, Fantom, Arbitrum, Optimism, Base.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/etherscan", tags=["etherscan"])


# ── Models ───────────────────────────────────────────────────


class AccountQuery(BaseModel):
    address: str
    network: str = "eth"  # eth, bsc, polygon, avalanche, fantom, arbitrum, optimism, base
    page: int = 1
    offset: int = 100


class TokenBalanceQuery(BaseModel):
    contract: str
    address: str
    network: str = "eth"


class ContractQuery(BaseModel):
    contract: str
    network: str = "eth"


class LogsQuery(BaseModel):
    from_block: int
    to_block: int
    address: str | None = None
    topic0: str | None = None
    network: str = "eth"


# ── Account API ──────────────────────────────────────────────


@router.get("/balance")
async def get_balance(address: str, network: str = "eth"):
    """Get native token balance (ETH/BNB/MATIC/etc)."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        balance = await ec.get_balance(address, network)
        # Convert wei to native token
        try:
            native = int(balance) / 1e18 if balance else 0
        except Exception:
            native = 0
        return {
            "address": address,
            "network": network,
            "balance_wei": balance,
            "balance_native": native,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/transactions")
async def get_transactions(address: str, network: str = "eth", page: int = 1, offset: int = 100):
    """Get transaction list for address."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        txs = await ec.get_tx_list(address, network, page=page, offset=offset)
        return {
            "address": address,
            "network": network,
            "transactions": txs[:offset],
            "count": len(txs),
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/token-transfers")
async def get_token_transfers(
    address: str,
    network: str = "eth",
    contract: str | None = None,
    page: int = 1,
    offset: int = 100,
):
    """Get ERC-20 token transfer events."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        transfers = await ec.get_token_transfers(address, network, contract_address=contract, page=page, offset=offset)
        return {
            "address": address,
            "network": network,
            "transfers": transfers[:offset],
            "count": len(transfers),
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/token-balance")
async def get_token_balance(contract: str, address: str, network: str = "eth"):
    """Get ERC-20 token balance for address."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        balance = await ec.get_token_balance(contract, address, network)
        return {
            "contract": contract,
            "address": address,
            "network": network,
            "balance_wei": balance,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/nft-transfers")
async def get_nft_transfers(address: str, network: str = "eth", page: int = 1, offset: int = 100):
    """Get ERC-721 NFT transfer events."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        transfers = await ec.get_erc721_transfers(address, network, page=page, offset=offset)
        return {
            "address": address,
            "network": network,
            "transfers": transfers[:offset],
            "count": len(transfers),
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Contract API ─────────────────────────────────────────────


@router.get("/contract/abi")
async def get_contract_abi(contract: str, network: str = "eth"):
    """Get contract ABI."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        abi = await ec.get_contract_abi(contract, network)
        return {"contract": contract, "network": network, "abi": abi}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/contract/source")
async def get_contract_source(contract: str, network: str = "eth"):
    """Get contract source code."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        source = await ec.get_contract_source(contract, network)
        return {"contract": contract, "network": network, "source": source}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


@router.get("/contract/verify")
async def verify_contract(contract: str, network: str = "eth"):
    """Check if contract is verified."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        result = await ec.verify_contract(contract, network)
        return {"contract": contract, "network": network, **result}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Transaction API ──────────────────────────────────────────


@router.get("/tx/{tx_hash}/status")
async def get_tx_status(tx_hash: str, network: str = "eth"):
    """Get transaction status (success/fail)."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        status = await ec.get_transaction_status(tx_hash, network)
        return {"tx_hash": tx_hash, "network": network, "status": status}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Block API ────────────────────────────────────────────────


@router.get("/block/latest")
async def get_latest_block(network: str = "eth"):
    """Get latest block number."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        block = await ec.get_block_number(network)
        return {"network": network, "block_number": block}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Logs API ─────────────────────────────────────────────────


@router.post("/logs")
async def get_logs(req: LogsQuery):
    """Get event logs (filtered by address/topic)."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        logs = await ec.get_logs(req.from_block, req.to_block, req.address, req.topic0, req.network)
        return {"network": req.network, "logs": logs, "count": len(logs)}
    except ImportError:
        raise HTTPException(status_code=503, detail="Etherscan connector not available") from None
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200]) from e


# ── Health ────────────────────────────────────────────────────


@router.get("/health")
async def etherscan_health():
    """Etherscan connector status."""
    try:
        from app.etherscan_connector import get_etherscan_connector

        ec = get_etherscan_connector()
        return {"status": "ok", "service": "etherscan-connector", **ec.status()}
    except ImportError:
        return {"status": "ok", "service": "etherscan-connector", "networks_active": 0}
