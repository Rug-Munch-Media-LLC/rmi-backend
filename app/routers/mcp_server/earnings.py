"""Earnings dashboard — wallet balances, revenue by source."""

from __future__ import annotations
from typing import Any

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger("rmi_mcp_v3")
router = APIRouter(tags=["mcp_earnings"])

@router.get("/mcp/earnings")
async def earnings_dashboard() -> dict:
    """Live earnings dashboard - wallet balances, revenue by source."""
    from app.caching_shield.earnings_tracker import fetch_wallet_earnings, get_earnings_report
    wallets = await fetch_wallet_earnings()
    report = get_earnings_report()
    return {**report, "wallet_balances": wallets}

@router.get("/mcp/earnings/wallets")
async def earnings_wallets() -> Any:
    """Current payment wallet balances."""
    from app.caching_shield.earnings_tracker import fetch_wallet_earnings
    return await fetch_wallet_earnings()

@router.get("/mcp/earnings/sources")
async def earnings_by_source() -> Any:
    """Revenue broken down by tool, chain, and facilitator."""
    from app.caching_shield.earnings_tracker import get_revenue_by_source
    return get_revenue_by_source()
