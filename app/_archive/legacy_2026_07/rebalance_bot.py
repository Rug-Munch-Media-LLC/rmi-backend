#!/usr/bin/env python3
"""#20 - Cross-Chain Portfolio Rebalance Bot. Users set automated rebalancing rules.
Executes via x402, monitors via DataBus. "Keep 50% USDC on Solana, 30% ETH on Arbitrum." """

import os
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/rebalance", tags=["portfolio-rebalance"])

DATABUS = os.environ.get("DATABUS_URL", "http://localhost:8000/api/v1/databus")
BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")

# In-memory store for rebalancing rules (Redis/DB in prod)
_rebalance_rules: dict[str, dict] = {}


class RebalanceRule(BaseModel):
    user_id: str
    name: str
    allocations: list[dict]  # [{"chain": "solana", "token": "USDC", "target_pct": 50}, ...]
    rebalance_threshold_pct: float = 5.0  # Rebalance if any allocation off by >5%
    max_slippage_pct: float = 1.0
    enabled: bool = True


@router.post("/rules")
async def create_rebalance_rule(rule: RebalanceRule):
    """Create an automated portfolio rebalancing rule."""
    rule_id = f"{rule.user_id}:{rule.name}"
    _rebalance_rules[rule_id] = {
        "id": rule_id,
        "user_id": rule.user_id,
        "name": rule.name,
        "allocations": rule.allocations,
        "rebalance_threshold_pct": rule.rebalance_threshold_pct,
        "max_slippage_pct": rule.max_slippage_pct,
        "enabled": rule.enabled,
        "created_at": datetime.now(UTC).isoformat(),
        "last_rebalanced": None,
    }
    return {"id": rule_id, "status": "created", "allocations": rule.allocations}


@router.get("/rules/{user_id}")
async def list_rules(user_id: str):
    """List all rebalancing rules for a user."""
    user_rules = [r for rid, r in _rebalance_rules.items() if r["user_id"] == user_id]
    return {"rules": user_rules, "count": len(user_rules)}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Delete a rebalancing rule."""
    _rebalance_rules.pop(rule_id, None)
    return {"id": rule_id, "status": "deleted"}


@router.get("/simulate/{user_id}")
async def simulate_rebalance(user_id: str):
    """Simulate rebalancing - check what would change without executing."""
    user_rules = [r for rid, r in _rebalance_rules.items() if r["user_id"] == user_id and r["enabled"]]
    if not user_rules:
        return {"simulations": [], "note": "No enabled rules found"}

    simulations = []
    for rule in user_rules[:5]:
        current: list[dict] = []
        # Fetch current balances for each allocation
        async with httpx.AsyncClient(timeout=15) as client:
            for alloc in rule["allocations"]:
                try:
                    resp = await client.get(
                        f"{DATABUS}/{alloc['chain']}/balance/{user_id}", params={"token": alloc.get("token", "native")}
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        current.append(
                            {
                                "chain": alloc["chain"],
                                "token": alloc.get("token", "native"),
                                "balance_usd": data.get("value_usd", 0) or 0,
                                "current_pct": 0,
                                "target_pct": alloc["target_pct"],
                                "difference_pct": 0,
                            }
                        )
                except Exception:
                    current.append(
                        {
                            "chain": alloc["chain"],
                            "token": alloc.get("token", "native"),
                            "balance_usd": 0,
                            "target_pct": alloc["target_pct"],
                            "error": "unavailable",
                        }
                    )

        # Calculate percentages and differences
        total = sum(a["balance_usd"] for a in current) or 1
        needs_rebalance = False
        for a in current:
            a["current_pct"] = round((a["balance_usd"] / total) * 100, 1)
            a["difference_pct"] = round(a["current_pct"] - a["target_pct"], 1)
            if abs(a["difference_pct"]) > rule["rebalance_threshold_pct"]:
                needs_rebalance = True

        simulations.append(
            {
                "rule": rule["name"],
                "needs_rebalance": needs_rebalance,
                "total_value_usd": round(total, 2),
                "current_allocations": current,
                "rebalance_threshold_pct": rule["rebalance_threshold_pct"],
            }
        )

    return {"simulations": simulations}


@router.post("/execute/{rule_id}")
async def execute_rebalance(rule_id: str, background_tasks: BackgroundTasks):
    """Execute a rebalancing operation (simulation only - no real execution without x402)."""
    rule = _rebalance_rules.get(rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")

    if not rule["enabled"]:
        raise HTTPException(400, "Rule is disabled")

    # Simulation mode - return what WOULD be executed
    async with httpx.AsyncClient(timeout=15) as client:
        trades_needed: list[dict] = []
        current_values: list[dict] = []
        for alloc in rule["allocations"]:
            try:
                resp = await client.get(f"{DATABUS}/{alloc['chain']}/balance/{rule['user_id']}")
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    current_values.append(
                        {
                            "chain": alloc["chain"],
                            "token": alloc.get("token", "native"),
                            "value_usd": data.get("value_usd", 0) or 0,
                            "target_pct": alloc["target_pct"],
                        }
                    )
            except Exception:
                pass

        total = sum(a["value_usd"] for a in current_values) or 1
        for a in current_values:
            current_pct = (a["value_usd"] / total) * 100
            diff_pct = current_pct - a["target_pct"]
            diff_usd = total * (diff_pct / 100)
            if abs(diff_pct) > rule["rebalance_threshold_pct"]:
                trades_needed.append(
                    {
                        "chain": a["chain"],
                        "token": a["token"],
                        "action": "SELL" if diff_pct > 0 else "BUY",
                        "amount_usd": round(abs(diff_usd), 2),
                        "current_pct": round(current_pct, 1),
                        "target_pct": a["target_pct"],
                    }
                )

    rule["last_rebalanced"] = datetime.now(UTC).isoformat()

    return {
        "rule_id": rule_id,
        "status": "simulated",
        "total_value_usd": round(total, 2),
        "trades_needed": trades_needed,
        "note": "Simulation only. Real execution requires x402 payment authorization.",
    }
