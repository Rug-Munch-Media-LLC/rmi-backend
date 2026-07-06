"""Content Moderation Pipeline - AI-powered spam/scam/NSFW detection for user content."""

import os
import re

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/moderation", tags=["moderation"])

OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434")

BLOCKED_PATTERNS = [
    (r"(?i)(buy|sell|trade).*\b(signal|call)\b", "trading_signal"),
    (r"(?i)(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)", "ip_address"),
    (r"(?i)\b(0x[a-fA-F0-9]{40})\b.*\b(private.?key|seed.?phrase|mnemonic|password)\b", "wallet_phishing"),
    (r"(?i)(airdrop|giveaway|free.*token).*\b(claim|connect|verify)\b", "scam_airdrop"),
    (r"(https?://(?!rugmunch\.io|polymarket\.com|dexscreener\.com)[^\s]+)", "external_link"),
]


class ModerationRequest(BaseModel):
    text: str
    user_id: str = "anonymous"
    context: str = "comment"  # comment, post, review, chat


class ModerationResult(BaseModel):
    approved: bool
    risk_score: int  # 0-100
    flags: list[str]
    reason: str = ""


async def _ai_classify(text: str) -> dict:
    """Use Ollama to classify content."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{OLLAMA}/api/generate",
                json={
                    "model": "qwen2.5-coder:7b",
                    "prompt": f"Classify this crypto-related content as SAFE, SPAM, SCAM, or NSFW. Answer with one word only.\n\nContent: {text[:500]}\n\nClassification:",
                    "stream": False,
                    "options": {"num_predict": 5, "temperature": 0.1},
                },
            )
            if r.status_code == 200:
                response = r.json().get("response", "").strip().upper()
                return {"ai_verdict": response, "ai_used": True}
    except Exception:
        pass
    return {"ai_verdict": "UNKNOWN", "ai_used": False}


@router.post("/check")
async def moderate_content(req: ModerationRequest):
    """Check content for spam, scams, NSFW, and policy violations."""
    flags = []
    risk = 0

    # Pattern-based detection
    for pattern, flag_type in BLOCKED_PATTERNS:
        if re.search(pattern, req.text):
            flags.append(flag_type)
            risk += 25

    # Length checks
    if len(req.text) < 5:
        flags.append("too_short")
        risk += 10
    if len(req.text) > 10000:
        flags.append("too_long")
        risk += 5

    # AI classification
    ai = await _ai_classify(req.text)
    ai_verdict = ai["ai_verdict"]
    if "SCAM" in ai_verdict:
        flags.append("ai_scam")
        risk += 40
    elif "SPAM" in ai_verdict:
        flags.append("ai_spam")
        risk += 25
    elif "NSFW" in ai_verdict:
        flags.append("ai_nsfw")
        risk += 50

    approved = risk < 40
    reason = "Content approved" if approved else f"Flagged: {', '.join(flags)}"

    return {
        "approved": approved,
        "risk_score": min(100, risk),
        "flags": flags,
        "reason": reason,
        "ai_classification": ai_verdict,
    }


@router.get("/stats")
async def moderation_stats():
    return {
        "patterns_checked": len(BLOCKED_PATTERNS),
        "ai_model": "qwen2.5-coder:7b",
        "categories": [
            "trading_signal",
            "wallet_phishing",
            "scam_airdrop",
            "external_link",
            "ai_scam",
            "ai_spam",
            "ai_nsfw",
        ],
    }
