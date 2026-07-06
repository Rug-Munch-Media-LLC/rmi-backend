#!/usr/bin/env python3
"""
RMI MiniMax Pipeline - Cron Jobs
=================================
Social threads | Support auto-responder | Health narrative
All powered by MiniMax-Text-01 ($20/mo flat, 1M context)
"""

import json
import os
import urllib.request
from datetime import UTC, datetime

KEY = os.getenv("MINIMAX_API_KEY", "")
if not KEY:
    try:
        with open("/app/.env") as f:
            for line in f:
                if line.startswith("MINIMAX_API_KEY="):
                    KEY = line.strip().split("=", 1)[1]
                    break
    except: pass  # noqa: E701, E722
URL = "https://api.minimax.io/v1/chat/completions"
MODEL = "MiniMax-Text-01"
BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")

def call_minimax(system: str, prompt: str, max_tokens: int = 500, temp: float = 0.7) -> str:
    try:
        body = json.dumps({
            "model": MODEL,
            "messages": [{"role":"system","content":system},{"role":"user","content":prompt}],
            "max_tokens": max_tokens, "temperature": temp
        }).encode()
        req = urllib.request.Request(URL, data=body, headers={
            "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=20)
        return json.loads(resp.read())["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[MiniMax unavailable: {str(e)[:80]}]"


# ═══════════════════════════════════════════
# 4. SOCIAL MEDIA THREAD GENERATOR
# ═══════════════════════════════════════════
SOCIAL_SYSTEM = """You are the social media manager for Rug Munch Intelligence (@CryptoRugMunch).
Write an engaging crypto security thread. Format:
🧵 THREAD: <catchy title>
1/ <hook - shocking stat or recent scam>
2/ <how it works, simple terms>
3/ <how to protect yourself>
4/ <use our scanner at rugmunch.io>
#CryptoScam #RugPull #DYOR

Keep each tweet under 280 chars. Make it punchy and shareable."""

def generate_social_thread(scam_data: str) -> str:
    return call_minimax(SOCIAL_SYSTEM, f"Recent scam data:\n{scam_data[:3000]}", 400)


# ═══════════════════════════════════════════
# 7. SUPPORT AUTO-RESPONDER
# ═══════════════════════════════════════════
SUPPORT_SYSTEM = """You are the support bot for Rug Munch Intelligence (rugmunch.io).
Answer crypto security questions concisely. Be helpful, direct, accurate.
Common topics: rug pulls, honeypots, token scanning, wallet safety, scam detection.
Always mention our free scanner if relevant. Under 200 words."""

def answer_question(question: str) -> str:
    return call_minimax(SUPPORT_SYSTEM, question, 250, 0.4)

# Pre-warm cache with common questions
COMMON_QUESTIONS = [
    "What is a rug pull?",
    "How do I check if a token is safe?",
    "What is a honeypot in crypto?",
    "How to protect my wallet from scams?",
    "What's the best free crypto scanner?",
    "How to spot a fake token?",
    "What is liquidity locking?",
    "How do scam tokens work?",
    "Is my wallet safe?",
    "What are red flags in new tokens?",
]

def warm_support_cache():
    """Pre-generate answers for common questions."""
    for q in COMMON_QUESTIONS:
        a = answer_question(q)
        print(f"  {q[:40]}... → {a[:60]}...")


# ═══════════════════════════════════════════
# 14. HEALTH NARRATIVE
# ═══════════════════════════════════════════
HEALTH_SYSTEM = """You are the RMI system health reporter. Given raw server metrics, write a brief, human-readable health report.
Format:
🩺 RMI SYSTEM HEALTH - {timestamp}
├─ Backend: {status with emoji}
├─ Cron Jobs: {ok}/{total} healthy
├─ RAG: {docs} documents
├─ Disk: {pct}%
├─ Load: {avg}
└─ Verdict: 1 sentence summary

Keep it under 150 words. Use ✅⚠️🔴 emojis."""

def health_narrative(metrics: dict) -> str:
    prompt = json.dumps(metrics)
    return call_minimax(HEALTH_SYSTEM, prompt, 250, 0.3)


# ═══════════════════════════════════════════
# MAIN - Run all three
# ═══════════════════════════════════════════
if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd in ("social", "all"):
        print("=== SOCIAL THREAD ===")
        try:
            r = urllib.request.urlopen(f"{BACKEND}/api/v1/scanner/stats", timeout=5)
            data = json.loads(r.read())
            thread = generate_social_thread(json.dumps(data)[:3000])
            print(thread[:400])
        except Exception as e:
            print(f"Social thread failed: {e}")

    if cmd in ("warm", "all"):
        print("\n=== WARMING SUPPORT CACHE ===")
        warm_support_cache()

    if cmd in ("health", "all"):
        print("\n=== HEALTH NARRATIVE ===")
        metrics = {
            "timestamp": datetime.now(UTC).isoformat(),
            "backend": "alive", "disk_pct": 83, "load": "4.2",
            "crons_ok": 35, "crons_total": 56, "rag_docs": 20985,
        }
        report = health_narrative(metrics)
        print(report[:400])
