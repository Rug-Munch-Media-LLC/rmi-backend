#!/usr/bin/env python3
"""
RMI AI Risk Explainer - Ollama Cloud Powered
=============================================
Takes raw scanner output → generates consumer-friendly risk explanations.
Used by Telegram bot, website, and scanner API.

Cost: ~100 tokens per explanation = ~$0.0007 on Ollama Cloud
"""

from __future__ import annotations
from typing import Any

import json
import os
from urllib.request import Request, urlopen

from app.core.logging import get_logger

logger = get_logger("rmi.risk_explainer")

OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = """You are RMI Risk Analyst. Given raw token scanner data, write a consumer-friendly risk explanation in 3-4 sentences.

Rules:
- Start with the safety score and risk level (SAFE/LOW/MEDIUM/HIGH/CRITICAL)
- Mention the 1-2 most important risk flags with plain-English explanations
- If there are green flags, mention the most reassuring one
- Be direct and honest - call out scams clearly
- Use Telegram HTML formatting: <b>bold</b> for key terms
- Never give financial advice. End with "Always DYOR."

Example output:
"<b>Safety: 23/100 - HIGH RISK</b>. This token has <b>unlocked liquidity</b>, meaning the deployer can drain funds anytime. The <b>deployer wallet has 6 prior rugs</b>. No redeeming factors found. Avoid this token. Always DYOR."
"""


def explain_risks(scan: dict) -> str:
    """Generate a human-readable risk explanation from scanner data."""
    if not scan or scan.get("safety_score") is None:
        return "<b>Unable to analyze</b> - no scanner data available."

    score = scan.get("safety_score", 50)
    flags = scan.get("risk_flags", [])
    green = scan.get("green_flags", [])
    name = scan.get("name", scan.get("symbol", "This token"))
    modules = len(scan.get("modules_run", []))

    # Build a concise prompt for the AI
    prompt = f"""Token safety scan results:
- Token: {name}
- Safety score: {score}/100
- Risk flags: {", ".join(flags[:5]) if flags else "none"}
- Green flags: {", ".join(green[:3]) if green else "none"}
- Modules analyzed: {modules}

Write the explanation."""

    try:
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 150,
                "temperature": 0.3,
            }
        ).encode()

        req = Request(
            OLLAMA_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {OLLAMA_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Risk explainer failed: {e}")
        # Fallback: basic explanation without AI
        return _basic_explain(scan)


def _basic_explain(scan: dict) -> str:
    """Basic explanation when AI is unavailable."""
    score = scan.get("safety_score", 50)
    if score >= 80:
        level = "SAFE"
    elif score >= 60:
        level = "LOW RISK"
    elif score >= 40:
        level = "MEDIUM RISK"
    elif score >= 20:
        level = "HIGH RISK"
    else:
        level = "CRITICAL"

    flags = scan.get("risk_flags", [])
    green = scan.get("green_flags", [])
    scan.get("name", scan.get("symbol", "This token"))

    msg = [f"<b>Safety: {score}/100 - {level}</b>"]
    if flags:
        msg.append(f"Risk flags: {', '.join(flags[:3])}")
    if green:
        msg.append(f"Green flags: {', '.join(green[:2])}")
    msg.append("Always DYOR.")
    return ". ".join(msg)


# ── News Classification ──

NEWS_SYSTEM = """Classify crypto news headlines into categories. Reply with ONLY the category name.

Categories:
- SCAM: rug pulls, hacks, exploits, phishing, fraud
- MARKET: price action, trading, volume, market cap, BTC/ETH moves
- REGULATION: government, SEC, legal, compliance, bans
- SECURITY: vulnerability, audit, patch, wallet security
- DEFI: DeFi protocols, yield, liquidity, lending
- MEMECOIN: meme tokens, celebrity coins, pump events
- GENERAL: anything else"""


def classify_news(title: str, content: str = "") -> str:
    """Classify a news article into a category."""
    text = f"{title}\n{content[:200]}" if content else title

    try:
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": NEWS_SYSTEM},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 10,
                "temperature": 0.1,
            }
        ).encode()

        req = Request(
            OLLAMA_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {OLLAMA_KEY}",
                "Content-Type": "application/json",
            },
        )
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read())
        category = data["choices"][0]["message"]["content"].strip().upper()
        # Normalize
        for cat in ["SCAM", "MARKET", "REGULATION", "SECURITY", "DEFI", "MEMECOIN", "GENERAL"]:
            if cat in category:
                return cat
        return "GENERAL"
    except Exception as e:
        logger.warning(f"News classification failed: {e}")
        # Basic keyword fallback
        t = (title + " " + content).lower()
        if any(w in t for w in ["hack", "exploit", "rug", "scam", "phish"]):
            return "SCAM"
        if any(w in t for w in ["price", "btc", "eth", "bull", "bear", "market"]):
            return "MARKET"
        if any(w in t for w in ["sec ", "regulation", "ban", "law", "legal"]):
            return "REGULATION"
        return "GENERAL"


if __name__ == "__main__":
    # Test
    test = {
        "safety_score": 23,
        "risk_flags": ["LP_LOCK_LOW", "DEV_HIGH_RISK", "HONEYPOT_DETECTED"],
        "green_flags": [],
        "name": "SCAMCOIN",
        "modules_run": ["security", "holders", "liquidity"],
    }
    print(explain_risks(test))
    print()
    print(classify_news("$4M rug pull on Solana - deployer drained LP", ""))
