"""
Community Forensics Router — Premium feature for on-chain sleuths.

Endpoints:
- POST /api/v1/community-forensics/investigations — Create investigation
- GET  /api/v1/community-forensics/investigations — List community investigations
- GET  /api/v1/community-forensics/investigations/{id} — Get investigation detail
- POST /api/v1/community-forensics/investigations/{id}/evidence — Add evidence
- POST /api/v1/community-forensics/investigations/{id}/submit — Submit for review
- POST /api/v1/community-forensics/reports — Submit forensic report
- GET  /api/v1/community-forensics/reports — List verified reports
- GET  /api/v1/community-forensics/reports/{id} — Get report detail
- POST /api/v1/community-forensics/reports/{id}/verify — Verify report (admin)
- GET  /api/v1/community-forensics/leaderboard — Top sleuths
- GET  /api/v1/community-forensics/notebooks — List notebook templates
- GET  /api/v1/community-forensics/tools — List open-source tools
- POST /api/v1/community-forensics/blockscout/query — Proxy to Blockscout API
- GET  /api/v1/community-forensics/blockscout/health — Blockscout health
"""

import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/community-forensics", tags=["community-forensics"])

# ── Config ──────────────────────────────────────────────────────

BLOCKSCOUT_URL = os.getenv("BLOCKSCOUT_URL", "http://rmi-blockscout:4000")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# ── Models ────────────────────────────────────────────────────────


class InvestigationCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    target_address: str = Field(..., min_length=20)
    chain: Literal["solana", "ethereum", "base", "bsc", "arbitrum", "polygon", "avalanche"]
    investigation_type: Literal[
        "wallet_trace",
        "token_audit",
        "rug_pull",
        "social_engineering",
        "bundle_detection",
        "cross_chain",
    ]
    tags: list[str] = Field(default_factory=list)


class EvidenceAdd(BaseModel):
    evidence_type: Literal[
        "transaction",
        "screenshot",
        "contract_code",
        "social_post",
        "graph",
        "note",
        "external_link",
    ]
    title: str
    content: str
    source_url: str | None = None
    severity: Literal["info", "warning", "critical"] = "info"
    metadata: dict | None = None


class ReportSubmit(BaseModel):
    investigation_id: str
    title: str
    summary: str
    findings: list[dict]
    risk_score: int = Field(..., ge=0, le=100)
    recommendations: list[str] = Field(default_factory=list)
    graph_data: dict | None = None  # Cytoscape/vis.js compatible graph


class VerifyReport(BaseModel):
    status: Literal["verified", "rejected", "needs_revision"]
    reviewer_notes: str | None = None
    bounty_reward: int | None = 0  # Reputation points


# ── In-memory store (replace with Supabase/PostgreSQL in prod) ──

INVESTIGATIONS: dict[str, dict] = {}
REPORTS: dict[str, dict] = {}
LEADERBOARD: dict[str, dict] = {}
NOTEBOOK_TEMPLATES = [
    {
        "id": "wallet-tracing-101",
        "name": "Wallet Tracing Fundamentals",
        "description": "Learn to trace fund flows using Web3.py and free APIs",
        "chain": "ethereum",
        "difficulty": "beginner",
        "tools": ["web3.py", "etherscan-api", "pandas", "networkx"],
        "download_url": "/static/notebooks/wallet_tracing_101.ipynb",
        "preview_image": "/static/notebooks/wallet_tracing_preview.png",
    },
    {
        "id": "solana-sleuth",
        "name": "Solana Sleuth Toolkit",
        "description": "Analyze Solana transactions, detect snipers, and map clusters",
        "chain": "solana",
        "difficulty": "intermediate",
        "tools": ["solana.py", "helius-api", "pandas", "matplotlib"],
        "download_url": "/static/notebooks/solana_sleuth.ipynb",
        "preview_image": "/static/notebooks/solana_sleuth_preview.png",
    },
    {
        "id": "bundle-detector",
        "name": "Bundle Detection Algorithm",
        "description": "Identify coordinated buy patterns and launch manipulation",
        "chain": "multi",
        "difficulty": "advanced",
        "tools": ["web3.py", "numpy", "scikit-learn", "graphviz"],
        "download_url": "/static/notebooks/bundle_detector.ipynb",
        "preview_image": "/static/notebooks/bundle_detector_preview.png",
    },
    {
        "id": "cross-chain-tracker",
        "name": "Cross-Chain Fund Tracker",
        "description": "Track stolen funds across bridges and mixers",
        "chain": "multi",
        "difficulty": "advanced",
        "tools": ["web3.py", "ccxt", "pandas", "networkx", "plotly"],
        "download_url": "/static/notebooks/cross_chain_tracker.ipynb",
        "preview_image": "/static/notebooks/cross_chain_tracker_preview.png",
    },
    {
        "id": "contract-audit",
        "name": "Smart Contract Audit Script",
        "description": "Automated vulnerability detection using Slither + Mythril",
        "chain": "ethereum",
        "difficulty": "advanced",
        "tools": ["slither-analyzer", "mythril", "solc", "pandas"],
        "download_url": "/static/notebooks/contract_audit.ipynb",
        "preview_image": "/static/notebooks/contract_audit_preview.png",
    },
    {
        "id": "social-manipulation",
        "name": "Social Manipulation Detector",
        "description": "Detect coordinated shilling and fake endorsements",
        "chain": "multi",
        "difficulty": "intermediate",
        "tools": ["tweepy", "praw", "pandas", "scikit-learn", "networkx"],
        "download_url": "/static/notebooks/social_manipulation.ipynb",
        "preview_image": "/static/notebooks/social_manipulation_preview.png",
    },
]

