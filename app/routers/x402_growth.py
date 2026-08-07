"""x402 Growth - webhooks, analytics, payment page, chains, affiliates.

Items 6-10 from the strategic roadmap.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any

import aiohttp

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.core.errors import NotFoundError
from app.core.logging import get_logger

logger = get_logger("x402.growth")

router = APIRouter(prefix="/api/v1/x402", tags=["x402-Growth"])

# ═══════════════════════════════════════════════════════════════════
#  6. PAYMENT WEBHOOKS
# ═══════════════════════════════════════════════════════════════════

_webhooks: dict[str, dict] = {}
_webhook_events: list[dict] = []


@router.post("/webhooks/register")
async def register_webhook(url: str = Query(...), events: str = Query("payment.confirmed")) -> dict:
    """Register a webhook URL for payment notifications.

    Events: payment.confirmed (when payment is verified), payment.failed,
    payment.refunded, trial.exhausted

    Webhook payload:
    {
        "event": "payment.confirmed",
        "tx_hash": "0x...",
        "tool": "token_scan",
        "amount_usd": 0.02,
        "chain": "base",
        "timestamp": 1234567890
    }
    """
    hook_id = f"wh_{secrets.token_hex(8)}"
    _webhooks[hook_id] = {
        "id": hook_id,
        "url": url,
        "events": events.split(","),
        "created_at": time.time(),
    }
    return {"webhook_id": hook_id, "url": url, "events": events}


@router.get("/webhooks")
async def list_webhooks() -> dict[str, list]:
    """List registered webhooks."""
    return {"webhooks": list(_webhooks.values()), "total": len(_webhooks)}


@router.delete("/webhooks/{hook_id}")
async def delete_webhook(hook_id: str) -> dict[str, bool]:
    """Delete a webhook."""
    _webhooks.pop(hook_id, None)
    return {"deleted": True}


@router.get("/webhooks/events")
async def webhook_events(limit: int = 100) -> dict:
    """View recent webhook delivery events."""
    return {"events": list(reversed(_webhooks))[:limit], "total": len(_webhook_events)}


async def fire_payment_webhook(
    tx_hash: str, tool: str, amount_usd: float, chain: str, status: str = "confirmed"
) -> None:
    """Fire webhook notifications for payment events."""
    import httpx

    event = {
        "event": f"payment.{status}",
        "tx_hash": tx_hash,
        "tool": tool,
        "amount_usd": amount_usd,
        "chain": chain,
        "timestamp": time.time(),
    }
    _webhook_events.append(event)
    async with httpx.AsyncClient(timeout=10) as c:
        for hook in _webhooks.values():
            if "payment.confirmed" in hook.get("events", []) and status == "confirmed":
                try:
                    await c.post(hook["url"], json=event)
                except (aiohttp.ClientError, TimeoutError) as e:
                    logger.warning(f"Webhook delivery failed for {hook['url']}: {e}")


# ═══════════════════════════════════════════════════════════════════
#  7. USAGE ANALYTICS — REMOVED (consolidated)
# ═══════════════════════════════════════════════════════════════════
#
# GET /api/v1/x402/usage is served by app.domains.x402 (mounted before this
# router in app/mount.py), so the duplicate handler registered here was
# shadowed and unreachable. Removed 2026-08-07 as part of endpoint
# consolidation. See app/domains/x402/router.py for the canonical handler.


# ═══════════════════════════════════════════════════════════════════
#  8. HOSTED PAYMENT PAGE
# ═══════════════════════════════════════════════════════════════════


@router.get("/pay/{invoice_id}")
async def payment_page(invoice_id: str) -> Any:
    """Hosted payment page for x402 invoices.

    Users connect their wallet and pay USDC with one click.
    On confirmation, they're redirected to the tool result.
    """
    page_id = invoice_id[:16]
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>x402 Payment</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0a0a0f;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.card{{background:#1a1a2e;border:1px solid #2a2a3e;border-radius:12px;padding:40px;max-width:480px;width:90%;text-align:center}}
.amount{{font-size:48px;font-weight:700;color:#4a6cf7;margin:20px 0}}
.address{{background:#0a0a0f;padding:12px;border-radius:8px;font-family:monospace;font-size:12px;word-break:break-all;margin:16px 0;color:#10b981}}
.btn{{background:#4a6cf7;color:#fff;border:none;padding:16px 32px;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer;width:100%}}
.btn:hover{{background:#3b5de7}}
.chain{{font-size:14px;color:#94a3b8;margin:8px 0}}
.tool{{font-size:12px;color:#666}}
</style>
</head>
<body>
<div class="card">
<div style="font-size:14px;color:#94a3b8;margin-bottom:8px">x402 Payment Required</div>
<div class="amount" id="amount">Loading...</div>
<div class="chain" id="chain">Loading...</div>
<div class="address" id="address">Loading...</div>
<div class="tool" id="tool">Invoice: {page_id}</div>
<button class="btn" onclick="copyAddress()">Copy Payment Address</button>
<div style="margin-top:12px;font-size:12px;color:#666">
Send USDC on the displayed chain to the address above.<br>
The tool will be unlocked automatically on confirmation.
</div>
</div>
<script>
async function loadInvoice() {{
const r = await fetch('/api/v1/x402/invoice/' + '{invoice_id}');
const d = await r.json();
document.getElementById('amount').textContent = '$' + d.amount_usd + ' USDC';
document.getElementById('chain').textContent = 'Chain: ' + d.chain.toUpperCase();
document.getElementById('address').textContent = d.pay_to;
}}
function copyAddress() {{
const addr = document.getElementById('address').textContent;
navigator.clipboard.writeText(addr);
alert('Address copied!');
}}
loadInvoice();
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/invoice/{invoice_id}")
async def get_invoice(invoice_id: str) -> dict:
    """Get invoice details (for hosted payment page)."""
    # Parse tool from invoice_id (format: uuid - lookup in pending)
    return {
        "invoice_id": invoice_id,
        "amount_usd": 0.01,
        "pay_to": os.getenv("X402_PAYMENT_ADDRESS", ""),
        "chain": os.getenv("X402_PAYMENT_CHAIN", "base"),
        "status": "pending",
        "tool": "unknown",
    }


# ═══════════════════════════════════════════════════════════════════
#  9. ADDITIONAL CHAINS - Polygon, Arbitrum, Optimism, Avalanche
# ═══════════════════════════════════════════════════════════════════

ADDITIONAL_CHAINS = {
    "polygon": {
        "network": "eip155:137",
        "chain_id": 137,
        "usdc": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "name": "USD Coin (PoS)",
        "version": "2",
        "verify": "self",
        "method": "local_eip712",
    },
    "arbitrum": {
        "network": "eip155:42161",
        "chain_id": 42161,
        "usdc": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "name": "USD Coin",
        "version": "2",
        "verify": "self",
        "method": "local_eip712",
    },
    "optimism": {
        "network": "eip155:10",
        "chain_id": 10,
        "usdc": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "name": "USD Coin",
        "version": "2",
        "verify": "self",
        "method": "local_eip712",
    },
    "avalanche": {
        "network": "eip155:43114",
        "chain_id": 43114,
        "usdc": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        "name": "USD Coin",
        "version": "2",
        "verify": "self",
        "method": "local_eip712",
    },
}


@router.post("/chains/add")
async def add_chains() -> dict:
    """Register Polygon, Arbitrum, Optimism, Avalanche into CHAIN_USDC.

    This extends payment coverage from 7 to 11 chains.
    The new chains use self-verification (same EVM wallet works across all).
    """
    from app.routers.x402_enforcement import CHAIN_USDC

    added = []
    for chain, cfg in ADDITIONAL_CHAINS.items():
        if chain not in CHAIN_USDC:
            CHAIN_USDC[chain] = cfg
            added.append(chain)
    return {"chains_added": added, "total_chains": len(CHAIN_USDC), "status": "active"}


@router.get("/chains")
async def list_chains() -> dict[str, dict]:
    """List all payment chains with USDC addresses."""
    from app.routers.x402_enforcement import CHAIN_USDC

    return {
        "chains": {
            k: {
                "network": v.get("network", ""),
                "usdc": v.get("usdc", ""),
                "method": v.get("method", v.get("verify", "self")),
                "facilitators": v.get("facilitators", []),
            }
            for k, v in CHAIN_USDC.items()
        },
        "total": len(CHAIN_USDC),
        "note": f"{len(CHAIN_USDC)} chains supported. All EVM chains share the same payment address.",
    }


# ═══════════════════════════════════════════════════════════════════
#  10. AFFILIATE SYSTEM
# ═══════════════════════════════════════════════════════════════════

_affiliates: dict[str, dict] = {}
_AFFILIATE_COMMISSION = 0.10  # 10%


@router.post("/affiliates/register")
async def register_affiliate(name: str = Query(...), email: str = Query("")) -> dict:
    """Register an affiliate account.

    Affiliates earn 10% of all x402 fees paid by users they refer.
    Payouts in USDC monthly.
    """
    code = f"x402_{secrets.token_hex(4)}"
    _affiliates[code] = {
        "code": code,
        "name": name,
        "email": email,
        "earned_usd": 0.0,
        "referred_users": 0,
        "created_at": time.time(),
    }
    return {"affiliate_code": code, "commission": f"{_AFFILIATE_COMMISSION * 100}%", "name": name}


@router.get("/affiliates/{code}")
async def affiliate_stats(code: str) -> Any:
    """Get affiliate stats - earnings, referrals, payouts."""
    aff = _affiliates.get(code)
    if not aff:
        raise NotFoundError("Affiliate not found")
    return aff


@router.get("/affiliates")
async def list_affiliates() -> dict[str, list]:
    """List all affiliates."""
    return {"affiliates": list(_affiliates.values()), "total": len(_affiliates)}


def record_affiliate_commission(affiliate_code: str, amount_usd: float) -> None:
    """Record affiliate commission for a payment."""
    aff = _affiliates.get(affiliate_code)
    if aff:
        commission = amount_usd * _AFFILIATE_COMMISSION
        aff["earned_usd"] += commission
        aff["referred_users"] += 1
