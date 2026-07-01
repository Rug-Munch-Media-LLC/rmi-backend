"""
Forensics API Router — Deep contract scan + cross-chain correlation + risk reports.
Connects to /api/v1/forensics/*
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/forensics", tags=["forensics"])


class DeepScanRequest(BaseModel):
    contract_address: str
    chain: str = "base"


class BatchScanRequest(BaseModel):
    contracts: list[str]
    chain: str = "base"


class RiskReportRequest(BaseModel):
    token_address: str
    chain: str = "solana"
    include_graph: bool = False


class ThreatCheckRequest(BaseModel):
    address: str
    chain_id: str = "1"  # 1=ethereum, 56=bsc, 8453=base, solana=solana


@router.post("/deep-scan")
async def deep_scan(req: DeepScanRequest):
    """Deep contract scan using slither/mythril."""
    try:
        from app.contract_deepscan import deep_scan_contract

        result = deep_scan_contract(req.contract_address, chain=req.chain)
        return {"contract": req.contract_address, "chain": req.chain, "scan": result}
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"Scanner unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.post("/batch-scan")
async def batch_deep_scan(req: BatchScanRequest):
    """Batch deep scan multiple contracts."""
    try:
        from app.contract_deepscan import batch_deep_scan

        result = batch_deep_scan(req.contracts, chain=req.chain)
        return {"contracts": len(req.contracts), "chain": req.chain, "results": result}
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.post("/risk-report")
async def risk_report(req: RiskReportRequest):
    """Generate comprehensive rug risk report."""
    try:
        from app.advanced_analysis import RugRiskReport

        report = RugRiskReport(token_address=req.token_address, chain=req.chain)
        return {"token_address": req.token_address, "chain": req.chain, "report": str(report)}
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.post("/cross-chain")
async def cross_chain_correlate(addresses: list[str] = Query(...), chains: list[str] | None = Query(None)):
    """Correlate wallets across chains using behavioral fingerprinting + CEX patterns."""
    try:
        from app.cross_chain_correlator import get_cross_chain_correlator

        cc = get_cross_chain_correlator()
        profiles = await cc.correlate(addresses=addresses, chains=chains)
        return {
            "addresses_scanned": len(addresses),
            "profiles_found": len(profiles),
            "profiles": [
                {
                    "entity_id": p.entity_id,
                    "total_addresses": p.total_addresses,
                    "chains": dict(p.chains),
                    "links": len(p.links),
                    "risk_score": p.risk_score,
                }
                for p in profiles
            ],
        }
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])


@router.get("/health")
async def forensics_health():
    return {"status": "ok", "service": "forensics-engine"}


@router.post("/threat-check")
async def threat_check(req: ThreatCheckRequest):
    """Multi-source threat intel check: CryptoScamDB + GoPlus + Januus (all free)."""
    try:
        from app.threat_feeds import get_threat_feeds

        feeds = get_threat_feeds()
        result = await feeds.full_check(address=req.address, chain_id=req.chain_id)
        return result
    except ImportError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:500])
