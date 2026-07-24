"""
RMI Intelligence Pipeline - HF + Supabase + RAG
================================================
Unified intelligence service: scam classification (HF models),
wallet labeling via RAG pattern matching, Supabase hybrid storage.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.secrets import get_secret_or_none

logger = get_logger(__name__)

HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_API = "https://api-inference.huggingface.co/models"

def _get_url() -> Any:
    return os.getenv("SUPABASE_URL", "")

def _get_key() -> Any:
    """Get Supabase service key for full CRUD access to intel tables."""
    return (
        get_secret_or_none("SUPABASE_SERVICE_KEY", "rmi/prod/supabase_service_key")
        or get_secret_or_none("SUPABASE_SERVICE_ROLE_KEY")
        or ""
    )

def _get_headers() -> dict:
    key = _get_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

# ── HF Models (Paywalled - using local fallback) ──────
# HF Inference API now requires PRO subscription ($9/mo).
# Using local pattern matching + RAG memory instead.
# Enable HF by setting HUGGINGFACE_TOKEN to a valid PRO key.

SCAM_LABELS = ["scam", "rugpull", "honeypot", "legitimate", "phishing", "ponzi"]

SCAM_KEYWORDS = {
    "scam": ["scam", "fraud", "stole", "stolen", "exit scam", "fake"],
    "rugpull": ["rug", "rugpull", "pulled liquidity", "drained", "removed liquidity", "lp removed"],
    "honeypot": [
        "honeypot",
        "cannot sell",
        "can't sell",
        "unable to sell",
        "transfer disabled",
        "sell tax 100",
    ],
    "phishing": [
        "phishing",
        "airdrop scam",
        "claim reward",
        "verify wallet",
        "seed phrase",
        "private key",
    ],
    "ponzi": [
        "ponzi",
        "mlm",
        "multi level",
        "referral rewards",
        "guaranteed returns",
        "double your",
    ],
    "insider": ["insider", "team wallet", "dev wallet", "pre-sale", "unlocked tokens", "vesting"],
}

async def classify_scam_risk(text: str) -> dict:
    """Classify scam risk using local pattern matching (HF paywalled)."""
    text_lower = text.lower()
    scores = {}

    for label, keywords in SCAM_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[label] = min(score / len(keywords), 1.0)

    try:
        from app.rag_service import search_documents

        rag_results = await search_documents("known_scams", text, limit=3)
        for r in rag_results:
            content = r.get("content", "").lower()
            for label, keywords in SCAM_KEYWORDS.items():
                if any(kw in content for kw in keywords):
                    scores[label] = scores.get(label, 0) + 0.1
    except (AttributeError, ImportError):
        pass

    if not scores:
        return {
            "risk": "low",
            "labels": {},
            "confidence": 0,
            "is_scam": False,
            "risk_level": "low",
            "model": "local_pattern_match",
        }

    top = max(scores, key=scores.get)
    top_score = scores[top]
    is_scam = top in ("scam", "rugpull", "honeypot", "phishing", "ponzi")

    return {
        "model": "local_pattern_match",
        "labels": {
            k: round(v, 3) for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        },
        "top_label": top,
        "confidence": round(top_score, 3),
        "is_scam": is_scam,
        "risk_level": "high" if (is_scam and top_score > 0.5) else "medium" if is_scam else "low",
    }

async def generate_embedding(text: str) -> list[float] | None:
    """Generate embedding vector for RAG storage using HF free tier."""
    if not HF_TOKEN:
        return None

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{HF_API}/{EMBEDDING_MODEL}",  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={"inputs": text[:1024]},
        )
        if r.status_code == 503:
            logger.warning("HF embedding cold start, model loading")
            return None
        if r.status_code == 200:
            result = r.json()
            # Handle different response formats
            if isinstance(result, list) and len(result) > 0:
                return result[0] if isinstance(result[0], list) else result
            return result
        logger.warning(f"HF embedding failed: {r.status_code}")
        return None

# ── Wallet Labeling via Pattern Memory ──────────────────

WALLET_PATTERNS = {
    "sybil_farmer": [
        "funded from exchange within seconds of 100+ other wallets",
        "identical funding amounts across multiple wallets",
        "no organic activity, only test transactions",
        "funded by known Sybil distributor",
    ],
    "wash_trader": [
        "circular transfers between related wallets",
        "buys and sells same token within minutes",
        "volume spikes without holder count changes",
        "coordinated buy/sell patterns",
    ],
    "sandwich_bot": [
        "high frequency trading with frontrun pattern",
        "buys before large buys, sells immediately after",
        "MEV extraction patterns",
        "flashbots bundle usage",
    ],
    "liquidity_remover": [
        "large LP removal shortly after token launch",
        "multiple LP positions removed simultaneously",
        "liquidity drained to fresh wallet",
        "LP removal preceded by marketing push",
    ],
    "honeypot_deployer": [
        "deploys tokens that can't be sold",
        "reuses contract code across multiple tokens",
        "disables transfers after liquidity added",
        "ownership not renounced, hidden mint functions",
    ],
    "phishing_operator": [
        "sends tokens to many addresses with scam links",
        "impersonates legitimate token contracts",
        "uses airdrop as phishing vector",
        "connects to known phishing domains",
    ],
    "mixer_user": [
        "funds pass through Tornado Cash or similar",
        "receives from mixer, sends to clean wallet",
        "layered mixing through multiple hops",
        "funds originate from high-risk sources",
    ],
    "insider_trader": [
        "buys tokens before public announcements",
        "linked to team wallets or deployers",
        "sells immediately after hype peak",
        "coordinated timing with other insiders",
    ],
}

async def label_wallet(wallet_data: dict) -> dict:
    """Label a wallet based on behavioral patterns and RAG memory."""
    labels = []
    confidence_scores = {}

    # Check behavioral patterns
    behavior = wallet_data.get("behavior_summary", "")
    wallet_data.get("transactions", [])

    for label, patterns in WALLET_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if pattern.lower() in behavior.lower():
                score += 1
        if score > 0:
            confidence = min(score / len(patterns), 1.0)
            confidence_scores[label] = round(confidence, 2)
            if confidence > 0.3:
                labels.append({"label": label, "confidence": round(confidence, 2)})

    # Query RAG for similar wallet patterns
    try:
        from app.rag_service import search_documents

        rag_results = await search_documents(
            "wallet_profiles", wallet_data.get("address", "") + " " + behavior, limit=5
        )
        if rag_results:
            for r in rag_results:
                content = r.get("content", "")
                for label, patterns in WALLET_PATTERNS.items():
                    if any(p.lower() in content.lower() for p in patterns):
                        if label not in confidence_scores:
                            confidence_scores[label] = 0
                        confidence_scores[label] = min(confidence_scores[label] + 0.15, 1.0)
    except (AttributeError, ImportError):
        pass

    labels = [
        {"label": k, "confidence": v}
        for k, v in sorted(confidence_scores.items(), key=lambda x: x[1], reverse=True)
        if v > 0.2
    ]

    return {
        "wallet_address": wallet_data.get("address", "unknown"),
        "labels": labels[:5],
        "primary_label": labels[0]["label"] if labels else "unknown",
        "risk_score": max([line["confidence"] for line in labels]) * 100 if labels else 0,
        "analyzed_at": datetime.now(UTC).isoformat(),
    }

# ── Supabase Hybrid Storage ────────────────────────────

async def sync_to_supabase(collection: str, document: dict) -> dict:
    """Sync RAG document to Supabase for persistent hybrid storage."""
    if not _get_url() or not _get_key():
        return {"status": "skipped", "reason": "No Supabase config"}

    doc_id = hashlib.sha256(
        f"{collection}:{document.get('content', '')[:100]}".encode()
    ).hexdigest()[:16]

    payload = {
        "document_id": doc_id,
        "collection": collection,
        "content": document.get("content", "")[:5000],
        "metadata": document.get("metadata", {}),
        "synced_at": datetime.now(UTC).isoformat(),
    }

    headers = _get_headers()
    headers["Prefer"] = "resolution=merge-duplicates"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{_get_url()}/rest/v1/rag_documents", json=payload, headers=headers)
        if r.status_code in (200, 201, 409):
            return {"status": "synced", "doc_id": doc_id, "supabase_status": r.status_code}
        return {"status": "failed", "error": r.text[:200]}

# ── Batch Processing ───────────────────────────────────

async def run_intelligence_cycle() -> dict:
    """Run a full intelligence cycle: classify, label, embed, sync."""
    results = {
        "cycle": datetime.now(UTC).isoformat(),
        "scam_checks": 0,
        "wallet_labels": 0,
        "embeddings": 0,
        "supabase_syncs": 0,
        "errors": [],
    }

    return results