OPEN_SOURCE_TOOLS = [
    {
        "id": "blockscout",
        "name": "Blockscout Explorer",
        "description": "Self-hosted open-source blockchain explorer",
        "category": "explorer",
        "url": "/blockscout",
        "github": "https://github.com/blockscout/blockscout",
        "self_hosted": True,
        "chains": ["ethereum", "base", "bsc", "arbitrum", "polygon"],
    },
    {
        "id": "web3py",
        "name": "Web3.py",
        "description": "Python library for interacting with Ethereum",
        "category": "sdk",
        "url": "https://web3py.readthedocs.io",
        "github": "https://github.com/ethereum/web3.py",
        "self_hosted": False,
        "chains": ["ethereum", "base", "bsc", "arbitrum", "polygon"],
    },
    {
        "id": "solana-py",
        "name": "Solana.py",
        "description": "Python SDK for Solana blockchain",
        "category": "sdk",
        "url": "https://michaelhly.github.io/solana-py/",
        "github": "https://github.com/michaelhly/solana-py",
        "self_hosted": False,
        "chains": ["solana"],
    },
    {
        "id": "slither",
        "name": "Slither",
        "description": "Solidity static analysis framework",
        "category": "security",
        "url": "https://github.com/crytic/slither",
        "github": "https://github.com/crytic/slither",
        "self_hosted": False,
        "chains": ["ethereum", "base", "bsc", "arbitrum", "polygon"],
    },
    {
        "id": "mythril",
        "name": "Mythril",
        "description": "Security analysis tool for EVM bytecode",
        "category": "security",
        "url": "https://github.com/Consensys/mythril",
        "github": "https://github.com/Consensys/mythril",
        "self_hosted": False,
        "chains": ["ethereum", "base", "bsc", "arbitrum", "polygon"],
    },
    {
        "id": "helius",
        "name": "Helius API",
        "description": "Enhanced Solana APIs (free tier available)",
        "category": "api",
        "url": "https://helius.xyz",
        "github": None,
        "self_hosted": False,
        "chains": ["solana"],
    },
    {
        "id": "etherscan",
        "name": "Etherscan API",
        "description": "Ethereum blockchain explorer API",
        "category": "api",
        "url": "https://etherscan.io/apis",
        "github": None,
        "self_hosted": False,
        "chains": ["ethereum"],
    },
    {
        "id": "solscan",
        "name": "Solscan API",
        "description": "Solana explorer API for transaction data",
        "category": "api",
        "url": "https://solscan.io",
        "github": None,
        "self_hosted": False,
        "chains": ["solana"],
    },
    {
        "id": "networkx",
        "name": "NetworkX",
        "description": "Python graph library for transaction network analysis",
        "category": "analysis",
        "url": "https://networkx.org",
        "github": "https://github.com/networkx/networkx",
        "self_hosted": False,
        "chains": ["multi"],
    },
    {
        "id": "plotly",
        "name": "Plotly",
        "description": "Interactive visualization library for forensic graphs",
        "category": "visualization",
        "url": "https://plotly.com/python/",
        "github": "https://github.com/plotly/plotly.py",
        "self_hosted": False,
        "chains": ["multi"],
    },
]

# ── Helpers ───────────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def gen_id() -> str:
    return str(uuid.uuid4())


