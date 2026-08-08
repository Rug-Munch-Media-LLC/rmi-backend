"""x402 SENTINEL TIER 1 evidence tools — multi-module token security.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/sentinel_scan         (full 9-module scan)
  POST /api/v1/x402-tools/holder_analysis
  POST /api/v1/x402-tools/bundle_detect
  POST /api/v1/x402-tools/exchange_fund_check
  POST /api/v1/x402-tools/liquidity_verify
  POST /api/v1/x402-tools/dev_reputation
  POST /api/v1/x402-tools/wash_trading
  POST /api/v1/x402-tools/social_engineering
  POST /api/v1/x402-tools/metadata_fingerprint
  POST /api/v1/x402-tools/sentiment_check
  POST /api/v1/x402-tools/pumpfun_analysis

TIER 2/3 modules (flash loans, oracle manipulation, static analysis,
decompiler, address labels, contract diff, fund flow, etc.) live in
tools/report_tools.py. label_tools.py hosts the standalone address
labeling endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.domains.billing.x402.shared import (
    SentinelModuleRequest,
    SentinelScanRequest,
    record_x402_payment,
)

sub_router = APIRouter(tags=["x402-sentinel-t1"])


@sub_router.post("/sentinel_scan")
async def sentinel_full_scan(req: SentinelScanRequest) -> dict[str, Any]:
    """Full SENTINEL deep scan - all 9 modules in parallel with graceful degradation.

    Pricing: $0.15 - the most comprehensive token security scan available.
    Returns composite risk score (0-100), per-module breakdown, and aggregated red flags.
    """
    try:
        from app.domains.scanners.sentinel_pipeline import dataclass_to_dict, run_sentinel_scan

        report = await run_sentinel_scan(
            token_address=req.address,
            chain=req.chain,
            dev_address=req.dev_address,
        )
        result = dataclass_to_dict(report)

        await record_x402_payment("sentinel_scan", "0.15", req.address)

        return {
            "tool": "SENTINEL Full Deep Scan",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/holder_analysis")
async def holder_analysis_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Holder Analysis - HHI concentration, fake diversification detection.

    Pricing: $0.05
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_holder_analysis

        result = await run_holder_analysis(req.address, req.chain)
        await record_x402_payment("holder_analysis", "0.05", req.address)

        return {
            "tool": "SENTINEL Holder Analysis",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/bundle_detect")
async def bundle_detect_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Bundle Detection - enhanced bundle/sniper detection, funding chain analysis.

    Pricing: $0.08
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_bundle_detection

        result = await run_bundle_detection(req.address, req.chain)
        await record_x402_payment("bundle_detect", "0.08", req.address)

        return {
            "tool": "SENTINEL Bundle Detection",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/exchange_fund_check")
async def exchange_fund_check_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Exchange Funder Check - CEX-funded wallet detection for token buyers.

    Pricing: $0.05
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_exchange_funding

        result = await run_exchange_funding(req.address, req.chain)
        await record_x402_payment("exchange_fund_check", "0.05", req.address)

        return {
            "tool": "SENTINEL Exchange Funder Check",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/liquidity_verify")
async def liquidity_verify_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Liquidity Verification - lock verification, fake locker detection, expiry monitoring.

    Pricing: $0.05
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_liquidity_verification

        result = await run_liquidity_verification(req.address, req.chain)
        await record_x402_payment("liquidity_verify", "0.05", req.address)

        return {
            "tool": "SENTINEL Liquidity Verification",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/dev_reputation")
async def dev_reputation_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Dev Reputation - serial rugg detection, cross-chain dev tracking.

    Pricing: $0.08
    Requires dev_address (deployer/creator wallet). Falls back to address if dev_address not provided.
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_dev_reputation

        dev_wallet = req.dev_address or req.address
        result = await run_dev_reputation(dev_wallet, chains=[req.chain])
        await record_x402_payment("dev_reputation", "0.08", req.address)

        return {
            "tool": "SENTINEL Dev Reputation",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": dev_wallet,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/wash_trading")
async def wash_trading_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Wash Trading Detection - circular transfer detection, cross-DEX loop analysis.

    Pricing: $0.08
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_wash_trading

        result = await run_wash_trading(req.address, req.chain)
        await record_x402_payment("wash_trading", "0.08", req.address)

        return {
            "tool": "SENTINEL Wash Trading Detection",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/social_engineering")
async def social_engineering_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Social Engineering & Identity Fraud Detection.

    Detects fake teams, AI-generated profiles, phishing domains,
    copied whitepapers, and social engineering campaigns.

    Pricing: $0.15
    """
    try:
        from app.social_engineering_detector import detect_social_engineering

        result = await detect_social_engineering(
            token_address=req.address,
            chain=req.chain,
            team_members=req.team_members if hasattr(req, "team_members") else None,
            social_links=req.social_links if hasattr(req, "social_links") else None,
            domain=req.domain if hasattr(req, "domain") else None,
            whitepaper_text=req.whitepaper if hasattr(req, "whitepaper") else None,
        )
        await record_x402_payment("social_engineering", "0.15", req.address)

        return {
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError, KeyError, OSError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/metadata_fingerprint")
async def metadata_fingerprint_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Metadata Fingerprint - HTML structure hashing, description similarity, social overlap detection.

    Pricing: $0.05
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_metadata_fingerprint

        result = await run_metadata_fingerprint(req.address, req.chain)
        await record_x402_payment("metadata_fingerprint", "0.05", req.address)

        return {
            "tool": "SENTINEL Metadata Fingerprint",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/sentiment_check")
async def sentiment_check_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL Sentiment Check - social sentiment scoring, bot campaign detection, pump probability.

    Pricing: $0.05
    """
    try:
        from app.domains.scanners.sentinel_pipeline import run_sentiment

        result = await run_sentiment(req.address, req.chain)
        await record_x402_payment("sentiment_check", "0.05", req.address)

        return {
            "tool": "SENTINEL Sentiment Check",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/pumpfun_analysis")
async def pumpfun_analysis_endpoint(req: SentinelModuleRequest) -> dict[str, Any]:
    """SENTINEL PumpFun Analysis - bonding curve progress, bot detection, graduation probability (Solana only).

    Pricing: $0.08
    Only works for Solana tokens. Returns error for other chains.
    """
    try:
        if req.chain.lower() != "solana":
            raise HTTPException(
                status_code=400,
                detail="PumpFun analysis is only available for Solana tokens. "
                f"Received chain={req.chain}. Use chain='solana'.",
            )

        from app.domains.scanners.sentinel_pipeline import run_pumpfun_analysis

        result = await run_pumpfun_analysis(req.address)
        await record_x402_payment("pumpfun_analysis", "0.08", req.address)

        return {
            "tool": "SENTINEL PumpFun Analysis",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "address": req.address,
            "chain": "solana",
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except HTTPException:
        raise
    except (ConnectionError, TimeoutError, ValueError, TypeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
