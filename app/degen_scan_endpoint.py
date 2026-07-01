#!/usr/bin/env python3
"""
FastAPI Endpoint for Degen Security Scanner
Mounts at: /degen-scan/*
Integrates with RMI backend main.py
"""

import logging
import os
import sys
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# Add the app directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.degen_security_scanner import (
    DegenSecurityScanner,
    SecurityReport,
    scan_token,
)

logger = logging.getLogger(__name__)

# ─── Pydantic Models ──────────────────────────────────────────────────────────


class DegenScanRequest(BaseModel):
    token_address: str = Field(..., min_length=32, max_length=48, description="Token mint address")
    quick: bool = Field(False, description="Skip slow operations for faster response")


class DegenScanResponse(BaseModel):
    success: bool
    token_address: str
    token_name: str
    token_symbol: str
    rug_pull_probability: int
    overall_risk: str
    confidence_level: str
    mint_authority_renounced: bool
    freeze_authority_renounced: bool
    top_10_concentration: float
    total_holders: int
    total_liquidity_usd: float
    bundler_detected: bool
    bundler_wallet_count: int
    sniper_count: int
    honeypot_detected: bool
    lp_locked: bool
    exchange_funding_pct: float
    critical_warnings: list
    recommendations: list
    full_report: dict[str, Any]
    scan_duration_ms: int
    timestamp: str


class BundleCheckResponse(BaseModel):
    token_address: str
    bundler_detected: bool
    bundler_wallets: list
    bundler_pattern: str
    bundler_supply_pct: float
    confidence: float
    details: dict[str, Any]


class FundingTraceResponse(BaseModel):
    token_address: str
    deployer: str
    funding_sources: list
    exchange_funding_pct: float
    organic_funding_pct: float
    mixer_detected: bool
    risk_level: str
    trace_graph: list


# ─── Cache Layer ──────────────────────────────────────────────────────────────

_scan_cache: dict[str, dict] = {}
CACHE_TTL_SECONDS = 300  # 5 minute cache


def _get_cached(token: str) -> dict | None:
    """Get cached scan result if still valid."""
    entry = _scan_cache.get(token)
    if entry:
        age = (datetime.utcnow() - entry["cached_at"]).total_seconds()
        if age < CACHE_TTL_SECONDS:
            return entry["data"]
    return None


def _set_cache(token: str, data: dict):
    """Cache scan result."""
    _scan_cache[token] = {
        "data": data,
        "cached_at": datetime.utcnow(),
    }


# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/degen-scan", tags=["Degen Security Scanner"])


@router.post("/full", response_model=DegenScanResponse)
async def full_degen_scan(request: DegenScanRequest):
    """
    Run a comprehensive DEGEN security scan on any Solana token.

    Analyzes 15+ scam indicators including:
    - Bundler detection, gas funding traces, exchange funding %
    - LP analysis, sniper metrics, insider trading detection
    - Wash trading, dev wallet forensics, authority renouncement
    - Tax detection, honeypot detection, holder concentration

    Returns a RUG PULL PROBABILITY score (0-100).
    """
    token = request.token_address

    # Check cache
    cached = _get_cached(token)
    if cached:
        return DegenScanResponse(**cached)

    try:
        report = await scan_token(token, quick=request.quick)

        response = {
            "success": True,
            "token_address": report.token_address,
            "token_name": report.token_name,
            "token_symbol": report.token_symbol,
            "rug_pull_probability": report.rug_pull_probability,
            "overall_risk": report.overall_risk,
            "confidence_level": report.confidence_level,
            "mint_authority_renounced": report.mint_authority_renounced,
            "freeze_authority_renounced": report.freeze_authority_renounced,
            "top_10_concentration": report.top_10_concentration,
            "total_holders": report.total_holders,
            "total_liquidity_usd": report.total_liquidity_usd,
            "bundler_detected": report.bundler_detected,
            "bundler_wallet_count": len(report.bundler_wallets),
            "sniper_count": report.sniper_count,
            "honeypot_detected": report.honeypot_detected,
            "lp_locked": any(p.lp_locked_percentage > 80 for p in report.liquidity_pools),
            "exchange_funding_pct": report.exchange_funding_percentage,
            "critical_warnings": report.critical_warnings,
            "recommendations": report.recommendations,
            "full_report": report.to_dict(),
            "scan_duration_ms": report.scan_duration_ms,
            "timestamp": datetime.utcnow().isoformat(),
        }

        _set_cache(token, response)
        return DegenScanResponse(**response)

    except Exception as e:
        logger.error(f"Degen scan failed for {token}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scan failed: {e!s}")


