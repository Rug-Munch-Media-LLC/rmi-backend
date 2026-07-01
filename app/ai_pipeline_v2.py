"""
RMI AI Pipeline v2 — Production Grade
======================================
Caching, fallbacks, rate limiting, smart prompts.
All 12 modules battle-tested against Ollama Cloud.
"""

import hashlib
import json
import logging
import os
import time
from urllib.request import Request, urlopen

logger = logging.getLogger("rmi.ai")
OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", "")
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
CACHE_TTL = 300  # 5 min cache for identical calls

# Simple TTL cache
_cache = {}


def _cached_call(system: str, prompt: str, max_tokens: int = 250, temp: float = 0.3) -> str:
    key = hashlib.md5(f"{system[:50]}|{prompt[:100]}".encode()).hexdigest()
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return _cache[key][1]
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
        resp = urlopen(req, timeout=12)
        result = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
        _cache[key] = (now, result)
        return result
    except Exception as e:
        logger.error(f"Ollama AI call failed: {e}")
        return ""


# ── 1. TOKEN RISK EXPLAINER (improved) ──
def explain_risks(scan: dict) -> str:
    if not scan or scan.get("safety_score") is None:
        return "<b>Unable to analyze</b> — no scanner data."
    score = scan.get("safety_score", 50)
    flags = scan.get("risk_flags", [])
    green = scan.get("green_flags", [])
    name = scan.get("name", scan.get("symbol", "token"))
    mods = len(scan.get("modules_run", []))
    prompt = f"Token:{name} Score:{score}/100 Risks:{', '.join(flags[:5]) or 'none'} Green:{', '.join(green[:3]) or 'none'} Modules:{mods}"
    system = """You explain token risk to non-technical users. 3-4 sentences. Start with safety score. Mention top risks in plain English. End with "Always DYOR." Use <b>bold</b> for key terms. Never give financial advice."""
    result = _cached_call(system, prompt, max_tokens=150, temp=0.2)
    return result or f"<b>Safety: {score}/100</b>. Risk flags: {', '.join(flags[:3])}. Always DYOR."


# ── 2. NEWS CLASSIFIER (improved) ──
def classify_news(title: str, content: str = "") -> str:
    text = f"{title} {content[:200]}"
    system = """Classify crypto news into ONE word: SCAM MARKET REGULATION SECURITY DEFI MEMECOIN GENERAL"""
    result = _cached_call(system, text, max_tokens=8, temp=0.1)
    if result:
        for cat in ["SCAM", "MARKET", "REGULATION", "SECURITY", "DEFI", "MEMECOIN", "GENERAL"]:
            if cat in result.upper():
                return cat
    # Fast fallback
    t = text.lower()
    if any(w in t for w in ["hack", "exploit", "rug", "scam", "phish", "drain"]):
        return "SCAM"
    if any(w in t for w in ["price", "btc", "eth", "bull", "bear", "market"]):
        return "MARKET"
    return "GENERAL"


# ── 3. WALLET PROFILER ──
def profile_wallet(tx: dict) -> str:
    system = """Classify wallet persona from tx data. Reply: PERSONA|confidence. Options: DayTrader Whale BotFarm Insider ScamDeployer AirdropHunter DiamondHands DegenGambler Unknown"""
    return _cached_call(system, json.dumps(tx)[:1000], max_tokens=25) or "Unknown|0"


# ── 4. RAG ENRICHER ──
def enrich_rag(query: str, docs: str) -> str:
    system = """Reformat RAG chunks into 2-3 sentence coherent answer. Preserve key facts."""
    return _cached_call(system, f"Q:{query}\nD:{docs[:2000]}", max_tokens=200) or docs[:400]


# ── 5. ALERT RANKER ──
def rank_alerts(alerts: list) -> list:
    summary = "\n".join(
        f"{a.get('id', '?')}|{a.get('severity', '?')}|{(a.get('title', '') or '')[:80]}" for a in alerts[:10]
    )
    result = _cached_call("Rank these by urgency. Reply: id1,id2,id3...", summary, max_tokens=50)
    return [x.strip() for x in (result or "").split(",") if x.strip()]


# ── 6. MARKET BRIEFING ──
def briefing(data: dict) -> str:
    system = """3-paragraph crypto market briefing. P1:volume+chains P2:top risks P3:what to watch. <b>bold</b> key findings. Under 250 words."""
    return _cached_call(system, json.dumps(data)[:2000], max_tokens=350, temp=0.5) or "Briefing unavailable."


# ── 7. INCIDENT AUTOPSY ──
def post_mortem(incident: dict) -> str:
    system = """Crypto scam forensic post-mortem. What happened→How→Red flags→Protection. <b>bold</b> findings. Under 200 words."""
    return _cached_call(system, json.dumps(incident)[:1500], max_tokens=300, temp=0.4) or "Autopsy unavailable."


# ── 8. COMMUNITY FORENSICS ──
def analyze_submission(sub: dict) -> str:
    system = """Analyze suspicious token submission. Verdict:LIKELY SCAM/SUSPICIOUS/MORE INFO + 2-3 concerns."""
    return _cached_call(system, json.dumps(sub)[:1500], max_tokens=200) or "Analysis unavailable."


# ── 9. CROSS-CHAIN DETECTION ──
def cross_chain(wallets: dict) -> str:
    system = """Same entity across chains? Reply: MATCH|conf|reason or NO_MATCH|reason"""
    return _cached_call(system, json.dumps(wallets)[:1500], max_tokens=80) or "Unknown"


# ── 10. BLOG DRAFT ──
def blog_draft(topic: str, data: dict) -> str:
    system = """Crypto security blog post draft. Title|Hook|Body(3-4para)|KeyTakeaways|CTA. Markdown. Professional."""
    return (
        _cached_call(system, f"Topic:{topic}\nData:{json.dumps(data)[:2000]}", max_tokens=500, temp=0.6)
        or f"# {topic}\n\nDraft unavailable."
    )


# ── 11. SOCIAL POSTS ──
def social_post(incident: dict) -> str:
    system = (
        """Tweet+Telegram post about crypto security finding. Twitter:<280 chars> | Telegram:<500 chars>. Hook first."""
    )
    return _cached_call(system, json.dumps(incident)[:1000], max_tokens=200, temp=0.7) or "Post unavailable."


# ── 12. TOKEN COMPARE ──
def compare_tokens(a: dict, b: dict) -> str:
    system = """Compare 2 tokens for safety. SAFER:<name> REASON:<2sentences> SCORE_DIFF:<a vs b> KEY_DIFFERENCES:<bullets>"""
    prompt = f"A:{json.dumps(a)[:800]}\nB:{json.dumps(b)[:800]}"
    return _cached_call(system, prompt, max_tokens=200) or "Comparison unavailable."
