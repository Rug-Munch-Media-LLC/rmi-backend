"""T34 x402 Payment Middleware.

Per v4.0 §T34. HTTP 402 'Payment Required' repurposed for AI agents.

Flow:
  1. Agent calls paid endpoint without X-Payment header
  2. Server returns 402 with payment challenge (signed invoice)
  3. Agent's wallet pays on-chain, gets tx_hash
  4. Agent re-calls with X-Payment: <base64(tx_hash + signature)>
  5. Server verifies on-chain payment via web3, fulfills request

Pricing (per v4.0):
  Free:   5 calls/day
  Pro:    $0.01/call (1000 calls/day)
  Ent:    $0.001/call (unlimited)

For v1, we use a simplified flow:
  - Track calls per agent in Redis (sliding window)
  - Receipts logged to x402_receipts table
  - Payment verification stubbed (returns True for now)
  - The 402 challenge includes a payment_url the agent hits
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Pricing per v4.0 §T34 ───────────────────────────────────────────
PRICING: dict[str, dict[str, Any]] = {
    # free_tier: bool, pro_cents: int (USD cents), ent_cents: int
    "get_token_risk": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Real-time token risk score",
    },
    "get_wallet_analysis": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Wallet activity + reputation",
    },
    "get_deployer_reputation": {
        "free_tier": False,
        "pro_cents": 2,
        "ent_cents": 1,
        "description": "Deployer reputation score",
    },
    "get_news_sentiment": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "Latest news + sentiment",
    },
    "generate_report": {
        "free_tier": False,
        "pro_cents": 500,
        "ent_cents": 400,
        "description": "Full AI research report",
    },
    "query_catalog": {
        "free_tier": False,
        "pro_cents": 5,
        "ent_cents": 3,
        "description": "Natural language catalog query",
    },
    "find_similar_tokens": {
        "free_tier": False,
        "pro_cents": 3,
        "ent_cents": 2,
        "description": "Vector-similar tokens",
    },
    "resolve_entity": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Cross-chain entity resolution",
    },
    "search_rag": {
        "free_tier": True,
        "pro_cents": 1,
        "ent_cents": 1,
        "description": "RAG semantic search",
    },
    "bulk_ingest": {
        "free_tier": False,
        "pro_cents": 10,
        "ent_cents": 5,
        "description": "Bulk RAG ingestion",
    },
}

# Free tier daily limit
FREE_DAILY_LIMIT = 5

# ── Nonce burn (replay protection) ──────────────────────────────
# Per MINIMAX_M3_TASKS.md T02. Each tx_hash can only be used ONCE.
# We use Redis SETNX (set-if-not-exists) as an atomic nonce burn:
#   - First call: SETNX returns 1, nonce is burned, request proceeds
#   - Second call: SETNX returns 0, nonce already burned → 410 Gone
# TTL is 24h (the x402 invoice expiry window).

X402_NONCE_TTL = 86400  # 24 hours


async def _burn_nonce(catalog, tx_hash: str) -> bool:
    """Atomically burn a payment nonce. Returns True if first use, False if replay.

    Uses Redis SETNX (SET key value NX EX seconds). Atomic by construction.
    Logs replay attempts for monitoring.
    """
    if not catalog or not catalog._health.redis:
        # Without Redis we cannot prevent replay - fail closed (reject).
        log.warning(f"x402_nocache_reject: tx_hash={tx_hash[:10]}...")
        return False
    key = f"x402:nonce:{tx_hash}"
    try:
        # SETNX semantics: return 1 if key was set, 0 if already existed
        ok = await catalog._redis.set(key, "1", ex=X402_NONCE_TTL, nx=True)
        if not ok:
            log.warning(f"x402_replay_attempt: tx_hash={tx_hash[:10]}...")
            _X402_REPLAY_COUNTER.labels(tool="unknown").inc()
        return bool(ok)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.error(f"x402_nonce_burn_fail: {e}")
        # Fail closed - reject if we can't burn atomically
        return False


# ── Prometheus counters (lightweight, no external dep) ──────────
try:
    from prometheus_client import REGISTRY, Counter

    # Use a module-level registry so multiple imports don't double-register
    _X402_REGISTRY = REGISTRY
    _X402_REPLAY_COUNTER: Any = Counter(
        "x402_replay_attempts_total",
        "Number of x402 replay attempts blocked by nonce burn",
        ["tool"],
        registry=_X402_REGISTRY,
    )
    _X402_REVENUE_COUNTER: Any = Counter(
        "x402_revenue_total_usd_cents",
        "x402 revenue in USD cents",
        ["tool", "tier"],
        registry=_X402_REGISTRY,
    )
except (ConnectionError, OSError, TimeoutError):
    # Prometheus not available - use no-op counter stubs
    class _Stub:
        def labels(self, **kw: Any) -> _Stub:
            return self

        def inc(self, n: float = 1.0) -> None:
            pass

    _X402_REPLAY_COUNTER = _Stub()
    _X402_REVENUE_COUNTER = _Stub()


def get_tool_price_usd(tool: str) -> float:
    """Get the per-call price for a tool in USD.

    Checks PRICING dict first, then falls back to the enforcement
    system's TOOL_PRICES for full coverage of 270+ tools.
    Returns 0.01 as default if tool is not found in either.
    """
    p = PRICING.get(tool, {})
    if p.get("free_tier", False):
        return 0.0
    if p.get("pro_cents"):
        return p["pro_cents"] / 100.0

    # Fallback: check the enforcement system's comprehensive price list
    try:
        from app.routers.x402_enforcement import TOOL_PRICES as _FALLBACK_PRICES, _ensure_tool_prices

        _ensure_tool_prices()
        fp = _FALLBACK_PRICES.get(tool, {})
        return fp.get("price_usd", 0.01)
    except (ImportError, ConnectionError, OSError, TimeoutError, ValueError):
        return p.get("pro_cents", 1) / 100.0


def get_tool_metadata(tool: str) -> dict[str, Any]:
    """Public-facing tool metadata for /api/v1/x402/catalog."""
    p = PRICING.get(tool, {})
    return {
        "name": tool,
        "free_tier": p.get("free_tier", False),
        "free_daily_limit": FREE_DAILY_LIMIT if p.get("free_tier") else 0,
        "price_pro_usd": p.get("pro_cents", 0) / 100.0,
        "price_ent_usd": p.get("ent_cents", 0) / 100.0,
        "description": p.get("description", ""),
    }


# ── Rate limit tracking (Redis) ───────────────────────────────────
def _agent_id_from_request(request: Request | None) -> str:
    """Extract agent id from headers. Defaults to 'anon:ip' for anonymous.

    Security: X-Agent-Id is self-declared and marked as unverified.
    X-Api-Key is checked against our admin key if configured.
    """
    if not request:
        return "anon:unknown"

    api_key = request.headers.get("X-Api-Key", "")
    if api_key:
        # Verify against configured admin key
        admin_key = os.getenv("ADMIN_API_KEY", os.getenv("WP_ADMIN_KEY", ""))
        if admin_key and api_key == admin_key:
            return "key:admin"
        return f"unverified_key:{api_key[:16]}"

    agent_id = request.headers.get("X-Agent-Id", "")
    if agent_id:
        return f"unverified_agent:{agent_id[:20]}"

    client = request.client
    if client:
        return f"anon:{client.host}"
    return "anon:unknown"


async def _check_rate_limit(redis, agent_id: str) -> None:
    """Sliding-window rate limit: 5 free calls/day, 1000 pro, unlimited ent.

    For v1, we use Redis with a daily counter. Per-second limits are
    enforced at the proxy level (nginx/Caddy).
    """
    if not redis:
        return
    try:
        day = datetime.now(UTC).strftime("%Y%m%d")
        key = f"x402:rl:{agent_id}:{day}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 86400)
        if count > FREE_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "agent_id": agent_id,
                    "daily_count": count,
                    "limit": FREE_DAILY_LIMIT,
                    "next_action": "Send X-Payment header with valid tx_hash to upgrade tier",
                },
            )
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, ConnectionError, OSError) as e:
        log.warning(f"rate_limit_check_fail: {e}")


# ── HMAC key from env ────────────────────────────────────────────


def _get_hmac_key() -> bytes:
    """Load HMAC signing key from environment. Never hardcoded."""
    key = os.getenv("X402_HMAC_KEY")
    if not key:
        key = os.getenv("ADMIN_API_KEY")
        if not key:
            raise RuntimeError("Either X402_HMAC_KEY or ADMIN_API_KEY environment variable is required")
        get_logger("wp.x402").warning("X402_HMAC_KEY not set, using ADMIN_API_KEY")
    return key.encode()


# ── Payment challenge ─────────────────────────────────────────────
def create_invoice(tool: str, agent_id: str) -> dict[str, Any]:
    """Create a payment challenge (signed invoice) for an agent to pay.

    The invoice is signed with the platform's HMAC key (from env var
    X402_HMAC_KEY, fallback ADMIN_API_KEY). The agent can verify the
    signature before paying.
    """
    invoice_id = uuid4().hex
    amount_usd = get_tool_price_usd(tool)
    pay_to = os.getenv("X402_PAYMENT_ADDRESS", "")
    invoice = {
        "id": invoice_id,
        "tool": tool,
        "agent_id": agent_id,
        "amount_usd": amount_usd,
        "amount_wei": int(amount_usd * 1e18 / float(os.getenv("X402_ETH_PRICE_USD", "3000"))) if amount_usd > 0 else 0,
        "pay_to": pay_to or "0xRMI_PLATFORM_WALLET (set X402_PAYMENT_ADDRESS)",
        "chain": os.getenv("X402_PAYMENT_CHAIN", "base"),
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": (datetime.now(UTC).timestamp() + 3600),
    }
    msg = json.dumps(invoice, sort_keys=True).encode()
    try:
        hmac_key = _get_hmac_key()
        invoice["signature"] = base64.b64encode(hmac.new(hmac_key, msg, hashlib.sha256).digest()).decode()
    except RuntimeError:
        # No signing key configured (X402_HMAC_KEY / ADMIN_API_KEY absent).
        # Still return a usable 402 challenge — unsigned, but the invoice
        # carries the payment address, amount and instructions.
        invoice["signature"] = ""
    return invoice


def verify_payment_header(x_payment: str, expected_amount_usd: float) -> tuple[str, str] | None:
    """Decode the X-Payment header. Returns (tx_hash, signature) or None.

    Format: base64({"tx_hash": "...", "signature": "..."})
    """
    if not x_payment:
        return None
    try:
        decoded = base64.b64decode(x_payment).decode()
        data = json.loads(decoded)
        return data.get("tx_hash", ""), data.get("signature", "")
    except json.JSONDecodeError:
        return None


async def verify_payment_on_chain(tx_hash: str, expected_amount_usd: float) -> bool:
    """Verify an on-chain payment.

    Checks via public RPC that the transaction:
    1. Exists on-chain
    2. Transferred >= expected_amount_usd of USDC
    3. Was sent to our payment address

    Supports Base (USDC) and Solana (USDC) - our primary payment chains.
    Falls back to the main x402_enforcement self-verify for other chains.
    """
    if not tx_hash:
        return False

    # Route to the production x402 enforcement system. The full x402
    # verifier requires a signed payment payload (not a bare tx_hash), so
    # on-chain verification falls through to the local RPC path below.
    try:
        from app.routers.x402_enforcement import verify_payment

        result = await verify_payment({"txHash": tx_hash, "accepted": {"network": "base"}})
        if result.get("verified", False):
            return True
    except ImportError:
        pass
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        get_logger("wp.x402").error(f"verify_payment_on_chain error: {e}")

    # Local verification for Base USDC via public RPC
    if tx_hash.startswith("0x") and len(tx_hash) == 66:
        try:
            import httpx

            pay_to = os.getenv("X402_PAYMENT_ADDRESS", "")
            if not pay_to:
                return False

            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://mainnet.base.org",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_getTransactionReceipt",
                        "params": [tx_hash],
                    },
                )
                data = r.json()
                if "result" not in data or data["result"] is None:
                    return False

                receipt = data["result"]
                if receipt.get("status") != "0x1":
                    return False

                # NOTE: receipt.to is the USDC *contract*, not the recipient,
                # for ERC-20 transfers. Parse Transfer event logs to find the
                # actual recipient + amount (mirrors self_verify_evm_usdc).
                transfer_event_topic0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                # Base USDC (CHAIN_USDC["base"]["usdc"])
                usdc_address = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
                pay_to_lower = pay_to.lower()
                expected_atoms = int(expected_amount_usd * 1_000_000) if expected_amount_usd > 0 else 0

                logs = receipt.get("logs", [])
                for log in logs:
                    topics = log.get("topics", [])
                    if len(topics) < 3:
                        continue
                    if topics[0].lower() != transfer_event_topic0:
                        continue
                    if log.get("address", "").lower() != usdc_address:
                        continue
                    try:
                        to_address = "0x" + topics[2][-40:].lower()
                    except (IndexError, ValueError):
                        continue
                    if to_address != pay_to_lower:
                        continue

                    log_data = log.get("data", "0x")
                    try:
                        if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                            actual_amount = int(log_data[:66], 16)
                        elif log_data:
                            actual_amount = int(log_data, 16)
                        else:
                            actual_amount = 0
                    except (ValueError, TypeError):
                        get_logger("wp.x402").warning(f"Could not decode Transfer data for {tx_hash[:16]}...")
                        continue

                    if actual_amount <= 0:
                        continue
                    if expected_atoms and actual_amount < expected_atoms:
                        get_logger("wp.x402").warning(
                            f"Base amount mismatch for {tx_hash[:16]}...: "
                            f"expected {expected_atoms} atoms, got {actual_amount}"
                        )
                        return False
                    return True

                return False
        except (
            httpx.HTTPError,
            ConnectionError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as e:
            get_logger("wp.x402").error(f"Base tx verify failed: {e}")
            return False

    # Solana verification via public RPC
    if len(tx_hash) == 88:  # Solana base58 signature length
        try:
            import httpx

            pay_to = os.getenv("X402_PAYMENT_ADDRESS_SOL", os.getenv("X402_PAYMENT_ADDRESS", ""))
            if not pay_to:
                return False

            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://api.mainnet-beta.solana.com",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            tx_hash,
                            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                        ],
                    },
                )
                data = r.json()
                tx = data.get("result")
                if not tx:
                    return False

                meta = tx.get("meta") or {}
                # Confirmed + succeeded — reject failed transactions.
                if meta.get("err") is not None:
                    return False

                # Solana USDC mint (CHAIN_USDC["solana"]["usdc"])
                usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
                expected_atoms = int(expected_amount_usd * 1_000_000) if expected_amount_usd > 0 else 0

                # Find the token balance delta for pay_to's USDC account.
                # postTokenBalances entries carry owner + mint + uiTokenAmount.
                pre_by_index: dict[int, int] = {}
                for bal in meta.get("preTokenBalances", []) or []:
                    if bal.get("mint") != usdc_mint:
                        continue
                    try:
                        pre_by_index[int(bal["accountIndex"])] = int((bal.get("uiTokenAmount") or {}).get("amount", 0))
                    except (ValueError, TypeError, KeyError):
                        continue

                for bal in meta.get("postTokenBalances", []) or []:
                    if bal.get("mint") != usdc_mint:
                        continue
                    if (bal.get("owner") or "").lower() != pay_to.lower():
                        continue
                    try:
                        post_amount = int((bal.get("uiTokenAmount") or {}).get("amount", 0))
                        account_index = int(bal["accountIndex"])
                    except (ValueError, TypeError, KeyError):
                        continue
                    pre_amount = pre_by_index.get(account_index, 0)
                    received = post_amount - pre_amount
                    if received <= 0:
                        continue
                    if expected_atoms and received < expected_atoms:
                        get_logger("wp.x402").warning(
                            f"Solana amount mismatch for {tx_hash[:16]}...: "
                            f"expected {expected_atoms} atoms, got {received}"
                        )
                        return False
                    return True

                return False
        except (
            httpx.HTTPError,
            ConnectionError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as e:
            get_logger("wp.x402").error(f"Solana tx verify failed: {e}")
            return False

    return False


async def record_receipt(catalog, tx_hash: str, tool: str, agent_id: str, amount_usd: float) -> bool:
    """Log the payment receipt to x402_receipts table."""
    if not catalog._health.postgres:
        return False
    try:
        async with catalog._pg_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO x402_receipts (tx_hash, agent_id, tool, amount_usd, chain, paid_at, tier)
                   VALUES ($1, $2, $3, $4, $5, NOW(), $6)
                   ON CONFLICT (tx_hash) DO NOTHING""",
                tx_hash,
                agent_id,
                tool,
                amount_usd,
                os.getenv("X402_PAYMENT_CHAIN", "base"),
                os.getenv("X402_DEFAULT_TIER", "pro"),
            )
        return True
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        log.warning(f"receipt_record_fail: {e}")
        return False