@router.get("/quick")
async def quick_degen_scan(
    token: str = Query(..., min_length=32, max_length=48),
):
    """
    Quick GET endpoint for instant risk assessment.
    Perfect for Telegram bot integration.
    """
    cached = _get_cached(token)
    if cached:
        return cached

    report = await scan_token(token, quick=True)

    response = {
        "success": True,
        "token": f"{report.token_name} (${report.token_symbol})",
        "rug_probability": report.rug_pull_probability,
        "risk": report.overall_risk,
        "confidence": report.confidence_level,
        "mint_renounced": "✅" if report.mint_authority_renounced else "❌",
        "freeze_renounced": "✅" if report.freeze_authority_renounced else "❌",
        "top_10_pct": report.top_10_concentration,
        "holders": report.total_holders,
        "liquidity_usd": report.total_liquidity_usd,
        "bundler": "🎯 YES" if report.bundler_detected else "No",
        "bundler_count": len(report.bundler_wallets),
        "snipers": report.sniper_count,
        "honeypot": "🍯 YES" if report.honeypot_detected else "No",
        "lp_locked": any(p.lp_locked_percentage > 80 for p in report.liquidity_pools),
        "warnings": report.critical_warnings[:5],
        "ms": report.scan_duration_ms,
    }

    _set_cache(token, response)
    return response


@router.get("/bundle-check", response_model=BundleCheckResponse)
async def check_bundler(
    token: str = Query(..., min_length=32, max_length=48),
):
    """
    Dedicated bundler detection endpoint.
    Identifies coordinated launch buying patterns.
    """
    scanner = DegenSecurityScanner()
    report = SecurityReport(token_address=token)
    await scanner._phase5_bundlers_snipers(report)

    return BundleCheckResponse(
        token_address=token,
        bundler_detected=report.bundler_detected,
        bundler_wallets=report.bundler_wallets,
        bundler_pattern=report.bundler_pattern,
        bundler_supply_pct=report.bundler_percentage_of_supply,
        confidence=0.85 if report.bundler_detected else 0.0,
        details={
            "sniper_count": report.sniper_count,
            "avg_sniper_entry_ms": report.avg_sniper_entry_time_ms,
            "sniper_volume_sol": report.sniper_volume_sol,
        },
    )


@router.get("/funding-trace", response_model=FundingTraceResponse)
async def trace_funding(
    token: str = Query(..., min_length=32, max_length=48),
):
    """
    Trace deployer wallet funding sources.
    Reveals exchange funding % and mixer usage.
    """
    scanner = DegenSecurityScanner()
    report = SecurityReport(token_address=token)
    await scanner._phase1_metadata(report)
    await scanner._phase4_funding(report)

    sources = []
    if report.deployer_funding:
        df = report.deployer_funding
        sources = [
            {
                "funder": df.initial_funder,
                "tag": df.funder_tag,
                "amount_sol": df.funding_amount_sol,
                "time": df.funding_time,
                "hops_from_exchange": df.hops_from_exchange,
                "is_exchange": df.is_exchange_direct,
            }
        ]

    return FundingTraceResponse(
        token_address=token,
        deployer=report.deployer,
        funding_sources=sources,
        exchange_funding_pct=report.exchange_funding_percentage,
        organic_funding_pct=report.organic_funding_percentage,
        mixer_detected=report.mixer_funding_detected,
        risk_level=report.funding_risk,
        trace_graph=[],
    )


@router.get("/compare")
async def compare_tokens(
    tokens: str = Query(..., description="Comma-separated token addresses"),
):
    """
    Compare security scores across multiple tokens.
    Perfect for evaluating which of several tokens is safest.
    """
    token_list = [t.strip() for t in tokens.split(",") if len(t.strip()) >= 32]
    if len(token_list) > 5:
        raise HTTPException(status_code=400, detail="Max 5 tokens per comparison")

    results = []
    for token in token_list:
        try:
            report = await scan_token(token, quick=True)
            results.append(
                {
                    "token": f"{report.token_name} (${report.token_symbol})",
                    "address": token,
                    "rug_probability": report.rug_pull_probability,
                    "risk": report.overall_risk,
                    "liquidity": report.total_liquidity_usd,
                    "holders": report.total_holders,
                    "bundler": report.bundler_detected,
                    "honeypot": report.honeypot_detected,
                    "mint_renounced": report.mint_authority_renounced,
                }
            )
        except Exception as e:
            results.append({"address": token, "error": str(e)})

    # Sort by safest first
    results.sort(key=lambda x: x.get("rug_probability", 100))

    return {
        "comparison": results,
        "safest": results[0] if results else None,
        "riskiest": results[-1] if len(results) > 1 else None,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─── Mount Instructions ───────────────────────────────────────────────────────

# In your main.py, add:
# from degen_scan_endpoint import router as degen_scan_router
# app.include_router(degen_scan_router)

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(title="RMI Degen Security Scanner")
    app.include_router(router)

    @app.get("/")
    def root():
        return {"status": "RMI Degen Scanner API", "version": "1.0.0"}

    uvicorn.run(app, host="0.0.0.0", port=8765)
