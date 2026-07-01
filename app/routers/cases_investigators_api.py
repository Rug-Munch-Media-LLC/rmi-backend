"""
cases_investigators_api.py — Public case files + investigator profiles

Endpoints:
  POST   /api/v1/cases              — create/save a case snapshot
  GET    /api/v1/cases/:id          — fetch a case by id (public)
  GET    /api/v1/cases              — list cases by creator (auth)
  DELETE /api/v1/cases/:id          — delete a case (creator only)
  GET    /api/v1/investigators/:username — public investigator profile
  GET    /api/v1/portfolio/features — returns tier-gated portfolio limits for current user
  POST   /api/v1/subscription/addon — add an add-on to an existing subscription

Designed to work with localStorage fallback on the frontend (cases are
still public-shareable URLs even before this backend is wired).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases-investigators"])


# ── Storage ───────────────────────────────────────────────────
# Cases are stored in Redis if available, else in-memory dict.
# In production this should be Postgres/ClickHouse, but for the
# public-share URL contract, this gives full functionality end-to-end.

_cases_db: dict[str, dict] = {}
_investigators_db: dict[str, dict] = {}

try:
    import redis as _redis  # type: ignore

    _r = _redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=0,
        decode_responses=True,
        socket_timeout=2,
    )
    _r.ping()
    REDIS_OK = True
except Exception as e:
    REDIS_OK = False
    logger.warning(f"cases: redis unavailable, using in-memory store ({e})")


def _store_case(case_id: str, data: dict) -> None:
    if REDIS_OK:
        _r.setex(f"rmi:case:{case_id}", 60 * 60 * 24 * 365, json.dumps(data))
    _cases_db[case_id] = data


def _load_case(case_id: str) -> dict | None:
    if REDIS_OK:
        raw = _r.get(f"rmi:case:{case_id}")
        if raw:
            return json.loads(raw)
    return _cases_db.get(case_id)


def _delete_case(case_id: str) -> bool:
    deleted = False
    if REDIS_OK:
        deleted = bool(_r.delete(f"rmi:case:{case_id}"))
    if case_id in _cases_db:
        del _cases_db[case_id]
        deleted = True
    return deleted


def _store_investigator(handle: str, data: dict) -> None:
    if REDIS_OK:
        _r.setex(f"rmi:investigator:{handle}", 60 * 60 * 24, json.dumps(data))
    _investigators_db[handle] = data


def _load_investigator(handle: str) -> dict | None:
    if REDIS_OK:
        raw = _r.get(f"rmi:investigator:{handle}")
        if raw:
            return json.loads(raw)
    return _investigators_db.get(handle)


# ── Models ────────────────────────────────────────────────────


class CaseWallet(BaseModel):
    address: str
    label: str | None = None
    pct: float | None = None


class CaseCreateRequest(BaseModel):
    chain: str
    token_address: str
    title: str
    risk_score: float = Field(ge=0, le=100)
    risk_level: str = "medium"
    description: str | None = None
    key_finding: str | None = None
    findings: list[str] = []
    evidence_wallets: list[CaseWallet] = []
    tx_hashes: list[str] = []
    bubble_svg: str | None = None


class AddonRequest(BaseModel):
    addon: str = Field(..., pattern="^(PORTFOLIO_PRO)$")
    period: str = Field(..., pattern="^(monthly|six_month|yearly)$")
    payment_method: str = Field("x402", pattern="^(stripe|crypto|x402)$")
    crypto_chain: str | None = None


# ── Cases ─────────────────────────────────────────────────────


@router.post("/cases")
async def create_case(req: CaseCreateRequest, request: Request):
    """Create a public case file. Returns the case id + public URL."""
    # Resolve creator if authed
    creator = "anonymous"
    try:
        from app.auth import get_current_user

        user = await get_current_user(request)
        if user:
            creator = (
                user.get("user_metadata", {}).get("handle")
                or user.get("email", "").split("@")[0]
                or user.get("id", "anonymous")
            )
    except Exception:
        pass

    # Derive deterministic id from payload
    payload = {
        "chain": req.chain,
        "address": req.token_address,
        "risk_score": req.risk_score,
        "findings": req.findings,
        "wallets": [w.model_dump() for w in req.evidence_wallets],
        "txs": req.tx_hashes,
        "title": req.title,
    }
    payload_sorted = json.dumps(payload, sort_keys=True)
    full_hash = hashlib.sha256(payload_sorted.encode()).hexdigest()
    case_id = f"rmi-{full_hash[:12]}"

    full = {
        "id": case_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "creator": creator,
        "chain": req.chain,
        "token_address": req.token_address,
        "title": req.title,
        "description": req.description,
        "risk_score": req.risk_score,
        "risk_level": req.risk_level,
        "key_finding": req.key_finding,
        "findings": req.findings,
        "evidence_wallets": [w.model_dump() for w in req.evidence_wallets],
        "tx_hashes": req.tx_hashes,
        "bubble_svg": req.bubble_svg,
        "snapshot_hash": full_hash,
        "url": f"https://rugmunch.io/case/{case_id}",
    }
    _store_case(case_id, full)

    return {
        "success": True,
        "case": full,
        "public_url": full["url"],
    }


@router.get("/cases/{case_id}")
async def get_case(case_id: str):
    """Public, no auth required. Returns 404 if not found."""
    data = _load_case(case_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    return data


@router.get("/cases")
async def list_my_cases(request: Request, limit: int = 50):
    """List cases created by the current user. Auth required."""
    try:
        from app.auth import get_current_user

        user = await get_current_user(request)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    creator = user.get("user_metadata", {}).get("handle") or user.get("email", "").split("@")[0] or user.get("id")
    matches = [c for c in _cases_db.values() if c.get("creator") == creator]
    matches.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return {"cases": matches[:limit], "total": len(matches)}


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str, request: Request):
    """Delete a case. Only the creator (or anon if created anonymously) can delete."""
    data = _load_case(case_id)
    if not data:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        from app.auth import get_current_user

        user = await get_current_user(request)
    except Exception:
        user = None
    creator = None
    if user:
        creator = user.get("user_metadata", {}).get("handle") or user.get("email", "").split("@")[0] or user.get("id")
    if data.get("creator") != "anonymous" and data.get("creator") != creator:
        raise HTTPException(status_code=403, detail="Not your case")
    _delete_case(case_id)
    return {"success": True}


# ── Investigator profiles ─────────────────────────────────────


@router.get("/investigators/{username}")
async def get_investigator(username: str):
    """Public investigator profile. Returns synthesized profile if no record."""
    handle = username.lower()
    data = _load_investigator(handle)
    if data:
        return data
    # Synthesize a default profile so the /u/:username page always renders
    return {
        "username": handle,
        "display_name": handle.replace("_", " ").replace("-", " ").title() if handle != "anonymous" else "Anonymous",
        "bio": f"RMI investigator @{handle}",
        "joined_at": (datetime.utcnow() - timedelta(days=90)).isoformat() + "Z",
        "reputation": 0,
        "cases_filed": 0,
        "scans_run": 0,
        "accuracy": 0,
        "badges": [],
    }


@router.post("/investigators/{username}")
async def upsert_investigator(username: str, request: Request):
    """Update or create investigator profile. Auth required."""
    try:
        from app.auth import get_current_user

        user = await get_current_user(request)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    body = await request.json()
    handle = username.lower()
    data = {
        "username": handle,
        "display_name": body.get("display_name", handle),
        "bio": body.get("bio", ""),
        "avatar_url": body.get("avatar_url"),
        "twitter": body.get("twitter"),
        "github": body.get("github"),
        "website": body.get("website"),
        "telegram": body.get("telegram"),
        "reputation": body.get("reputation", 0),
        "cases_filed": body.get("cases_filed", 0),
        "scans_run": body.get("scans_run", 0),
        "accuracy": body.get("accuracy", 0),
        "badges": body.get("badges", []),
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    _store_investigator(handle, data)
    return {"success": True, "investigator": data}


# ── Portfolio features (tier-gated limits) ───────────────────

_FREE_LIMITS = {"wallets": 3, "history_days": 7, "csv_export": False, "alerts": False}
_PORTFOLIO_PRO_LIMITS = {"wallets": 50, "history_days": 30, "csv_export": True, "alerts": True}


@router.get("/portfolio/features")
async def portfolio_features(request: Request):
    """Return tier-gated Portfolio features for the current user.

    Resolves the user's effective tier by checking:
      1. Supabase auth → user metadata.has_portfolio_pro
      2. Their active subscription (if any)
      3. Falls back to FREE limits

    Frontend uses this to gate wallets, history window, and exports.
    """
    is_pro = False
    has_subscription = False
    base_tier = "FREE"

    try:
        from app.auth import get_current_user

        user = await get_current_user(request)
    except Exception:
        user = None

    if user:
        # Check user metadata flag
        if user.get("user_metadata", {}).get("has_portfolio_pro"):
            is_pro = True
        # Check subscription entitlements (Redis or DB)
        # Convention: rmi:sub:{user_id} → JSON with {tier, addons: [PORTFOLIO_PRO], ...}
        try:
            if REDIS_OK:
                sub_raw = _r.get(f"rmi:sub:{user.get('id')}")
                if sub_raw:
                    sub = json.loads(sub_raw)
                    base_tier = sub.get("tier", "FREE")
                    has_subscription = True
                    if "PORTFOLIO_PRO" in sub.get("addons", []):
                        is_pro = True
        except Exception:
            pass

    limits = _PORTFOLIO_PRO_LIMITS if is_pro else _FREE_LIMITS
    return {
        "tier": "PORTFOLIO_PRO" if is_pro else base_tier,
        "is_portfolio_pro": is_pro,
        "has_subscription": has_subscription,
        "limits": limits,
        "price_usd": 3.00,
        "price_atoms": "3000000",
        "x402_chain_id": 8453,
        "x402_pay_to": os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"),
        "upgrade_url": "/pricing?tier=PORTFOLIO_PRO",
    }


# ── Add-on subscription ──────────────────────────────────────


@router.post("/subscription/addon")
async def add_addon(req: AddonRequest, request: Request):
    """Add an add-on (e.g. PORTFOLIO_PRO) to an existing subscription.

    Returns an x402 challenge for micropayment, or a stripe checkout URL
    for fiat, or a crypto payment address.
    """
    from app.auth import get_current_user

    try:
        user = await get_current_user(request)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    addon = req.addon.upper()
    if addon == "PORTFOLIO_PRO":
        price_usd = 3.00
        x402_price_atoms = "3000000"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown addon {addon}")

    addon_id = secrets.token_hex(8)
    now = datetime.utcnow()

    if req.payment_method == "x402":
        # Return EIP-3009 challenge
        return {
            "success": True,
            "addon": addon,
            "addon_id": addon_id,
            "user_id": user["id"],
            "x402": {
                "version": 2,
                "scheme": "exact",
                "network": "eip155:8453",
                "asset": "USDC",
                "amount_atoms": x402_price_atoms,
                "amount_usd": price_usd,
                "pay_to": os.getenv("X402_PAY_TO", "0x1E3AC01d0fdb976179790BDD02823196A92705C9"),
                "expires_at": (now + timedelta(minutes=10)).isoformat() + "Z",
            },
            "instructions": f"Sign EIP-3009 transfer for ${price_usd} USDC. Addon activates on settlement.",
        }

    if req.payment_method == "crypto":
        return {
            "success": True,
            "addon": addon,
            "addon_id": addon_id,
            "user_id": user["id"],
            "payment_details": {
                "chain": req.crypto_chain,
                "amount_usd": price_usd,
                "memo": f"RMI-ADDON-{addon_id}",
            },
        }

    # Stripe
    return {
        "success": True,
        "addon": addon,
        "addon_id": addon_id,
        "user_id": user["id"],
        "checkout_url": f"/pricing?addon={addon}&period={req.period}",
    }


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "redis": REDIS_OK,
        "cases_stored": len(_cases_db),
        "investigators_stored": len(_investigators_db),
    }
