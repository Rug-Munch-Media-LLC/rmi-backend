"""Public scanner endpoints - /api/v1/scanner/*.

Token + wallet scanning, scan status checks. Wires user-reported
addresses into the backend scanner pipeline. Also exposes:
  - GET /metrics — own-data coverage report
  - GET /sufficiency — data self-sufficiency report from ClickHouse
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

router = APIRouter(prefix="/scanner", tags=["scanner"])

# ── In-memory scan storage (ephemeral — survives for status polling) ──
_scan_results: dict[str, dict[str, Any]] = {}

# ── Token scan models ──


class ScanRequest(BaseModel):
    """Request model for token scanning."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    address: str = Field(
        ...,
        min_length=26,
        max_length=44,
    )
    depth: str = Field(
        default="quick",
        pattern=r"^(quick|standard|deep)$",
    )


class ScanResult(BaseModel):
    """Result of a token security scan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scan_id: str = Field(..., min_length=1, max_length=100)
    status: str = Field(
        ...,
        pattern=r"^(pending|running|completed|failed)$",
    )
    risk_score: int = Field(..., ge=0, le=100)
    risk_tier: str = Field(..., min_length=1, max_length=20)
    factors: list[str] = []


# ── Wallet scan models ──


class WalletScanRequest(BaseModel):
    """Request model for wallet scanning."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(
        default="solana",
        pattern=r"^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$",
    )
    address: str = Field(..., min_length=26, max_length=44)
    depth: str = Field(default="quick", pattern=r"^(quick|standard|deep)$")