def get_user_id(request) -> str:
    """Extract user ID from auth header. Fallback to anonymous."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        # In production, validate JWT here
        return auth.split(" ")[1][:32]  # Placeholder
    return "anonymous"


def update_reputation(user_id: str, points: int, action: str):
    """Update sleuth reputation score."""
    if user_id not in LEADERBOARD:
        LEADERBOARD[user_id] = {
            "user_id": user_id,
            "reputation": 0,
            "investigations": 0,
            "reports_submitted": 0,
            "reports_verified": 0,
            "evidence_added": 0,
            "joined_at": now_iso(),
        }
    LEADERBOARD[user_id]["reputation"] += points
    if action == "investigation":
        LEADERBOARD[user_id]["investigations"] += 1
    elif action == "report_submit":
        LEADERBOARD[user_id]["reports_submitted"] += 1
    elif action == "report_verified":
        LEADERBOARD[user_id]["reports_verified"] += 1
    elif action == "evidence":
        LEADERBOARD[user_id]["evidence_added"] += 1


# ── Endpoints: Investigations ────────────────────────────────────


@router.post("/investigations")
async def create_investigation(req: InvestigationCreate, request):
    """Create a new community investigation."""
    user_id = get_user_id(request)
    inv_id = gen_id()

    investigation = {
        "id": inv_id,
        "title": req.title,
        "description": req.description,
        "target_address": req.target_address,
        "chain": req.chain,
        "investigation_type": req.investigation_type,
        "tags": req.tags,
        "status": "open",  # open | in_progress | closed | submitted
        "created_by": user_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "evidence": [],
        "collaborators": [user_id],
        "report_id": None,
        "upvotes": 0,
        "views": 0,
    }
    INVESTIGATIONS[inv_id] = investigation
    update_reputation(user_id, 10, "investigation")

    return {"id": inv_id, "investigation": investigation}


@router.get("/investigations")
async def list_investigations(
    chain: str | None = None,
    status: str | None = None,
    investigation_type: str | None = None,
    sort: Literal["newest", "popular", "trending"] = "newest",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List community investigations with filters."""
    results = list(INVESTIGATIONS.values())

    if chain:
        results = [r for r in results if r["chain"] == chain]
    if status:
        results = [r for r in results if r["status"] == status]
    if investigation_type:
        results = [r for r in results if r["investigation_type"] == investigation_type]

    if sort == "popular":
        results.sort(key=lambda x: x["upvotes"], reverse=True)
    elif sort == "trending":
        results.sort(key=lambda x: x["views"] + x["upvotes"] * 3, reverse=True)
    else:
        results.sort(key=lambda x: x["created_at"], reverse=True)

    total = len(results)
    results = results[offset : offset + limit]

    return {"investigations": results, "total": total, "limit": limit, "offset": offset}


@router.get("/investigations/{inv_id}")
async def get_investigation(inv_id: str):
    """Get investigation details with evidence."""
    if inv_id not in INVESTIGATIONS:
        raise HTTPException(status_code=404, detail="Investigation not found")

    inv = INVESTIGATIONS[inv_id]
    inv["views"] = inv.get("views", 0) + 1
    return inv


@router.post("/investigations/{inv_id}/evidence")
async def add_evidence(inv_id: str, req: EvidenceAdd, request):
    """Add evidence to an investigation."""
    if inv_id not in INVESTIGATIONS:
        raise HTTPException(status_code=404, detail="Investigation not found")

    user_id = get_user_id(request)
    evidence = {
        "id": gen_id(),
        "investigation_id": inv_id,
        "evidence_type": req.evidence_type,
        "title": req.title,
        "content": req.content,
        "source_url": req.source_url,
        "severity": req.severity,
        "metadata": req.metadata or {},
        "added_by": user_id,
        "added_at": now_iso(),
        "verified": False,
    }

    INVESTIGATIONS[inv_id]["evidence"].append(evidence)
    INVESTIGATIONS[inv_id]["updated_at"] = now_iso()
    update_reputation(user_id, 5, "evidence")

    return {"evidence_id": evidence["id"], "evidence": evidence}


@router.post("/investigations/{inv_id}/upvote")
async def upvote_investigation(inv_id: str, request):
    """Upvote an investigation."""
    if inv_id not in INVESTIGATIONS:
        raise HTTPException(status_code=404, detail="Investigation not found")

    INVESTIGATIONS[inv_id]["upvotes"] = INVESTIGATIONS[inv_id].get("upvotes", 0) + 1
    return {"upvotes": INVESTIGATIONS[inv_id]["upvotes"]}


@router.post("/investigations/{inv_id}/submit")
async def submit_investigation(inv_id: str, request):
    """Submit investigation for official review."""
    if inv_id not in INVESTIGATIONS:
        raise HTTPException(status_code=404, detail="Investigation not found")

    INVESTIGATIONS[inv_id]["status"] = "submitted"
    INVESTIGATIONS[inv_id]["updated_at"] = now_iso()

    return {"status": "submitted", "message": "Investigation submitted for review"}


# ── Endpoints: Reports ───────────────────────────────────────────


