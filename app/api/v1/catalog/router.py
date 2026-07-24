"""T27B HTTP routes - CatalogService endpoints.

Per v4.0 §T27. The thin HTTP layer over app.catalog.service.CatalogService.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.catalog.models import Chain
from app.catalog.service import get_catalog

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

# ── Request models ───────────────────────────────────────────────
class RagIngestRequest(BaseModel):
    content: str = Field(..., min_length=1)
    collection: str = "scam_intel"
    doc_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    collection: str = "scam_intel"
    top_k: int = Field(default=5, ge=1, le=50)

class ResolveEntityRequest(BaseModel):
    wallet_id: str
    max_chains: int = Field(default=5, ge=1, le=20)

class FindRiskyTokensRequest(BaseModel):
    min_rug_count: int = Field(default=1, ge=1)
    chain: str | None = None
    limit: int = Field(default=50, ge=1, le=200)

class AttachRagRequest(BaseModel):
    chain: str
    address: str
    qdrant_point_id: str

# ── Health / introspection ───────────────────────────────────────
@router.get("/stats")
async def stats() -> dict:
    """Catalog stats: which stores are reachable + entity counts."""
    return await get_catalog().stats()

@router.get("/probe")
async def probe() -> dict:
    """Probe which stores are reachable from this container."""
    return await get_catalog().probe_stores()

# ── Token endpoints ─────────────────────────────────────────────
@router.get("/tokens/{chain}/{address}")
async def get_token(chain: str, address: str) -> dict:
    """Get a token by chain+address. Returns full Token model + provenance."""
    try:
        c = Chain(chain)
    except ValueError:
        raise HTTPException(400, f"unknown chain: {chain}") from None
    tok = await get_catalog().get_token(c, address)
    if not tok:
        raise HTTPException(404, "token not found")
    return tok.model_dump(mode="json")

@router.get("/tokens/{chain}/{address}/risk")
async def get_token_risk(chain: str, address: str) -> dict:
    """Recipe 3 - Real-time risk score. Composes Redis + Postgres + Neo4j."""
    try:
        c = Chain(chain)
    except ValueError:
        raise HTTPException(400, f"unknown chain: {chain}") from None
    return await get_catalog().get_token_risk(c, address)

@router.post("/tokens/risky-by-deployer")
async def risky_tokens(req: FindRiskyTokensRequest) -> dict:
    """Recipe 1 - Find tokens deployed by wallets with rug history."""
    chain_enum = None
    if req.chain:
        try:
            chain_enum = Chain(req.chain)
        except ValueError:
            raise HTTPException(400, f"unknown chain: {req.chain}") from None
    tokens = await get_catalog().find_tokens_by_deployer_history(
        min_rug_count=req.min_rug_count, chain=chain_enum, limit=req.limit
    )
    return {
        "count": len(tokens),
        "tokens": [t.model_dump(mode="json") for t in tokens],
    }

# ── Wallet endpoints ─────────────────────────────────────────────
@router.get("/wallets/{chain}/{address}", summary="Get Wallet", description="Look up a wallet by chain and address. Returns full wallet model with provenance, entity labels, and transaction history from the catalog store. Supports all 13+ tracked chains.")
async def get_wallet(chain: str, address: str) -> dict:
    try:
        c = Chain(chain)
    except ValueError:
        raise HTTPException(400, f"unknown chain: {chain}") from None
    w = await get_catalog().get_wallet(c, address)
    if not w:
        raise HTTPException(404, "wallet not found")
    return w.model_dump(mode="json")

# ── Entity resolution (Recipe 5) ────────────────────────────────
@router.post("/entities/resolve")
async def resolve_entity(req: ResolveEntityRequest) -> dict:
    """Cross-chain entity resolution via Neo4j Cypher."""
    return await get_catalog().resolve_entity(req.wallet_id, req.max_chains)

# ── RAG bridge endpoints ────────────────────────────────────────
@router.post("/rag/search")
async def rag_search(req: RagSearchRequest) -> dict:
    """Search the RAG system. Returns ranked hits with RRF scores."""
    hits = await get_catalog().rag_search(
        query=req.query, collection=req.collection, top_k=req.top_k
    )
    return {"count": len(hits), "hits": hits}

@router.post("/rag/ingest")
async def rag_ingest(req: RagIngestRequest) -> dict:
    """Ingest content into RAG. Returns qdrant_point_id for cross-store linking."""
    return await get_catalog().rag_ingest(
        content=req.content,
        collection=req.collection,
        doc_id=req.doc_id,
        metadata=req.metadata,
    )

@router.post("/tokens/{chain}/{address}/attach-rag")
async def attach_rag(chain: str, address: str, req: AttachRagRequest) -> dict:
    """Link an existing RAG embedding (Qdrant point) to a Token row."""
    try:
        c = Chain(chain)
    except ValueError:
        raise HTTPException(400, f"unknown chain: {chain}") from None
    ok = await get_catalog().attach_rag_to_token(c, address, req.qdrant_point_id)
    if not ok:
        raise HTTPException(404, "token not found or update failed")
    return {"ok": True, "chain": chain, "address": address, "rag_embedding_id": req.qdrant_point_id}
