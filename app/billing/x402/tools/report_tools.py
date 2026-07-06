"""x402 SENTINEL TIER 2/3 + visualization tools — flash loans, oracle, governance,
proxy, static analysis, decompilation, fund flow, contract diff.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/flash_loan_detect
  POST /api/v1/x402-tools/pump_dump_detect
  POST /api/v1/x402-tools/oracle_manipulation
  POST /api/v1/x402-tools/governance_attack
  POST /api/v1/x402-tools/proxy_detect
  POST /api/v1/x402-tools/static_analysis
  POST /api/v1/x402-tools/decompiler_analysis
  POST /api/v1/x402-tools/fund_flow
  POST /api/v1/x402-tools/contract_diff

Address labels live in tools/label_tools.py.
TIER 1 modules (holder analysis, bundle detect, etc.) live in
tools/evidence_tools.py.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.billing.x402.shared import (
    SentinelModuleRequest,
    record_x402_payment,
)

sub_router = APIRouter(tags=["x402-sentinel-t2-t3"])


@sub_router.post("/flash_loan_detect")
async def flash_loan_detect_endpoint(req: SentinelModuleRequest):
    """SENTINEL Flash Loan Detection - borrow-then-dump patterns, single-block exploits.

    Pricing: $0.08
    Detects wallet activity that matches flash loan attack patterns:
    large buy/sell in consecutive blocks, near-zero pre-trade balance,
    volume exceeding pool liquidity ratio.
    """
    try:
        from app.scanners.sentinel_pipeline import run_flash_loan_detection

        result = await run_flash_loan_detection(req.address, req.chain)
        await record_x402_payment("flash_loan_detect", "0.08", req.address)

        return {
            "tool": "SENTINEL Flash Loan Detection",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/pump_dump_detect")
async def pump_dump_detect_endpoint(req: SentinelModuleRequest):
    """SENTINEL Pump-and-Dump Detection - volume spikes, coordinated buys, lifecycle stage.

    Pricing: $0.08
    Detects pump-and-dump patterns: sudden volume spikes (>5x average),
    coordinated fresh-wallet buy clusters, price-volume divergence,
    and rug pull lifecycle stage (deploy/pump/distribution/dump).
    """
    try:
        from app.scanners.sentinel_pipeline import run_pump_dump_detection

        result = await run_pump_dump_detection(req.address, req.chain)
        await record_x402_payment("pump_dump_detect", "0.08", req.address)

        return {
            "tool": "SENTINEL Pump-and-Dump Detection",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/oracle_manipulation")
async def oracle_manipulation_endpoint(req: SentinelModuleRequest):
    """SENTINEL Oracle Manipulation - oracle source analysis, price manipulation vulnerability.

    Pricing: $0.08
    Analyzes oracle risk: single-source dependency, pool depth vulnerability,
    DEX-vs-oracle price deviation, and overall manipulable score.
    Critical for lending/borrowing protocols that rely on price feeds.
    """
    try:
        from app.scanners.sentinel_pipeline import run_oracle_manipulation

        result = await run_oracle_manipulation(req.address, req.chain)
        await record_x402_payment("oracle_manipulation", "0.08", req.address)

        return {
            "tool": "SENTINEL Oracle Manipulation",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/governance_attack")
async def governance_attack_endpoint(req: SentinelModuleRequest):
    """x402 Governance Attack - governance concentration, timelock/quorum risk detection.

    Pricing: $0.08 (x402 tool #44)
    Detects governance risks: top holder dominance (>50%), missing timelock,
    low quorum thresholds enabling flash-loan governance attacks,
    and admin key concentration on Solana programs.

    Standalone module: app/governance_attack_detector.py
    """
    try:
        from app.governance_attack_detector import detect_governance_attack

        result = await detect_governance_attack(req.address, req.chain)
        await record_x402_payment("governance_attack", "0.08", req.address)

        return {
            "tool": "Governance Attack & Concentration Risk Detector",
            "version": "1.0",
            "pricing": "$0.08 per analysis, 1 free trial",
            "timestamp": datetime.utcnow().isoformat(),
            "token_address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/proxy_detect")
async def proxy_detect_endpoint(req: SentinelModuleRequest):
    """SENTINEL Proxy Detection - proxy resolution, implementation fingerprinting, upgrade risk.

    Pricing: $0.08
    Resolves proxy contracts to their real implementation, checks upgrade authority
    and timelock status, and fingerprints implementation bytecode against known
    rug contract patterns. Essential for EVM tokens behind proxies.
    """
    try:
        from app.scanners.sentinel_pipeline import run_proxy_detection

        result = await run_proxy_detection(req.address, req.chain)
        await record_x402_payment("proxy_detect", "0.08", req.address)

        return {
            "tool": "SENTINEL Proxy Detection",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/static_analysis")
async def static_analysis_endpoint(req: SentinelModuleRequest):
    """SENTINEL Static Analysis - Slither vulnerability detection + Forta alert integration.

    Pricing: $0.12
    Runs Slither static analysis on verified (or Heimdall-decompiled) contracts
    and queries Forta public alerts for the address. Returns vulnerability findings
    and active threat alerts.
    """
    try:
        from app.scanners.sentinel_pipeline import run_static_analysis

        result = await run_static_analysis(req.address, req.chain)
        await record_x402_payment("static_analysis", "0.12", req.address)

        return {
            "tool": "SENTINEL Static Analysis",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/decompiler_analysis")
async def decompiler_analysis_endpoint(req: SentinelModuleRequest):
    """SENTINEL Decompiler Analysis - Heimdall decompilation + whatsABI function extraction.

    Pricing: $0.10
    Decompiles unverified contract bytecode using Heimdall-rs, extracts function
    selectors via PUSH4 scanning, and identifies dangerous rug-pull function signatures.
    Essential for tokens with unverified source code.
    """
    try:
        from app.scanners.sentinel_pipeline import run_decompiler_analysis

        result = await run_decompiler_analysis(req.address, req.chain)
        await record_x402_payment("decompiler_analysis", "0.10", req.address)

        return {
            "tool": "SENTINEL Decompiler Analysis",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/fund_flow")
async def fund_flow_endpoint(req: SentinelModuleRequest):
    """SENTINEL Fund Flow Visualization - SVG fund flow graph for token analysis.
    Pricing: $0.10
    """
    try:
        from app.scanners.sentinel_pipeline import run_fund_flow

        result = await run_fund_flow(req.address, req.chain)
        await record_x402_payment("fund_flow", "0.10", req.address)
        return {
            "tool": "SENTINEL Fund Flow",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@sub_router.post("/contract_diff")
async def contract_diff_endpoint(req: SentinelModuleRequest):
    """SENTINEL Contract Diff - bytecode hash comparison against known rug contracts.

    Pricing: $0.10
    Compares a token's contract bytecode against a database of known rug contracts.
    Detects clones, forks, and near-identical contracts by hashing function selectors,
    bytecode sections, and metadata patterns. Identifies dangerous function signatures
    (withdrawAll, drain, setOwner, emergencyWithdraw) and rug-specific bytecode patterns.
    """
    try:
        from app.scanners.sentinel_pipeline import run_contract_diff

        result = await run_contract_diff(req.address, req.chain)
        await record_x402_payment("contract_diff", "0.10", req.address)

        return {
            "tool": "SENTINEL Contract Diff",
            "version": "1.0",
            "timestamp": datetime.utcnow().isoformat(),
            "address": req.address,
            "chain": req.chain,
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
