"""Pydantic request models for x402 DataBus tools."""
from pydantic import BaseModel


class AddressRequest(BaseModel):
    address: str
    chain: str = "solana"


class MintRequest(BaseModel):
    mint: str
    chain: str = "solana"


class QueryRequest(BaseModel):
    query: str = ""
    chain: str = "solana"
    category: str = ""


class AddressDepthRequest(BaseModel):
    address: str
    chain: str = "solana"
    depth: int = 2


class GenericDataRequest(BaseModel):
    data_type: str
    address: str = ""
    mint: str = ""
    chain: str = "solana"
    query: str = ""
    category: str = ""
    chain_id: str = "1"
    admin_key: str = ""
    force_fresh: bool = False