class WalletScanResult(BaseModel):
    """Result of a wallet security scan."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scan_id: str = Field(..., min_length=1, max_length=100)
    status: str = Field(..., pattern=r"^(pending|running|completed|failed)$")
    address: str = Field(..., min_length=26, max_length=44)
    chain: str = Field(..., min_length=1, max_length=50)
    risk_score: int = Field(..., ge=0, le=100)
    risk_factors: list[str] = []
    token_holdings: list[dict[str, Any]] = []
    analysis: dict[str, Any] = {}


# ══════════════════════════════════════════════════════════════════════
# TOKEN SCAN
# ══════════════════════════════════════════════════════════════════════


@router.post("/scan", response_model=ScanResult)
async def scan_token(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
) -> Any:
    """Run a security scan on a token address.

    Kicks off the scan asynchronously and returns a scan_id immediately.
    Poll GET /scan/{scan_id} for status.
    """
    scan_id = f"scan_{uuid4().hex[:12]}"
    _scan_results[scan_id] = {
        "status": "pending",
        "risk_score": 0,
        "risk_tier": "unknown",
        "factors": [],
    }
    background_tasks.add_task(_run_token_scan, scan_id, request.chain, request.address, request.depth)
    return ScanResult(
        scan_id=scan_id,
        status="pending",
        risk_score=0,
        risk_tier="unknown",
    )


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan_status(scan_id: str) -> Any:
    """Check the status of a previously submitted scan."""
    result = _scan_results.get(scan_id)
    if not result:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanResult(scan_id=scan_id, **result)


async def _run_token_scan(scan_id: str, chain: str, address: str, depth: str) -> None:
    """Run the actual token scan in the background."""
    try:
        # Attempt real scanner integration
        from app.domains.scanner.service import scan_token as real_scan

        # Map depth → scanner tiers
        depth_tiers: dict[str, list[str]] = {
            "quick": ["free"],
            "standard": ["free", "pro"],
            "deep": ["free", "pro", "elite"],
        }
        tiers = depth_tiers.get(depth, ["free"])
        scan_result = await real_scan(token_address=address, chain=chain, tiers=tiers)

        # Convert domain ScanResult → API response format
        safety = getattr(scan_result, "safety_score", 50)
        risk_score = max(0, min(100, 100 - safety))
        risk_tier: str = "unknown"
        risk_flags: list[str] = getattr(scan_result, "risk_flags", [])
        if risk_score >= 70:
            risk_tier = "critical"
        elif risk_score >= 40:
            risk_tier = "medium"
        elif safety >= 70:
            risk_tier = "low"
        else:
            risk_tier = "caution"

        _scan_results[scan_id] = {
            "status": "completed",
            "risk_score": risk_score,
            "risk_tier": risk_tier,
            "factors": risk_flags,
        }
    except ImportError:
        # Fallback: compute basic risk from on-chain data
        try:
            from app.unified_scanner import scan_wallet as wallet_scan

            result = await wallet_scan(wallet_address=address, chain=chain)
            score = result.get("risk_score", 50)
            # TOP-100 #22: scan_wallet returns "risk_flags" (not flags/
            # risk_factors) — the old lookup always yielded []. Severity now
            # spans the full scale instead of collapsing to medium/low.
            if score >= 80:
                tier = "critical"
            elif score >= 60:
                tier = "high"
            elif score >= 40:
                tier = "medium"
            else:
                tier = "low"
            _scan_results[scan_id] = {
                "status": "completed",
                "risk_score": score,
                "risk_tier": tier,
                "factors": result.get("risk_flags", result.get("flags", result.get("risk_factors", []))),
            }
        except Exception as e:
            _scan_results[scan_id] = {
                "status": "failed",
                "risk_score": 0,
                "risk_tier": "unknown",
                "factors": [str(e)[:200]],
            }
    except Exception as e:
        _scan_results[scan_id] = {
            "status": "failed",
            "risk_score": 0,
            "risk_tier": "unknown",
            "factors": [str(e)[:200]],
        }


# ══════════════════════════════════════════════════════════════════════
# WALLET SCAN
# ══════════════════════════════════════════════════════════════════════


@router.post("/scan-wallet", response_model=WalletScanResult)
async def scan_wallet_endpoint(request: WalletScanRequest) -> Any:
    """Run a security scan on a wallet address.

    Used by walletpress and other apps to delegate wallet scanning
    to the RMI intelligence scanner.
    """
    scan_id = f"wscan_{uuid4().hex[:12]}"
    try:
        from app.unified_scanner import scan_wallet as _scan_wallet

        result = await _scan_wallet(wallet_address=request.address, chain=request.chain)
        return WalletScanResult(
            scan_id=scan_id,
            status="completed",
            address=request.address,
            chain=request.chain,
            risk_score=result.get("risk_score", 0),
            # TOP-100 #22: read risk_flags first — scan_wallet's actual key.
            risk_factors=result.get("risk_flags", result.get("flags", result.get("risk_factors", []))),
            token_holdings=result.get("top_tokens", result.get("token_holdings", [])),
            analysis=result,
        )
    except ImportError:
        return WalletScanResult(
            scan_id=scan_id,
            status="completed",
            address=request.address,
            chain=request.chain,
            risk_score=0,
            risk_factors=[],
            token_holdings=[],
            analysis={"note": "Full scanner not available — basic check only"},
        )
    except Exception as e:
        return WalletScanResult(
            scan_id=scan_id,
            status="failed",
            address=request.address,
            chain=request.chain,
            risk_score=0,
            risk_factors=[f"Scan error: {str(e)[:200]}"],
            token_holdings=[],
            analysis={},
        )


# ══════════════════════════════════════════════════════════════════════
# DATA COVERAGE METRICS
# ══════════════════════════════════════════════════════════════════════


@router.get("/metrics")
async def get_scanner_metrics() -> dict[str, Any]:
    """Get own-data coverage report for the scanner.

    Returns what percentage of scanner checks are answered by own data,
    external API calls, and DataBus. Also shows per-module breakdown
    and per-question data source mapping.

    Returns:
        {
            "own_data_pct": float,     # % answered by ClickHouse/own data
            "databus_pct": float,      # % answered by DataBus free tier
            "external_pct": float,     # % requiring external API
            "total_checks": int,
            "by_module": {...},
            "questions": {...},
            "questions_own_data_pct": float,  # % of 20 scanner questions
            "target_pct": float,              # Architecture target
        }
    """
    try:
        from app.domains.scanner.metrics import (
            OWN_DATA_PCT_TARGET,
            get_coverage_report,
        )

        report = get_coverage_report()
        report["target_pct"] = OWN_DATA_PCT_TARGET
        return report
    except ImportError:
        return {"error": "Metrics module not available", "own_data_pct": 0}
    except Exception as exc:
        return {"error": str(exc), "own_data_pct": 0}


@router.get("/sufficiency")
async def get_scanner_sufficiency() -> dict[str, Any]:
    """Get scanner data self-sufficiency report from ClickHouse.

    Queries the actual ClickHouse database to report how many scanner
    questions could be answered from our own data right now.

    Returns:
        {
            "own_data_questions": int,       # Questions answerable by own data
            "total_questions": int,           # Total scanner questions
            "own_data_pct": float,            # Coverage percentage
            "data_volume": {
                "evm_transfers_24h": int,
                "dex_events_24h": int,
                "deployer_events_total": int,
            },
            "capability": {
                "price": bool,
                "honeypot": bool,
                ...
            },
        }
    """
    try:
        from app.domains.scanner.metrics import OWN_DATA_CAPABILITY, SCANNER_QUESTIONS

        # Try ClickHouse to get actual data volume
        data_volume = await _query_self_sufficiency()

        own_data_count = sum(1 for v in OWN_DATA_CAPABILITY.values() if v)
        total = len(SCANNER_QUESTIONS)

        return {
            "own_data_questions": own_data_count,
            "total_questions": total,
            "own_data_pct": round(own_data_count / total * 100, 1) if total > 0 else 0,
            "data_volume": data_volume,
            "capability": OWN_DATA_CAPABILITY,
            "questions": {k: OWN_DATA_CAPABILITY.get(k, False) for k in SCANNER_QUESTIONS},
        }
    except ImportError:
        return {"error": "Metrics module not available"}
    except Exception as exc:
        return {"error": str(exc)}


async def _query_self_sufficiency() -> dict[str, Any]:
    """Query ClickHouse for data volume metrics."""
    import os

    volume: dict[str, Any] = {
        "evm_transfers_24h": 0,
        "dex_events_24h": 0,
        "deployer_events_total": 0,
        "dex_fill_rate_pct": 0.0,
        "freshness_hours": 0.0,
    }

    try:
        from clickhouse_driver import Client as CHClient

        ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
        ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
        ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
        ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
        ch_db = "wallet_memory"

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=5,
            send_receive_timeout=30,
        )

        # Query transfer counts
        rows = client.execute("""
            SELECT
                (SELECT count() FROM wallet_memory.evm_transfers
                 WHERE block_timestamp >= now() - INTERVAL 24 HOUR) AS transfers_24h,
                (SELECT count() FROM wallet_memory.dex_events
                 WHERE block_timestamp >= now() - INTERVAL 24 HOUR) AS dex_24h,
                (SELECT count() FROM wallet_memory.deployer_events) AS deployers_total,
                (SELECT round(countIf(token0!='')*100.0/count(), 1)
                 FROM wallet_memory.dex_events
                 WHERE block_timestamp >= now() - INTERVAL 24 HOUR
                   AND source='lite_indexer') AS dex_fill_rate
        """)

        if rows and rows[0]:
            row = rows[0]
            volume["evm_transfers_24h"] = row[0]
            volume["dex_events_24h"] = row[1]
            volume["deployer_events_total"] = row[2]
            volume["dex_fill_rate_pct"] = float(row[3] or 0) if row[3] else 0.0

            # Data freshness: age of latest dex event
            fresh_rows = client.execute("""
                SELECT round((now() - max(block_timestamp)) / 3600, 1)
                FROM wallet_memory.dex_events
                WHERE block_timestamp >= now() - INTERVAL 24 HOUR
            """)
            if fresh_rows and fresh_rows[0]:
                volume["freshness_hours"] = float(fresh_rows[0][0])

        return volume

    except Exception as exc:
        return {**volume, "error": str(exc)[:100]}
