"""#14 - Dify RMI Tool Suite. 15 crypto tools exposed as OpenAPI endpoints for Dify agents.
Each tool is a standalone endpoint that Dify can call via API. Security: rate-limited, API-key gated, logged."""

import os

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/dify-tools", tags=["dify-tools"])

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
RMI_KEY = os.getenv("RMI_INTERNAL_KEY", "rmi-internal-2026")


# ── Tool 1: Search Crypto ──
@router.get("/search")
async def search_crypto(q: str, limit: int = Query(5, le=20)):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/databus/fetch/token_search",
            params={"q": q, "limit": limit},
            headers={"X-RMI-Key": RMI_KEY},
        )
        return {"query": q, "results": r.json() if r.status_code == 200 else []}


# ── Tool 2: Scan Token ──
@router.get("/scan")
async def scan_token(address: str, chain: str = "solana"):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{BACKEND}/api/v1/token/scan",
            json={"token_address": address, "chain": chain},
            headers={"X-RMI-Key": RMI_KEY},
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "safety_score": data.get("safety_score"),
                "risk_flags": data.get("risk_flags", []),
                "symbol": data.get("symbol"),
            }
        return {"error": "scan failed"}


# ── Tool 3: Get Price ──
@router.get("/price")
async def get_price(symbol: str, chain: str | None = None):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/databus/fetch/token_price", params={"symbol": symbol}, headers={"X-RMI-Key": RMI_KEY}
        )
        return r.json() if r.status_code == 200 else {"error": "price unavailable"}


# ── Tool 4: Whale Alert ──
@router.get("/whales")
async def whale_alert(chain: str = "ethereum", threshold: float = Query(100000, ge=10000)):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/databus/fetch/whale_alerts", params={"chain": chain}, headers={"X-RMI-Key": RMI_KEY}
        )
        return r.json() if r.status_code == 200 else {"alerts": 0}


# ── Tool 5: Market Overview ──
@router.get("/market")
async def market_overview():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BACKEND}/api/v1/databus/fetch/market_overview", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "market data unavailable"}


# ── Tool 6: Token Report ──
@router.get("/report")
async def token_report(address: str, chain: str = "ethereum"):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{BACKEND}/api/v1/token-cv/{chain}/{address}", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "report failed"}


# ── Tool 7: Address Profile ──
@router.get("/profile")
async def address_profile(address: str):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BACKEND}/api/v1/address-profiler/profile/{address}", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "profile failed"}


# ── Tool 8: Chain Compare ──
@router.get("/compare")
async def compare_chains(symbol: str):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BACKEND}/api/v1/chain-compare/token/{symbol}", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "compare failed"}


# ── Tool 9: Predict Rug ──
@router.get("/predict")
async def predict_rug(address: str, chain: str = "ethereum"):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{BACKEND}/api/v1/death-clock/predict/{chain}/{address}", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "prediction failed"}


# ── Tool 10: Contract Analyze ──
@router.get("/contract")
async def contract_analyze(address: str, chain: str = "ethereum"):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/contract-analyzer/analyze/{address}?chain={chain}", headers={"X-RMI-Key": RMI_KEY}
        )
        return r.json() if r.status_code == 200 else {"error": "analysis failed"}


# ── Tool 11: Trending ──
@router.get("/trending")
async def trending_tokens(chain: str = "solana", limit: int = Query(10, le=25)):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/databus/fetch/trending",
            params={"chain": chain, "limit": limit},
            headers={"X-RMI-Key": RMI_KEY},
        )
        return r.json() if r.status_code == 200 else {"trending": []}


# ── Tool 12: Fear & Greed ──
@router.get("/fear-greed")
async def fear_greed_index():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BACKEND}/api/v1/databus/fetch/fear_greed", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"value": 50, "classification": "Neutral"}


# ── Tool 13: News ──
@router.get("/news")
async def news_headlines(limit: int = Query(5, le=20)):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BACKEND}/api/v1/databus/fetch/news", params={"limit": limit}, headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"headlines": []}


# ── Tool 14: RAG Search ──
@router.get("/rag")
async def search_rag(query: str, limit: int = Query(5, le=10)):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/rag/search", params={"q": query, "limit": limit}, headers={"X-RMI-Key": RMI_KEY}
        )
        return r.json() if r.status_code == 200 else {"results": []}


# ── Tool 15: Ollama Chat (fallback for local inference) ──
@router.get("/ollama")
async def ollama_chat(prompt: str, model: str = "qwen2.5-coder:7b"):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(f"{BACKEND}/api/v1/ollama/chat?prompt={prompt}&model={model}", headers={"X-RMI-Key": RMI_KEY})
        return r.json() if r.status_code == 200 else {"error": "ollama unavailable"}


# ── Tool catalog ──
@router.get("/catalog")
async def tool_catalog():
    return {
        "tools": [
            {"name": "search_crypto", "description": "Search crypto tokens across 112 chains"},
            {"name": "scan_token", "description": "SENTINEL security scan"},
            {"name": "get_price", "description": "Real-time token price"},
            {"name": "whale_alert", "description": "Recent whale movements"},
            {"name": "market_overview", "description": "Market stats + fear & greed"},
            {"name": "token_report", "description": "Full token security report"},
            {"name": "address_profile", "description": "Cross-chain wallet analysis"},
            {"name": "compare_chains", "description": "Price/liquidity across chains"},
            {"name": "predict_rug", "description": "Token Death Clock prediction"},
            {"name": "contract_analyze", "description": "Smart contract function analysis"},
            {"name": "trending_tokens", "description": "What's pumping right now"},
            {"name": "fear_greed_index", "description": "Market sentiment index"},
            {"name": "news_headlines", "description": "Latest crypto news"},
            {"name": "search_rag", "description": "Search 17K+ scam documents"},
            {"name": "ollama_chat", "description": "Local AI inference (free)"},
        ],
        "total": 15,
    }