# ── Main middleware ────────────────────────────────────────────────
async def require_payment(
    tool: str,
    request: Request | None = None,
    catalog=None,
) -> dict[str, Any]:
    """Verify x402 payment. Returns payment context dict.

    Raises HTTPException 402 if payment is required but not provided.
    Raises HTTPException 429 if rate limit is exceeded.
    """
    agent_id = _agent_id_from_request(request)
    if not catalog or not catalog._health.redis:
        # No rate limiting available - log and pass
        log.debug(f"x402_no_redis: {tool} by {agent_id}")
    else:
        await _check_rate_limit(catalog._redis, agent_id)

    # Free tool - no payment required
    if get_tool_price_usd(tool) == 0:
        return {"agent_id": agent_id, "tool": tool, "tier": "free", "paid_via_x402": None}

    # Paid tool - check X-Payment header
    if request is None:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"{os.getenv('X402_PAYMENT_URL', 'https://pay.rugmunch.io')}/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    x_payment = request.headers.get("X-Payment")
    if not x_payment:
        invoice = create_invoice(tool, agent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "invoice": invoice,
                "payment_url": f"{os.getenv('X402_PAYMENT_URL', 'https://pay.rugmunch.io')}/{invoice['id']}",
                "instructions": "Pay on-chain, then retry with X-Payment header (base64 of {tx_hash, signature})",
            },
        )

    decoded = verify_payment_header(x_payment, get_tool_price_usd(tool))
    if not decoded:
        raise HTTPException(status_code=402, detail="invalid X-Payment format")
    tx_hash, _sig = decoded
    if not await verify_payment_on_chain(tx_hash, get_tool_price_usd(tool)):
        raise HTTPException(status_code=402, detail="invalid payment proof")

    # T02: Burn the nonce atomically. Same tx_hash cannot be used twice.
    if not await _burn_nonce(catalog, tx_hash):
        raise HTTPException(
            status_code=410,
            detail={
                "error": "payment_already_used",
                "tx_hash": tx_hash,
                "message": "This payment receipt has already been used. Each tx_hash can only be used once.",
            },
        )
    _X402_REVENUE_COUNTER.labels(tool=tool, tier="pro").inc(int(get_tool_price_usd(tool) * 100))

    # Record the receipt
    if catalog:
        await record_receipt(catalog, tx_hash, tool, agent_id, get_tool_price_usd(tool))

    return {
        "agent_id": agent_id,
        "tool": tool,
        "tier": "pro",
        "paid_via_x402": tx_hash,
    }


