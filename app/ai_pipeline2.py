#!/usr/bin/env python3
"""
RMI AI Pipeline Part 2 — Remaining 7 Modules
=============================================
Community Forensics | Cross-Chain Entity | Ghost Blog | Social Media | Token Compare
All Ollama Cloud deepseek-v4-flash. ~$0.001/operation.
"""

import json
import logging
import os
from urllib.request import Request, urlopen

logger = logging.getLogger("rmi.ai_pipeline2")
OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"


def _call_ai(system: str, prompt: str, max_tokens: int = 250, temp: float = 0.3) -> str:
    try:
        body = json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temp,
            }
        ).encode()
        req = Request(
            OLLAMA_URL,
            data=body,
            headers={"Authorization": f"Bearer {OLLAMA_KEY}", "Content-Type": "application/json"},
        )
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"AI call failed: {e}")
        return ""


# ── 8. COMMUNITY FORENSICS AUTO-ANALYSIS ──
FORENSICS_SYSTEM = """You are a crypto forensics investigator. A community member submitted a suspicious token for review.
Analyze the information and provide:
1. Initial verdict (LIKELY SCAM / SUSPICIOUS / NEEDS MORE INFO)
2. Key concerns (2-3 bullet points)
3. Recommended next steps for the investigator
Keep it under 150 words."""


def analyze_community_submission(submission: dict) -> str:
    return _call_ai(FORENSICS_SYSTEM, json.dumps(submission)[:1500], max_tokens=250)


# ── 10. CROSS-CHAIN ENTITY DETECTION ──
CROSSCHAIN_SYSTEM = """You identify crypto entities operating across multiple blockchains.
Given wallet data from different chains, determine if they're the same entity.
Reply format: MATCH|confidence_0-100|reason OR NO_MATCH|reason"""


def detect_cross_chain(wallets: dict) -> str:
    return _call_ai(CROSSCHAIN_SYSTEM, json.dumps(wallets)[:1500], max_tokens=100)


# ── 11. GHOST BLOG AUTO-DRAFT ──
GHOST_SYSTEM = """You are a crypto security blogger for Rug Munch Intelligence (rugmunch.io).
Write a blog post draft from scanner data and incident reports.
Structure:
- Title (catchy, SEO-friendly, under 80 chars)
- Hook (1 sentence that grabs attention)
- Body (3-4 paragraphs explaining the threat)
- Key takeaways (2-3 bullet points)
- Call to action (check your tokens, use our scanner)
Use markdown formatting. Professional but engaging tone."""


def draft_blog_post(topic: str, data: dict) -> str:
    prompt = f"Topic: {topic}\n\nData:\n{json.dumps(data)[:2000]}"
    return _call_ai(GHOST_SYSTEM, prompt, max_tokens=500, temp=0.6)


# ── 13. SOCIAL MEDIA POST GENERATOR ──
SOCIAL_SYSTEM = """You are the social media manager for Rug Munch Intelligence (@CryptoRugMunch).
Write a tweet/telegram post about a crypto security finding.
Rules:
- Under 280 chars for Twitter, under 500 for Telegram
- Start with a hook (stat, warning, or question)
- Include $TICKER if relevant
- End with a call to action or link
- Use emojis sparingly (1-2 max)
- No hashtag spam (2-3 max)
Reply format: TWITTER: <tweet> | TELEGRAM: <post>"""


def generate_social_post(incident: dict, platform: str = "both") -> str:
    return _call_ai(SOCIAL_SYSTEM, json.dumps(incident)[:1000], max_tokens=200, temp=0.7)


# ── 14. TOKEN COMPARISON ENGINE ──
COMPARE_SYSTEM = """Compare two crypto tokens for safety. Given their scanner results, determine which is safer and why.
Reply format:
SAFER: <token_name>
REASON: <2-3 sentence comparison>
SCORE_DIFF: <token1_score> vs <token2_score>
KEY_DIFFERENCES: <bullet points>"""


def compare_tokens(token_a: dict, token_b: dict) -> str:
    prompt = f"Token A:\n{json.dumps(token_a)[:800]}\n\nToken B:\n{json.dumps(token_b)[:800]}"
    return _call_ai(COMPARE_SYSTEM, prompt, max_tokens=200)