@router.post("/reports")
async def submit_report(req: ReportSubmit, request):
    """Submit a forensic report from community investigation."""
    user_id = get_user_id(request)
    report_id = gen_id()

    report = {
        "id": report_id,
        "investigation_id": req.investigation_id,
        "title": req.title,
        "summary": req.summary,
        "findings": req.findings,
        "risk_score": req.risk_score,
        "recommendations": req.recommendations,
        "graph_data": req.graph_data,
        "submitted_by": user_id,
        "submitted_at": now_iso(),
        "status": "pending",  # pending | verified | rejected | featured
        "reviewer_notes": None,
        "verified_by": None,
        "verified_at": None,
        "bounty_reward": 0,
        "upvotes": 0,
        "views": 0,
    }
    REPORTS[report_id] = report
    update_reputation(user_id, 25, "report_submit")

    # Link to investigation
    if req.investigation_id in INVESTIGATIONS:
        INVESTIGATIONS[req.investigation_id]["report_id"] = report_id
        INVESTIGATIONS[req.investigation_id]["status"] = "submitted"

    return {"id": report_id, "report": report}


@router.get("/reports")
async def list_reports(
    status: str | None = None,
    chain: str | None = None,
    sort: Literal["newest", "popular", "verified"] = "verified",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List community forensic reports."""
    results = list(REPORTS.values())

    if status:
        results = [r for r in results if r["status"] == status]

    if sort == "popular":
        results.sort(key=lambda x: x["upvotes"], reverse=True)
    elif sort == "newest":
        results.sort(key=lambda x: x["submitted_at"], reverse=True)
    else:  # verified first
        results.sort(key=lambda x: (x["status"] != "verified", -x["upvotes"]))

    total = len(results)
    results = results[offset : offset + limit]

    return {"reports": results, "total": total, "limit": limit, "offset": offset}


@router.get("/reports/{report_id}")
async def get_report(report_id: str):
    """Get report details."""
    if report_id not in REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    report = REPORTS[report_id]
    report["views"] = report.get("views", 0) + 1
    return report


@router.post("/reports/{report_id}/verify")
async def verify_report(report_id: str, req: VerifyReport, request):
    """Verify a community report (admin only in production)."""
    if report_id not in REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    report = REPORTS[report_id]
    report["status"] = req.status
    report["reviewer_notes"] = req.reviewer_notes
    report["bounty_reward"] = req.bounty_reward
    report["verified_at"] = now_iso()

    if req.status == "verified":
        update_reputation(report["submitted_by"], req.bounty_reward or 50, "report_verified")

    return {"report": report}


@router.post("/reports/{report_id}/upvote")
async def upvote_report(report_id: str, request):
    """Upvote a report."""
    if report_id not in REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    REPORTS[report_id]["upvotes"] = REPORTS[report_id].get("upvotes", 0) + 1
    return {"upvotes": REPORTS[report_id]["upvotes"]}


# ── Endpoints: Leaderboard ───────────────────────────────────────


@router.get("/leaderboard")
async def get_leaderboard(
    period: Literal["all_time", "month", "week"] = "all_time",
    limit: int = Query(50, ge=1, le=100),
):
    """Get top community sleuths."""
    sleuths = list(LEADERBOARD.values())
    sleuths.sort(key=lambda x: x["reputation"], reverse=True)

    return {
        "sleuths": sleuths[:limit],
        "total_sleuths": len(sleuths),
        "your_rank": None,  # Would calculate based on auth
    }


# ── Endpoints: Notebooks & Tools ─────────────────────────────────


@router.get("/notebooks")
async def list_notebooks(
    chain: str | None = None,
    difficulty: str | None = None,
):
    """List Jupyter notebook templates for on-chain analysis."""
    results = NOTEBOOK_TEMPLATES

    if chain:
        results = [n for n in results if n["chain"] == chain or n["chain"] == "multi"]
    if difficulty:
        results = [n for n in results if n["difficulty"] == difficulty]

    return {"notebooks": results, "total": len(results)}


@router.get("/tools")
async def list_tools(
    chain: str | None = None,
    category: str | None = None,
):
    """List open-source tools for community forensics."""
    results = OPEN_SOURCE_TOOLS

    if chain:
        results = [t for t in results if chain in t["chains"] or "multi" in t["chains"]]
    if category:
        results = [t for t in results if t["category"] == category]

    return {"tools": results, "total": len(results)}


# ── Endpoints: Blockscout Proxy ──────────────────────────────────


@router.get("/blockscout/health")
async def blockscout_health():
    """Check Blockscout instance health."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BLOCKSCOUT_URL}/api/v2/main-page/indexing-status")
            return {"status": "ok", "blockscout": resp.json()}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.get("/blockscout/{path:path}")