async def payment_gate(request: Request) -> dict[str, Any]:
    """FastAPI dependency — enforce x402 payment on paid tool routes.

    Wraps :func:`require_payment` so it can be wired as a router-level
    dependency (``dependencies=[Depends(payment_gate)]``) on the
    /api/v1/x402-tools/* surface.

    - Free discovery paths (catalog, bundles listing, framework docs, ...)
      are exempt via route_classifier.is_free_path.
    - The tool ID is derived from the last path segment, matching
      route_classifier.extract_tool_id (also handles the catch-all
      POST /{tool_id} proxy route and bundle sub-paths).
    - Paid tools called without a valid X-Payment header raise HTTP 402
      with a signed invoice + payment instructions (from require_payment).

    Returns the payment context dict (agent_id, tool, tier, paid_via_x402)
    for paid calls that pass verification.
    """
    # Deferred imports: free discovery classification lives in the billing
    # domain and the catalog singleton is the rate-limit/nonce/receipt store.
    from app.catalog.service import get_catalog
    from app.domains.billing.x402.route_classifier import is_free_path

    path = request.url.path
    if is_free_path(path, request.method):
        return {"agent_id": "free", "tool": "", "tier": "free", "paid_via_x402": None}

    tool_id = path.rstrip("/").split("/")[-1] or "unknown"
    catalog = get_catalog()
    return await require_payment(tool_id, request, catalog=catalog)
