"""Supabase x402 payments persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.services.supabase_transport import (
    _get,
    _get_headers,
    _get_url,
    _post,
)


async def record_x402_payment_supabase(
    tool: str,
    amount_atoms: str,
    chain: str,
    payer: str = "unknown",
    tx_hash: str = "",
    status: str = "fulfilled",
) -> dict[str, Any] | None:
    """Insert a payment record into x402_payments table."""
    return await _post(
        "x402_payments",
        {
            "tool": tool,
            "amount_atoms": str(amount_atoms),
            "chain": chain,
            "payer": payer,
            "tx_hash": tx_hash,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


async def get_x402_payments(
    tool: str | None = None,
    chain: str | None = None,
    payer: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Any]:
    """Query x402_payments with optional filters."""
    params = {}
    if tool:
        params["tool"] = tool
    if chain:
        params["chain"] = chain
    if payer:
        params["payer"] = payer
    if status:
        params["status"] = status
    return await _get("x402_payments", params, order="created_at.desc", limit=limit)


async def get_x402_payment_by_tx(tx_hash: str) -> dict[str, Any] | None:
    """Get a specific payment by tx_hash."""
    data = await _get("x402_payments", {"tx_hash": tx_hash}, limit=1)
    return data[0] if data else None


async def get_x402_earnings_summary() -> dict[str, Any]:
    """Get earnings summary from Supabase.

    Aggregates total by chain, tool, and day using Supabase data.
    """
    try:
        rows = await _get("x402_payments", select="tool,amount_atoms,chain,created_at,status", limit=5000)
        total_atoms = 0
        by_chain = {}
        by_tool = {}
        by_day = {}
        for row in rows:
            if row.get("status") != "fulfilled":
                continue
            try:
                amt = int(row.get("amount_atoms", "0") or "0")
            except (ValueError, TypeError):
                amt = 0
            total_atoms += amt
            chain = row.get("chain", "unknown")
            by_chain[chain] = by_chain.get(chain, 0) + amt
            tool = row.get("tool", "unknown")
            by_tool[tool] = by_tool.get(tool, 0) + amt
            # Day extraction
            created = row.get("created_at", "")
            day = created[:10] if created else "unknown"
            by_day[day] = by_day.get(day, 0) + amt

        return {
            "total_usdc": round(total_atoms / 1e6, 6),
            "by_chain_usdc": {k: round(v / 1e6, 6) for k, v in by_chain.items()},
            "by_tool_usdc": {k: round(v / 1e6, 6) for k, v in by_tool.items()},
            "by_day_usdc": {k: round(v / 1e6, 6) for k, v in sorted(by_day.items())[-30:]},
            "record_count": len(rows),
        }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        return {
            "total_usdc": 0,
            "by_chain_usdc": {},
            "by_tool_usdc": {},
            "by_day_usdc": {},
            "record_count": 0,
        }


async def ensure_x402_payments_table() -> bool:
    """Create x402_payments table in Supabase if it doesn't exist."""
    sql = """
    DO $$ BEGIN
        CREATE TABLE IF NOT EXISTS x402_payments (
            id BIGSERIAL PRIMARY KEY,
            tool TEXT NOT NULL DEFAULT 'unknown',
            amount_atoms TEXT NOT NULL DEFAULT '0',
            chain TEXT NOT NULL DEFAULT 'unknown',
            payer TEXT NOT NULL DEFAULT 'unknown',
            tx_hash TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'fulfilled'
        );
    END $$;
    CREATE INDEX IF NOT EXISTS idx_x402_payments_tool ON x402_payments(tool);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_chain ON x402_payments(chain);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_tx_hash ON x402_payments(tx_hash);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_created_at ON x402_payments(created_at);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_payer ON x402_payments(payer);
    -- RLS: service_role only -- payment data is backend-only
    ALTER TABLE x402_payments ENABLE ROW LEVEL SECURITY;
    DO $$ BEGIN
        DROP POLICY IF EXISTS "Service role full access" ON x402_payments;
    EXCEPTION WHEN OTHERS THEN NULL; END $$;
    CREATE POLICY "Service role full access" ON x402_payments FOR ALL TO service_role USING (true) WITH CHECK (true);
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_get_url()}/rest/v1/rpc/exec_sql",
                json={"sql": sql},
                headers=_get_headers(),
            )
            return r.status_code in (200, 204)
    except (httpx.HTTPError, httpx.TimeoutException):
        return False
