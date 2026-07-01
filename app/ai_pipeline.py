#!/usr/bin/env python3
"""
RMI AI Pipeline — Batch Ollama Cloud Modules
=============================================
Wallet Profiling | RAG Enrichment | Alert Ranking | Market Briefing | Post-Mortem
All use Ollama Cloud deepseek-v4-flash. ~$0.001 per operation.
"""

import json
import logging
import os
from urllib.request import Request, urlopen

logger = logging.getLogger("rmi.ai_pipeline")
OLLAMA_KEY = os.getenv("OLLAMA_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
OLLAMA_URL = "https://ollama.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"


def _call_ai(system: str, prompt: str, max_tokens: int = 200, temp: float = 0.3) -> str:
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


# ── 7. WALLET BEHAVIORAL PROFILING ──
WALLET_SYSTEM = """Classify a crypto wallet into a persona based on transaction patterns.
Reply with ONLY: persona_name|confidence_0-100

Personas:
- Day Trader: frequent buys/sells, short holds, high volume
- Whale Accumulator: large buys, holds long, rare sells
- Bot Farm: identical transaction patterns, same gas, rapid-fire
- Insider: buys before pumps, sells before dumps, too perfect timing
- Honeypot Victim: bought tokens that can't be sold
- Scam Deployer: creates tokens, drains liquidity, repeats
- Airdrop Hunter: tiny transactions, hundreds of tokens, zero holds
- Diamond Hands: bought once, never sold, regardless of price
- Degen Gambler: buys meme coins, holds minutes, high risk tolerance
- Unknown: insufficient data"""


def profile_wallet(tx_data: dict) -> str:
    summary = json.dumps(tx_data)[:1000]
    result = _call_ai(WALLET_SYSTEM, f"Transactions:\n{summary}", max_tokens=30)
    return result if "|" in result else "Unknown|0"


# ── 9. RAG QUERY ENRICHMENT ──
RAG_SYSTEM = """You reformat raw RAG search results into a coherent, readable answer.
Keep it under 150 words. Preserve key facts. Add a 1-line summary at the end."""


def enrich_rag_results(query: str, raw_docs: str) -> str:
    return _call_ai(RAG_SYSTEM, f"Query: {query}\n\nRaw results:\n{raw_docs[:2000]}")


# ── 12. ALERT PRIORITIZATION ──
ALERT_SYSTEM = """Rank these crypto security alerts by urgency. Reply ONLY with the alert IDs in priority order, comma-separated.
Priority rules: CRITICAL (immediate rug/hack) > HIGH (likely scam) > MEDIUM (suspicious) > LOW (noise)."""


def rank_alerts(alerts: list) -> list:
    summary = "\n".join(
        f"ID:{a.get('id', '?')} | {a.get('severity', '?')} | {a.get('title', '?')[:100]}" for a in alerts[:20]
    )
    result = _call_ai(ALERT_SYSTEM, summary, max_tokens=50)
    return [x.strip() for x in result.split(",") if x.strip()]


# ── 6. DAILY MARKET BRIEFING ──
MARKET_SYSTEM = """Write a 3-paragraph daily crypto market briefing from scanner data.
Para 1: Market overview (most scanned chains, scan volume)
Para 2: Top risks (worst tokens found today, emerging patterns)
Para 3: What to watch (trending scam types, new threat vectors)
Use Telegram HTML formatting. Keep it under 250 words. Professional but direct tone."""


def generate_market_briefing(scan_summary: dict) -> str:
    return _call_ai(MARKET_SYSTEM, json.dumps(scan_summary)[:2000], max_tokens=350, temp=0.5)


# ── 15. INCIDENT POST-MORTEM ──
AUTOPSY_SYSTEM = """Write a forensic post-mortem of a crypto scam incident.
Structure:
1. What happened (1 sentence)
2. How it worked (the mechanics, 2-3 sentences)
3. Red flags that were visible beforehand
4. How to protect against similar scams
Keep it under 200 words. Use <b>bold</b> for key findings. Professional forensic tone."""


def write_post_mortem(incident: dict) -> str:
    return _call_ai(AUTOPSY_SYSTEM, json.dumps(incident)[:1500], max_tokens=300, temp=0.4)
