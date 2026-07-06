"""
All Connectors Router - Wires all 20+ unwired modules into API routes.
One file to rule them all.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["connectors"])


# ═══════════════════════════════════════════════════════════
# BIRDEYE - Token data, trending, OHLCV
# ═══════════════════════════════════════════════════════════
@router.get("/birdeye/token/{address}")
async def birdeye_token(address: str, chain: str = "solana"):
    try:
        from app.birdeye_client import BirdeyeClient

        client = BirdeyeClient()
        data = client.get_token_info(address)
        return {"address": address, "chain": chain, "data": data, "source": "birdeye"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/birdeye/trending")
async def birdeye_trending(chain: str = "solana", limit: int = 20):
    try:
        from app.birdeye_client import BirdeyeClient

        client = BirdeyeClient()
        tokens = client.get_trending(limit=limit)
        return {"tokens": tokens, "chain": chain, "source": "birdeye"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# ARKHAM INTELLIGENCE - Entity labeling, wallet attribution,
# institutional tracking, sanctions screening
# ═══════════════════════════════════════════════════════════
@router.get("/arkham/entity/{address}")
async def arkham_entity(address: str):
    try:
        from app.arkham_connector import ArkhamClient

        client = ArkhamClient()
        try:
            data = await client.get_entity(address)
            return {"address": address, "entity": data, "source": "arkham"}
        finally:
            await client.close()
    except Exception as e:
        return {"error": str(e)}


@router.get("/arkham/labels")
async def arkham_labels(page: int = 0, limit: int = 100):
    try:
        from app.arkham_connector import ArkhamClient

        client = ArkhamClient()
        try:
            data = await client.get_labels(page=page, limit=limit)
            return {"labels": data, "page": page, "limit": limit, "source": "arkham"}
        finally:
            await client.close()
    except Exception as e:
        return {"error": str(e)}


@router.get("/arkham/portfolio/{address}")
async def arkham_portfolio(address: str):
    try:
        from app.arkham_connector import ArkhamClient

        client = ArkhamClient()
        try:
            data = await client.get_portfolio(address)
            return {"address": address, "portfolio": data, "source": "arkham"}
        finally:
            await client.close()
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# BLOCKCHAIR - Multi-chain block explorer
# ═══════════════════════════════════════════════════════════
@router.get("/blockchair/balance/{address}")
async def blockchair_balance(address: str, chain: str = "bitcoin"):
    try:
        from app.blockchair_connector import get_address_balance

        result = get_address_balance(address, chain)
        return {"address": address, "chain": chain, "data": result, "source": "blockchair"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/blockchair/search")
async def blockchair_search(q: str):
    try:
        from app.blockchair_connector import search_blockchain

        result = search_blockchain(q)
        return {"query": q, "results": result, "source": "blockchair"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# DEFILLAMA - DeFi analytics
# ═══════════════════════════════════════════════════════════
@router.get("/defillama/tvl")
async def defillama_tvl():
    try:
        from app.defillama_connector import get_defi_tvl

        result = get_defi_tvl()
        return {"tvl": result, "source": "defillama"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/defillama/protocols")
async def defillama_protocols():
    try:
        from app.defillama_connector import get_defi_protocols

        result = get_defi_protocols()
        return {"protocols": result, "source": "defillama"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/defillama/chains")
async def defillama_chains():
    try:
        from app.defillama_connector import get_chain_tvls

        result = get_chain_tvls()
        return {"chains": result, "source": "defillama"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# ENTITY CLUSTERING - Wallet cluster analysis
# ═══════════════════════════════════════════════════════════
@router.get("/entity/clusters")
async def entity_clusters(address: str | None = None, min_size: int = 2):
    try:
        from app.entity_clustering import get_clustering_engine

        engine = get_clustering_engine()
        if address:
            entity = engine.graph.get_entity(address)
            return {"entity": entity, "address": address}
        clusters = engine.graph.find_clusters(min_size=min_size)
        return {"clusters": clusters, "total": len(clusters)}
    except Exception as e:
        return {"error": str(e)}


@router.post("/entity/link")
async def entity_link(data: dict):
    try:
        from app.entity_clustering import get_clustering_engine

        engine = get_clustering_engine()
        addr1 = data.get("address1", "")
        addr2 = data.get("address2", "")
        relationship = data.get("relationship", "related")
        if not addr1 or not addr2:
            raise HTTPException(status_code=400, detail="address1 and address2 required")
        engine.graph.link_wallets(addr1, addr2, relationship)
        return {
            "status": "linked",
            "address1": addr1,
            "address2": addr2,
            "relationship": relationship,
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# THREAT INTEL - Sanctions, reputation, blocklists
# ═══════════════════════════════════════════════════════════
@router.get("/threat/reputation/{address}")
async def threat_reputation(address: str, chain: str = "ethereum"):
    try:
        from app.threat_intel import check_wallet_reputation

        result = check_wallet_reputation(address, chain)
        return {"address": address, "chain": chain, "reputation": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/threat/sanctions/{address}")
async def threat_sanctions(address: str):
    try:
        from app.threat_intel import check_sanctions

        result = check_sanctions(address)
        return {"address": address, "sanctions": result, "sanctioned": len(result) > 0}
    except Exception as e:
        return {"error": str(e)}


@router.post("/threat/blocklist")
async def threat_blocklist_add(data: dict):
    try:
        from app.threat_intel import add_to_blocklist

        address = data.get("address", "")
        reason = data.get("reason", "")
        if not address:
            raise HTTPException(status_code=400, detail="address required")
        success = add_to_blocklist(address, reason)
        return {"address": address, "added": success, "reason": reason}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# EXCHANGE FLOW - CEX inflows/outflows
# ═══════════════════════════════════════════════════════════
@router.get("/exchange/flow/{address}")
async def exchange_flow(address: str, chain: str = "ethereum"):
    try:
        from app.exchange_flow_analyzer import analyze_entity_flows

        result = analyze_entity_flows(address, chain)
        return {"address": address, "chain": chain, "flows": result}
    except Exception as e:
        return {"error": str(e)}


@router.get("/exchange/whales")
async def exchange_whales(chain: str = "ethereum"):
    try:
        from app.exchange_flow_analyzer import ExchangeFlowAnalyzer

        analyzer = ExchangeFlowAnalyzer()
        whale_movements = analyzer.detect_large_transfers(chain=chain, min_value_usd=1000000)
        return {"whale_movements": whale_movements, "chain": chain}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# CROSS-CHAIN CORRELATOR
# ═══════════════════════════════════════════════════════════
@router.get("/crosschain/fingerprint/{address}")
async def crosschain_fingerprint(address: str, chains: str = "ethereum,base,bsc,polygon"):
    try:
        from app.cross_chain_correlator import CrossChainCorrelator

        correlator = CrossChainCorrelator()
        chain_list = [c.strip() for c in chains.split(",")]
        results = {}
        for chain in chain_list:
            try:
                fp = correlator.get_fingerprint(address, chain)
                results[chain] = fp
            except Exception:
                results[chain] = {"error": f"Chain {chain} unavailable"}
        return {"address": address, "fingerprints": results, "chains_checked": len(results)}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# AGENT MESH - 8 AI agents
# ═══════════════════════════════════════════════════════════
AGENTS = {
    "nexus": {
        "name": "NEXUS",
        "role": "Strategic Coordinator",
        "tier": "T0",
        "triggers": ["strategize", "plan", "coordinate"],
    },
    "scout": {
        "name": "SCOUT",
        "role": "Alpha Hunter",
        "tier": "T3",
        "triggers": ["find", "scan", "hunt", "alpha"],
    },
    "tracer": {
        "name": "TRACER",
        "role": "Forensic Investigator",
        "tier": "T1",
        "triggers": ["trace", "investigate", "wallet"],
    },
    "cipher": {
        "name": "CIPHER",
        "role": "Contract Auditor",
        "tier": "T1",
        "triggers": ["audit", "security", "contract"],
    },
    "sentinel": {
        "name": "SENTINEL",
        "role": "Rug Detector",
        "tier": "T2",
        "triggers": ["monitor", "watch", "alert", "rug"],
    },
    "chronicler": {
        "name": "CHRONICLER",
        "role": "Investigative Reporter",
        "tier": "T2",
        "triggers": ["write", "document", "report"],
    },
    "forge": {
        "name": "FORGE",
        "role": "Implementation Architect",
        "tier": "T1",
        "triggers": ["code", "implement", "build"],
    },
    "relay": {
        "name": "RELAY",
        "role": "Communications Coordinator",
        "tier": "T3",
        "triggers": ["format", "relay", "dispatch"],
    },
}


@router.get("/agents")
async def list_agents():
    return {"agents": AGENTS, "total": len(AGENTS)}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    agent = AGENTS.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    return agent


@router.post("/agents/{agent_id}/command")
async def agent_command(agent_id: str, data: dict):
    agent = AGENTS.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    command = data.get("command", "")
    return {
        "agent": agent["name"],
        "role": agent["role"],
        "command": command,
        "status": "queued",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ═══════════════════════════════════════════════════════════
# MCP SERVERS - Multi-chain data gateways
# ═══════════════════════════════════════════════════════════
@router.get("/mcp/servers")
async def mcp_servers_list():
    return {
        "servers": {
            "dexpaprika": "Real-time DEX data for 5M+ tokens across 20+ chains",
            "solana": "Solana RPC - wallet balances, token prices, DeFi yields",
            "dexscreener": "DEX pair data, token info, market stats",
            "defillama": "DeFi TVL, protocols, yields, fees",
            "coingecko": "13K+ tokens, global stats, historical data",
            "helius": "Enhanced Solana RPC - parsed txs, webhooks",
            "goplus": "Multi-chain token security - 700K+ tokens scanned",
            "rugcheck": "Solana token safety audit",
        },
        "status": "operational",
    }


# ═══════════════════════════════════════════════════════════
# SENTIMENT - Crypto market sentiment analysis
# ═══════════════════════════════════════════════════════════
@router.get("/sentiment/market")
async def sentiment_market():
    try:
        from app.ml_anomaly import AnomalyDetector

        detector = AnomalyDetector()
        anomalies = detector.detect_market_anomalies()
        return {"anomalies": anomalies, "timestamp": datetime.now(UTC).isoformat()}
    except Exception:
        # Fallback to fear & greed
        import httpx

        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                return {
                    "sentiment": {
                        "fear_greed_index": int(data.get("value", 50)),
                        "classification": data.get("value_classification", "Neutral"),
                    },
                    "source": "alternative.me",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
        return {"error": "Sentiment data unavailable"}


@router.get("/sentiment/token/{address}")
async def sentiment_token(address: str):
    # On-chain sentiment from buy/sell ratio
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    buys = p.get("txns", {}).get("h24", {}).get("buys", 0)
                    sells = p.get("txns", {}).get("h24", {}).get("sells", 0)
                    total = buys + sells
                    buy_ratio = buys / total if total > 0 else 0.5
                    sentiment = "bullish" if buy_ratio > 0.6 else ("bearish" if buy_ratio < 0.4 else "neutral")
                    return {
                        "token": address,
                        "sentiment": sentiment,
                        "buy_ratio": round(buy_ratio, 3),
                        "buys_24h": buys,
                        "sells_24h": sells,
                        "source": "dexscreener",
                    }
    except Exception:
        pass
    return {"token": address, "sentiment": "unknown"}


# ═══════════════════════════════════════════════════════════
# NANSEN - Wallet labels, smart money, token flow
# ═══════════════════════════════════════════════════════════
@router.get("/nansen/labels/{address}")
async def nansen_labels(address: str):
    try:
        from app.nansen_connector import get_wallet_labels

        result = get_wallet_labels(address)
        return {"address": address, "labels": result, "source": "nansen"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/nansen/smart-money")
async def nansen_smart_money():
    try:
        from app.nansen_connector import get_smart_money

        result = get_smart_money()
        return {"smart_money": result, "source": "nansen"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/nansen/activity/{address}")
async def nansen_activity(address: str):
    try:
        from app.nansen_connector import get_wallet_activity

        result = get_wallet_activity(address)
        return {"address": address, "activity": result, "source": "nansen"}
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════
# MEMPOOL - Bitcoin mempool monitoring
# ═══════════════════════════════════════════════════════════
@router.get("/mempool/status")
async def mempool_status():
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://mempool.space/api/v1/fees/recommended")
            if r.status_code == 200:
                fees = r.json()
                r2 = await c.get("https://mempool.space/api/mempool")
                mempool = r2.json() if r2.status_code == 200 else {}
                return {
                    "fees": fees,
                    "mempool_tx_count": mempool.get("count", 0),
                    "mempool_size_mb": round(mempool.get("vsize", 0) / 1_000_000, 2),
                    "source": "mempool.space",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
    except Exception:
        pass
    return {"error": "Mempool data unavailable"}


# ═══════════════════════════════════════════════════════════
# COINGECKO - Price data, trending, global metrics
# ═══════════════════════════════════════════════════════════
@router.get("/coingecko/ping")
async def coingecko_ping():
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.ping()
        return {"ping": result, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/coingecko/trending")
async def coingecko_trending():
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.get_trending()
        return {"trending": result, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/coingecko/markets")
async def coingecko_markets(vs_currency: str = "usd", per_page: int = 50):
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.get_market_overview(vs_currency=vs_currency, per_page=per_page)
        return {"markets": result, "vs_currency": vs_currency, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/coingecko/price/{coin_id}")
async def coingecko_price(coin_id: str):
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.get_token_price(coin_id)
        return {"coin_id": coin_id, "price": result, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/coingecko/global")
async def coingecko_global():
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.get_global_metrics()
        return {"global": result, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/coingecko/coin/{coin_id}")
async def coingecko_coin(coin_id: str):
    try:
        from app.coingecko_connector import get_coingecko_connector

        cg = get_coingecko_connector()
        result = await cg.get_token_detail(coin_id)
        return {"coin_id": coin_id, "coin": result, "source": "coingecko"}
    except Exception as e:
        return {"error": str(e)}