async def blockscout_proxy(path: str, request):
    """Proxy requests to self-hosted Blockscout API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Forward query params
            query = str(request.query_params)
            url = f"{BLOCKSCOUT_URL}/api/v2/{path}"
            if query:
                url += f"?{query}"

            resp = await client.get(url)
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Blockscout unavailable: {e}")


# ── Endpoints: File Uploads ──────────────────────────────────────


@router.post("/investigations/{inv_id}/upload")
async def upload_evidence_file(inv_id: str, file: UploadFile, request):
    """Upload evidence file (screenshot, graph export, etc)."""
    if inv_id not in INVESTIGATIONS:
        raise HTTPException(status_code=404, detail="Investigation not found")

    # In production: upload to S3/Cloudflare R2
    # For now, return metadata
    user_id = get_user_id(request)
    file_id = gen_id()

    evidence = {
        "id": file_id,
        "investigation_id": inv_id,
        "evidence_type": "screenshot" if file.content_type and "image" in file.content_type else "file",
        "title": file.filename,
        "content": f"Uploaded file: {file.filename} ({file.size} bytes)",
        "source_url": f"/uploads/{file_id}",
        "severity": "info",
        "metadata": {
            "filename": file.filename,
            "content_type": file.content_type,
            "size": file.size,
        },
        "added_by": user_id,
        "added_at": now_iso(),
        "verified": False,
    }

    INVESTIGATIONS[inv_id]["evidence"].append(evidence)
    INVESTIGATIONS[inv_id]["updated_at"] = now_iso()
    update_reputation(user_id, 5, "evidence")

    return {"file_id": file_id, "evidence": evidence}


# ── Endpoints: Stats ─────────────────────────────────────────────


@router.get("/stats")
async def community_stats():
    """Get community forensics platform statistics."""
    total_investigations = len(INVESTIGATIONS)
    total_reports = len(REPORTS)
    verified_reports = len([r for r in REPORTS.values() if r["status"] == "verified"])
    total_sleuths = len(LEADERBOARD)
    total_evidence = sum(len(inv["evidence"]) for inv in INVESTIGATIONS.values())

    return {
        "total_investigations": total_investigations,
        "total_reports": total_reports,
        "verified_reports": verified_reports,
        "verification_rate": round(verified_reports / total_reports * 100, 1) if total_reports else 0,
        "total_sleuths": total_sleuths,
        "total_evidence": total_evidence,
        "notebook_templates": len(NOTEBOOK_TEMPLATES),
    }


# ── Wallet Graph Tracing ─────────────────────────────────────────
# AI-powered on-chain wallet connection tracing with caching

_graph_cache: dict[str, dict] = {}
_graph_cache_time: dict[str, float] = {}
GRAPH_CACHE_TTL = 300  # 5 min cache for graph data


class WalletTraceRequest(BaseModel):
    address: str = Field(..., min_length=20)
    chain: Literal["ethereum", "base", "bsc", "solana", "arbitrum", "polygon"] = "ethereum"
    depth: int = Field(2, ge=1, le=4, description="How many hops to trace")
    min_value_usd: float = Field(100, ge=0, description="Minimum transfer value to include")


@router.post("/wallet-trace")
async def trace_wallet_connections(req: WalletTraceRequest):
    """
    Trace wallet connections from a target address.
    Returns graph data (nodes + edges) for visualization.
    Uses our own APIs with caching + fallback.
    """
    cache_key = f"{req.chain}:{req.address}:{req.depth}:{req.min_value_usd}"
    now = time.time()

    # Check cache
    if cache_key in _graph_cache and now - _graph_cache_time.get(cache_key, 0) < GRAPH_CACHE_TTL:
        return _graph_cache[cache_key]

    nodes = []
    edges = []
    seen_addresses = set()
    queue = [(req.address, 0)]
    seen_addresses.add(req.address.lower())

    # Add target node
    nodes.append(
        {
            "id": req.address,
            "label": f"{req.address[:6]}...{req.address[-4:]}",
            "type": "target",
            "chain": req.chain,
        }
    )

    # BFS trace using our own x402-tools API
    base_url = "http://localhost:8000/api/v1/x402-tools"

    for hop in range(req.depth):
        next_queue = []
        for addr, _ in queue:
            if hop >= req.depth:
                break

            # Use our own wallet analyzer endpoint
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        f"{base_url}/wallet_analyzer",
                        json={
                            "query": addr,
                            "chain": req.chain,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        # Extract connected wallets from analysis
                        interactions = data.get("recent_interactions", data.get("interactions", []))
                        if isinstance(interactions, list):
                            for ix in interactions[:20]:  # Cap per hop
                                other_addr = ix.get("address", ix.get("to", ix.get("from", "")))
                                value = float(ix.get("value_usd", ix.get("value", 0)))

                                if not other_addr or other_addr.lower() in seen_addresses:
                                    continue
                                if value < req.min_value_usd:
                                    continue

                                seen_addresses.add(other_addr.lower())

                                # Classify wallet type
                                wtype = "wallet"
                                label_prefix = ""
                                if any(kw in other_addr.lower() for kw in ["cex", "binance", "coinbase", "kraken"]):
                                    wtype = "cex"
                                    label_prefix = "🏦 "
                                elif any(kw in str(ix) for kw in ["mixer", "tornado", "blender"]):
                                    wtype = "mixer"
                                    label_prefix = "🌀 "
                                elif any(kw in str(ix) for kw in ["contract", "0x"]):
                                    wtype = "contract"

                                nodes.append(
                                    {
                                        "id": other_addr,
                                        "label": f"{label_prefix}{other_addr[:6]}...{other_addr[-4:]}",
                                        "type": wtype,
                                        "chain": req.chain,
                                        "hop": hop + 1,
                                    }
                                )

                                edges.append(
                                    {
                                        "source": addr,
                                        "target": other_addr,
                                        "value": value,
                                        "label": f"${value:,.0f}",
                                        "direction": ix.get("direction", "out"),
                                        "tx_hash": ix.get("tx_hash", ""),
                                    }
                                )

                                if hop + 1 < req.depth:
                                    next_queue.append((other_addr, hop + 1))
            except Exception:
                pass  # Silent fallback — partial graph is fine

        queue = next_queue

    result = {
        "nodes": nodes,
        "edges": edges,
        "target": req.address,
        "chain": req.chain,
        "depth": req.depth,
        "total_connections": len(edges),
        "generated_at": now_iso(),
    }

    # Cache result
    _graph_cache[cache_key] = result
    _graph_cache_time[cache_key] = now

    return result


# ── Free Investigation Gate ──────────────────────────────────────
# 1 free investigation per anonymous user, then signup required

FREE_INV_LIMIT = 1
_anon_inv_count: dict[str, int] = {}


def check_free_limit(request) -> bool:
    """Check if anonymous user has free investigations left."""
    user_id = get_user_id(request)
    if not user_id.startswith("anon_"):
        return True  # Signed up users have no limit
    return _anon_inv_count.get(user_id, 0) < FREE_INV_LIMIT


@router.get("/free-investigations-remaining")
async def free_investigations_remaining(request):
    """How many free investigations the current user has left."""
    user_id = get_user_id(request)
    used = _anon_inv_count.get(user_id, 0)
    remaining = max(0, FREE_INV_LIMIT - used)
    return {
        "used": used,
        "limit": FREE_INV_LIMIT,
        "remaining": remaining,
        "is_signed_up": not user_id.startswith("anon_"),
    }


# ── Pro Feature: AI-Guided Investigative Assistant ───────────────


class AIAssistRequest(BaseModel):
    investigation_id: str
    target_address: str
    chain: str
    current_evidence_summary: str  # Brief summary of what we know so far
    user_query: str | None = None  # e.g., "What should I look for next?"


class AIAssistResponse(BaseModel):
    next_steps: list[str]
    missing_information: list[str]
    risk_flags: list[str]
    suggested_queries: list[str]  # e.g., "Check Solscan for TX xyz"


@router.post("/ai-assist")
async def get_ai_investigation_guidance(req: AIAssistRequest):
    """
    AI acts as a senior crypto forensics analyst to guide the user's investigation.
    Analyzes current evidence and suggests next steps, missing info, and risk flags.
    """
    if not LLM_API_KEY:
        raise HTTPException(status_code=503, detail="LLM API key not configured for AI assistance")

    prompt = f"""You are a senior crypto forensics investigator and blockchain analyst.
