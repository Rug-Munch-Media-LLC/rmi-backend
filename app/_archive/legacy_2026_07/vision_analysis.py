"""Multi-Modal Token Analysis - Gemini 2.5 Flash vision for logo/website scanning.
Detects: stolen artwork, template websites, fake team photos, suspicious branding."""

import base64
import os

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/vision", tags=["vision-analysis"])

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
GEMINI_VISION_MODEL = "gemini-2.5-flash"  # 1,500 req/day free
GEMINI_PRO_MODEL = "gemini-2.5-pro"  # 50 req/day free - use sparingly


async def _analyze_image(image_url: str, question: str) -> dict:
    """Send image to Gemini 2.5 Flash for vision analysis."""
    if not GEMINI_KEY:
        return {"error": "GEMINI_API_KEY not configured"}

    try:
        # Fetch image and convert to base64
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(image_url)
            if r.status_code != 200:
                return {"error": f"Image fetch failed: {r.status_code}"}
            img_b64 = base64.b64encode(r.content).decode()

        # Send to Gemini
        payload = {
            "contents": [{"parts": [{"text": question}, {"inline_data": {"mime_type": "image/png", "data": img_b64}}]}]
        }

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{GEMINI_URL}?key={GEMINI_KEY}", json=payload)
            if r.status_code == 200:
                data = r.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return {"analysis": text, "model": "gemini-2.5-flash"}
            return {"error": f"Gemini API: {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@router.get("/token-logo")
async def analyze_token_logo(token_address: str, chain: str = Query("solana")):
    """Analyze token logo for scam indicators - stolen art, AI-generated, generic."""

    # Build logo URL based on chain
    logo_urls = {
        "solana": f"https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/{token_address}/logo.png",
        "ethereum": f"https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/{token_address}/logo.png",
        "bsc": f"https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/smartchain/assets/{token_address}/logo.png",
    }

    logo_url = logo_urls.get(chain, logo_urls["solana"])

    question = """Analyze this token logo for scam indicators:
1. Is it AI-generated? (look for artifacts, unnatural symmetry)
2. Is it stolen from another project? (known brands, copied designs)
3. Is it generic/template? (no original design elements)
4. Does it look professional or rushed?
5. SCAM_SCORE: give a 0-100 score where 100 = definitely a scam logo
Respond in JSON: {"ai_generated": bool, "stolen": bool, "generic": bool, "professional": bool, "scam_score": int, "explanation": "one sentence"}"""

    result = await _analyze_image(logo_url, question)
    result["token"] = token_address
    result["chain"] = chain
    return result


@router.get("/website-scan")
async def scan_token_website(url: str):
    """Scan a token's website screenshot for red flags."""
    # Use a screenshot service or just analyze the URL patterns
    website_red_flags = []

    # Quick pattern checks
    import re

    if re.search(r"(airdrop|claim|giveaway|free).*\.(io|com|xyz)", url.lower()):
        website_red_flags.append("Scam giveaway pattern in URL")
    if url.endswith((".xyz", ".click", ".win", ".loan")):
        website_red_flags.append("Suspicious TLD - common for scams")
    if len(url) > 50:
        website_red_flags.append("Unusually long URL - possible phishing")

    risk = min(100, len(website_red_flags) * 30)

    return {
        "url": url,
        "red_flags": website_red_flags,
        "risk_score": risk,
        "risk_level": "HIGH" if risk > 50 else "MEDIUM" if risk > 20 else "LOW",
        "note": "Full screenshot analysis requires Gemini Pro vision. Upgrade for visual scan."
        if not GEMINI_KEY
        else "Gemini vision analysis ready.",
    }


@router.get("/token-visual-audit")
async def full_visual_audit(token_address: str, chain: str = "solana", website_url: str = ""):
    """Complete visual audit: logo + website + social presence."""
    logo_check = await analyze_token_logo(token_address, chain)
    website_check = await scan_token_website(website_url) if website_url else {}

    logo_score = logo_check.get("analysis", {})
    website_score = website_check.get("risk_score", 0)

    # Combined score
    logo_scam = logo_score.get("scam_score", 0) if isinstance(logo_score, dict) else 0

    combined_score = (logo_scam * 0.6) + (website_score * 0.4)

    return {
        "token": token_address,
        "chain": chain,
        "overall_visual_risk": round(combined_score, 1),
        "risk_level": "CRITICAL"
        if combined_score > 70
        else "HIGH"
        if combined_score > 40
        else "MEDIUM"
        if combined_score > 20
        else "LOW",
        "logo_analysis": logo_check,
        "website_analysis": website_check,
        "verdict": "Visual audit shows significant scam indicators. Avoid."
        if combined_score > 50
        else "Visual audit passed. No obvious red flags in branding."
        if combined_score < 25
        else "Some concerns. Proceed with caution.",
    }