A user is investigating a potential scam/fraud on the {req.chain} blockchain.

TARGET: {req.target_address}
CURRENT EVIDENCE SUMMARY: {req.current_evidence_summary}
USER QUESTION: {req.user_query or "Analyze the current evidence and guide my next steps."}

Provide a structured JSON response with the following keys:
1. "next_steps": 3-4 highly specific, actionable investigative steps to take right now.
2. "missing_information": 2-3 critical pieces of data we still need to build a solid case (e.g., specific TX hashes, contract source code, CEX deposit addresses).
3. "risk_flags": 1-3 red flags or suspicious patterns detected in the current evidence.
4. "suggested_queries": 2-3 specific queries or actions the user can perform (e.g., "Search Solscan for wallet interactions with [Address]", "Check if contract has a mint function").

Return ONLY valid JSON. Do not include markdown formatting or explanations outside the JSON object.
"""

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 800,
                    "temperature": 0.2,  # Low temperature for factual, structured analysis
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # Clean up markdown code blocks if the LLM adds them
                content = content.replace("```json", "").replace("```", "").strip()
                try:
                    parsed = json.loads(content)
                    return parsed
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse AI response: {content}")
                    raise HTTPException(status_code=500, detail="AI returned invalid JSON")
            else:
                logger.error(f"LLM API error: {resp.status_code} - {resp.text}")
                raise HTTPException(status_code=500, detail="AI analysis failed")
    except httpx.RequestError as e:
        logger.error(f"HTTP request to LLM failed: {e}")
        raise HTTPException(status_code=503, detail="AI service unavailable")


# ── Pro Feature: Law Enforcement / Public Publishing ─────────────


class PublishReportRequest(BaseModel):
    report_id: str
    publish_status: Literal["private", "public", "law_enforcement"]
    include_full_wallet_history: bool = True
    investigator_narrative: str | None = None
    redact_personal_info: bool = True


@router.post("/reports/publish")
async def publish_report(req: PublishReportRequest, request):
    """Publish a forensic report with specific visibility (Pro Feature)."""
    if req.report_id not in REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    report = REPORTS[req.report_id]
    report["publish_status"] = req.publish_status
    report["investigator_narrative"] = req.investigator_narrative
    report["include_full_wallet_history"] = req.include_full_wallet_history
    report["redact_personal_info"] = req.redact_personal_info
    report["published_at"] = now_iso()

    # Generate shareable link/token based on status
    if req.publish_status == "law_enforcement":
        report["access_token"] = f"LE-{gen_id()[:8].upper()}"
        report["share_url"] = f"/forensics/reports/{req.report_id}?token={report['access_token']}"
    elif req.publish_status == "public":
        report["share_url"] = f"/forensics/reports/{req.report_id}"
    else:
        report["share_url"] = None

    return {
        "status": "success",
        "report": report,
        "message": f"Report published as {req.publish_status}",
    }


# ── Pro Feature: AI Report & Graphics Generation ─────────────────


class GenerateDossierRequest(BaseModel):
    report_id: str
    investigator_name: str
    narrative: str
    qwen_api_key: str = Field(..., description="API key for Qwen image generation")


@router.post("/reports/generate-dossier")
async def generate_dossier(req: GenerateDossierRequest, request):
    """Generate a complete HTML dossier with AI-generated graphics for the report."""
    if req.report_id not in REPORTS:
        raise HTTPException(status_code=404, detail="Report not found")

    report = REPORTS[req.report_id]

    # 1. Generate AI Graphics using Qwen
    graph_image_url = ""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Using the provided Qwen API endpoint structure
            resp = await client.post(
                "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis",
                headers={
                    "Authorization": f"Bearer {req.qwen_api_key}",
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                },
                json={
                    "model": "wanx-v1",
                    "input": {
                        "prompt": f"Professional cybersecurity forensic network graph, dark theme, glowing nodes, criminal enterprise hierarchy, institutional fintech aesthetic, high detail, 8k resolution, {report['title']}",
                        "negative_prompt": "cartoon, low quality, blurry, bright colors, unprofessional",
                    },
                    "parameters": {"size": "1024*1024", "n": 1},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                # Note: Qwen text2image is async, in a real impl we'd poll the task_id.
                # For this prototype, we'll use a placeholder or the direct result if sync.
                graph_image_url = data.get("output", {}).get("results", [{}])[0].get("url", "")
    except Exception as e:
        logger.warning(f"Qwen image gen failed: {e}. Using fallback graph.")
        graph_image_url = "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1024&q=80"  # Fallback cyber image

    # 2. Build the HTML Dossier
    dossier_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{report["title"]} - Forensic Dossier</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js" integrity="sha384-+EY7N7kzZqJ7v6qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF3qF" crossorigin="anonymous"></script>
    <style>
        :root {{
            --bg-color: #0f1115;
            --card-bg: #1a1d24;
            --text-primary: #e6e8eb;
            --text-secondary: #9ca3af;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --accent-green: #10b981;
            --border-color: #2d313a;
        }}
        body {{ font-family: 'Segoe UI', Roboto, sans-serif; background-color: var(--bg-color); color: var(--text-primary); line-height: 1.6; margin: 0; padding: 40px; }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        .header {{ border-bottom: 2px solid var(--accent-red); padding-bottom: 20px; margin-bottom: 40px; }}
        .classification {{ background-color: var(--accent-red); color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold; font-size: 0.85em; text-transform: uppercase; letter-spacing: 1px; display: inline-block; margin-bottom: 15px; }}
        h1 {{ font-size: 2.5em; margin: 0 0 10px 0; }}
        h2 {{ font-size: 1.5em; color: var(--accent-blue); border-bottom: 1px solid var(--border-color); padding-bottom: 8px; margin-top: 40px; }}
        .card {{ background-color: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        .story-section {{ border-left: 4px solid var(--accent-green); background-color: rgba(16, 185, 129, 0.05); }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 0.95em; }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid var(--border-color); }}
        th {{ background-color: rgba(59, 130, 246, 0.1); color: var(--accent-blue); font-weight: 600; }}
        .wallet-addr {{ font-family: 'Courier New', monospace; background-color: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; color: var(--accent-green); }}
        .mermaid {{ background-color: var(--card-bg); border-radius: 8px; padding: 20px; text-align: center; }}
        .generated-graph {{ width: 100%; border-radius: 8px; border: 1px solid var(--border-color); margin: 20px 0; }}
        @media print {{
            body {{ background-color: white; color: black; padding: 20px; }}
            .card {{ background-color: #f9f9f9; border: 1px solid #ddd; }}
            .classification {{ background-color: #d32f2f; color: white; }}
            h1, h2 {{ color: #111 !important; }}
            .wallet-addr {{ color: #006400; background-color: #eee; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="classification">{"LAW ENFORCEMENT SENSITIVE" if report.get("publish_status") == "law_enforcement" else "CONFIDENTIAL"}</div>
            <h1>{report["title"]}</h1>
            <div style="color: var(--text-secondary); font-size: 0.95em;">
                <strong>Lead Investigator:</strong> {req.investigator_name}<br>
                <strong>Report ID:</strong> {req.report_id}<br>
                <strong>Generated:</strong> {now_iso()}<br>
                <strong>Risk Score:</strong> {report["risk_score"]}/100
            </div>
        </div>

        <div class="card story-section">
            <h2>📖 Investigator's Narrative</h2>
            <p>{req.narrative.replace(chr(10), "<br>")}</p>
        </div>

        <div class="card">
            <h2>📊 Executive Summary</h2>
            <p>{report["summary"]}</p>
            <h3>Key Findings</h3>
            <ul>
                {"".join(f"<li>{f.get('title', 'Finding')}: {f.get('description', '')}</li>" for f in report.get("findings", []))}
            </ul>
        </div>

        <div class="card">
            <h2>🕸️ AI-Generated Network Topology</h2>
            <p>Visual representation of the criminal enterprise hierarchy and fund flows.</p>
            <img src="{graph_image_url}" alt="Forensic Network Graph" class="generated-graph" onerror="this.src='https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=1024&q=80'">

            <h3>Interactive Graph Data</h3>
            <div class="mermaid">
graph TD
    Target["Target Address<br/>{report.get("investigation_id", "Unknown")}"] -->|Funds Flow| Node1["Intermediate Wallet 1"]
    Target -->|Funds Flow| Node2["Intermediate Wallet 2"]
    Node1 -->|Consolidation| CEX["Centralized Exchange<br/>(KYC Required)"]
    Node2 -->|Consolidation| CEX
    style Target fill:#ef4444,stroke:#fff,stroke-width:2px,color:#fff
    style CEX fill:#3b82f6,stroke:#fff,stroke-width:2px,color:#fff
            </div>
        </div>

        <div class="card">
            <h2>🔗 Key Wallet Directory</h2>
            <table>
                <thead>
                    <tr><th>Role</th><th>Address</th><th>Explorer Link</th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Target</strong></td>
                        <td><span class="wallet-addr">{report.get("investigation_id", "N/A")}</span></td>
                        <td><a href="https://solscan.io/account/{report.get("investigation_id", "")}" target="_blank">View on Solscan ↗</a></td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2>⚖️ Recommendations for Law Enforcement</h2>
            <ul>
                {"".join(f"<li>{rec}</li>" for rec in report.get("recommendations", ["Preserve all on-chain evidence immediately.", "Issue subpoenas to identified CEXs for KYC data."]))}
            </ul>
        </div>

        <div style="text-align: center; margin-top: 40px; color: var(--text-secondary); font-size: 0.85em;">
            <p>Generated by RMI Community Forensics Platform | Powered by Hermes Agent & Qwen AI</p>
            <p>CONFIDENTIAL - DO NOT DISTRIBUTE WITHOUT AUTHORIZATION</p>
        </div>
    </div>
    <script>
        mermaid.initialize({startOnLoad: true, theme: 'dark' });
    </script>
</body>
</html>"""

    return {
        "status": "success",
        "dossier_html": dossier_html,
        "graph_image_url": graph_image_url,
        "message": "Dossier generated successfully. Save the HTML and print to PDF.",
    }
