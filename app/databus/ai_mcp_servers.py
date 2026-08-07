"""
RMI AI-POWERED MCP SERVERS - Local Ollama, Zero API Cost
=========================================================
8 unique MCP servers using local AI models.
qwen2.5-coder:7b - primary (coding, analysis)
llama3.2:3b - fast fallback (classification, summarization)
nomic-embed-text - RAG embeddings
dolphin-mistral:7b - creative/uncensored tasks
smollm2:1.7b - ultra-fast simple tasks
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx as req

OLLAMA = "http://ollama:11434/api/generate"


def gredis() -> Any:
    from dotenv import load_dotenv

    load_dotenv("/app/.env", override=True)
    import redis

    return redis.Redis(host="rmi-redis", port=6379, password=os.getenv("REDIS_PASSWORD"), decode_responses=True)


def trial(fp: str, tool: str, limit: int = 5) -> dict:
    # Premium AI tools - lower free tier, costs us CPU
    r, k = gredis(), f"mcp:trial:{tool}:{fp}"
    c = int(r.get(k) or 0)
    if c < limit:
        r.incr(k)
        r.expire(k, 86400)
        return {"tier": "free", "remaining": limit - c - 1}
    return {"tier": "free_exhausted", "upgrade": "$0.05-0.15/call via x402. Pays for our compute."}


def ask(prompt: str, model: str = "qwen2.5-coder:7b", tokens: int = 250) -> str:
    try:
        r = req.post(
            OLLAMA,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": tokens},
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("response", "").strip()
    except (ConnectionError, OSError, TimeoutError, req.HTTPError, req.TimeoutException):
        pass
    return ""


# ═══════════════════════════════════════════════════
# MCP #1: CONTRACT EXPLAINER - AI explains smart contracts
# ═══════════════════════════════════════════════════
def explain_contract(address: str, chain: str = "ethereum", fp: str = "anon") -> dict:
    """AI explains what a smart contract does. Uses qwen2.5-coder. 5 free/day. $0.10 premium."""
    auth = trial(fp, "explain", 5)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    result = {"address": address, "chain": chain}
    # Fetch source from Etherscan
    try:
        r = req.get(
            f"https://api.etherscan.io/api?module=contract&action=getsourcecode&address={address}",
            timeout=10,
        )
        if r.status_code == 200:
            src = r.json().get("result", [{}])[0].get("SourceCode", "")
            if src:
                # Ask AI to explain
                explanation = ask(
                    f"""You are a smart contract auditor. Explain this contract in plain English:
Contract: {src[:3000]}
Explain: 1) What this contract does 2) Any risks 3) Key functions. Be concise.""",
                    "qwen2.5-coder:7b",
                    250,
                )
                result["explanation"] = explanation
                result["ai_model"] = "qwen2.5-coder:7b"
    except (ConnectionError, OSError, TimeoutError, req.HTTPError, req.TimeoutException):
        pass
    result["auth"] = auth
    result["mcp"] = "rmi-contract-explain"
    return result


# ═══════════════════════════════════════════════════
# MCP #2: TX FORENSICS NARRATOR - AI narrates wallet activity
# ═══════════════════════════════════════════════════
def narrate_wallet(address: str, chain: str = "ethereum", fp: str = "anon") -> dict:
    """AI narrates what a wallet is doing. 5 free/day. $0.10 premium."""
    auth = trial(fp, "narrate", 5)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    result = {"address": address, "chain": chain}
    try:
        r = req.get(
            f"https://api.etherscan.io/api?module=account&action=txlist&address={address}&sort=desc&page=1&offset=10",
            timeout=10,
        )
        if r.status_code == 200:
            txs = r.json().get("result", [])
            tx_summary = "\n".join(
                [
                    f"TX {i}: from {t['from'][:10]}... to {t['to'][:10]}... value {int(t['value']) / 1e18:.2f} ETH"
                    for i, t in enumerate(txs[:10])
                ]
            )
            narrative = ask(
                f"""You are a crypto forensic analyst. Analyze this wallet activity:
{tx_summary}
Narrate: What is this wallet doing? Trading? Accumulating? Laundering? Be specific.""",
                "qwen2.5-coder:7b",
                200,
            )
            result["narrative"] = narrative
            result["transactions_analyzed"] = len(txs)
            result["ai_model"] = "qwen2.5-coder:7b"
    except (ConnectionError, OSError, TimeoutError, req.HTTPError, req.TimeoutException):
        pass
    result["auth"] = auth
    result["mcp"] = "rmi-wallet-narrator"
    return result


# ═══════════════════════════════════════════════════
# MCP #3: RUG PULL PREDICTOR - AI predicts rug risk
# ═══════════════════════════════════════════════════
def predict_rug(address: str, chain: str = "ethereum", fp: str = "anon") -> dict:
    """AI rug pull prediction. Combines on-chain data + AI analysis. 3 free/day. $0.15 premium."""
    auth = trial(fp, "rugpredict", 3)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    result = {"address": address, "chain": chain}
    signals = []
    # GoPlus security
    try:
        r = req.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain}?contract_addresses={address}",
            timeout=10,
        )
        if r.status_code == 200:
            d = r.json().get("result", {}).get(address.lower(), {})
            signals = [
                f"Honeypot: {d.get('is_honeypot', '0')}",
                f"Buy tax: {d.get('buy_tax', '0')}%",
                f"Sell tax: {d.get('sell_tax', '0')}%",
                f"Open source: {d.get('is_open_source', '0')}",
                f"Owner renounced: {d.get('is_owner_renounced', '0')}",
            ]
    except (ConnectionError, OSError, TimeoutError, req.HTTPError, req.TimeoutException):
        pass
    prompt = f"""Token security scan results:\n{chr(10).join(signals)}\n\nBased on these signals, rate rug pull risk 0-100 and explain in 2 sentences."""
    prediction = ask(prompt, "qwen2.5-coder:7b", 150)
    result["signals"] = signals
    result["ai_prediction"] = prediction
    result["ai_model"] = "qwen2.5-coder:7b"
    result["auth"] = auth
    result["mcp"] = "rmi-rug-predictor"
    return result


# ═══════════════════════════════════════════════════
# MCP #4: NEWS TL;DR - AI summarizes crypto news
# ═══════════════════════════════════════════════════
def news_tldr(topic: str = "", fp: str = "anon") -> dict:
    """AI summarizes latest crypto news. Uses dolphin-mistral. 10 free/day. $0.05 premium."""
    auth = trial(fp, "tldr", 10)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    r = gredis()
    articles = []
    for idx in ["rmi:news:500feeds", "rmi:news:index", "rmi:news:global:index"]:
        for aid in r.zrevrange(idx, 0, 29):
            a = json.loads(r.get(f"rmi:news:article:{aid}") or "{}")
            if not topic or topic.lower() in a.get("title", "").lower():
                articles.append(a.get("title", ""))
    if not articles:
        return {"error": "No articles found", "auth": auth}
    prompt = f"""Summarize these 20 crypto news headlines into 3 key bullet points. Be concise and insightful.\n\nHeadlines:\n{chr(10).join(articles[:20])}"""
    summary = ask(prompt, "dolphin-mistral:7b", 200)
    return {
        "articles_analyzed": len(articles),
        "ai_summary": summary,
        "ai_model": "dolphin-mistral:7b",
        "auth": auth,
        "mcp": "rmi-news-tldr",
    }


# ═══════════════════════════════════════════════════
# MCP #5: WALLET PROFILER - AI profiles wallet identity
# ═══════════════════════════════════════════════════
def profile_wallet(address: str, fp: str = "anon") -> dict:
    """AI wallet identity profiling. 5 free/day. $0.08 premium."""
    auth = trial(fp, "profile", 5)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    result = {"address": address}
    r = gredis()
    # Gather labels
    labels = []
    for chain in ["ethereum", "solana", "bsc"]:
        cached = r.get(f"rmi:label:{chain}:{address.lower()}")
        if cached:
            c = json.loads(cached)
            labels.append(f"{chain}: {c.get('label', '')} / {c.get('name_tag', '')}")
    prompt = f"""Based on these blockchain labels, profile this wallet in 2 sentences:\n\nLabels: {", ".join(labels) if labels else "No known labels. Possibly personal wallet or new address."}\n\nWho might own this wallet? What is it used for?"""
    profile = ask(prompt, "llama3.2:3b", 120)
    result["labels_found"] = len(labels)
    result["ai_profile"] = profile
    result["ai_model"] = "llama3.2:3b"
    result["auth"] = auth
    result["mcp"] = "rmi-wallet-profiler"
    return result


# ═══════════════════════════════════════════════════
# MCP #6: CODE AUDITOR - AI reviews Solidity for bugs
# ═══════════════════════════════════════════════════
def audit_code(code: str, fp: str = "anon") -> dict:
    """AI reviews Solidity code. 3 free/day. $0.15 premium."""
    auth = trial(fp, "auditai", 3)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    prompt = f"""You are a senior Solidity auditor. Review this code for vulnerabilities:\n\n{code[:2500]}\n\nList: 1) Critical issues 2) Medium issues 3) Gas optimizations. Be concise."""
    audit = ask(prompt, "qwen2.5-coder:7b", 300)
    return {
        "code_length": len(code),
        "ai_audit": audit,
        "ai_model": "qwen2.5-coder:7b",
        "auth": auth,
        "mcp": "rmi-code-auditor",
    }


# ═══════════════════════════════════════════════════
# MCP #7: SENTIMENT ORACLE - AI market sentiment
# ═══════════════════════════════════════════════════
def sentiment_oracle(token: str = "bitcoin", fp: str = "anon") -> dict:
    """AI market sentiment. Analyzes news + labels. 10 free/day. $0.05 premium."""
    auth = trial(fp, "sentiment", 10)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    r = gredis()
    titles = []
    for idx in ["rmi:news:500feeds", "rmi:news:index"]:
        for aid in r.zrevrange(idx, 0, 29):
            a = json.loads(r.get(f"rmi:news:article:{aid}") or "{}")
            if token.lower() in (a.get("title", "") + a.get("content", "")).lower():
                titles.append(a.get("title", "")[:100])
    prompt = f"""Analyze crypto news sentiment for {token}. Rate BULLISH, NEUTRAL, or BEARISH with confidence 0-100.\n\nHeadlines:\n{chr(10).join(titles[:15])}\n\nRespond in 2 sentences with sentiment and rationale."""
    analysis = ask(prompt, "llama3.2:3b", 150)
    return {
        "token": token,
        "articles_found": len(titles),
        "ai_sentiment": analysis,
        "ai_model": "llama3.2:3b",
        "auth": auth,
        "mcp": "rmi-sentiment-oracle",
    }


# ═══════════════════════════════════════════════════
# MCP #8: CROSS-CHAIN STORY - AI traces multi-chain flows
# ═══════════════════════════════════════════════════
def trace_story(address: str, fp: str = "anon") -> dict:
    """AI traces fund flows across chains. 5 free/day. $0.12 premium."""
    auth = trial(fp, "story", 5)
    if auth["tier"] == "free_exhausted":
        return {"error": "Free exhausted", "upgrade": auth["upgrade"]}
    result = {"address": address}
    r = gredis()
    findings = []
    for chain in ["ethereum", "bsc", "polygon", "arbitrum", "optimism", "base"]:
        cached = r.get(f"rmi:label:{chain}:{address.lower()}")
        if cached:
            c = json.loads(cached)
            findings.append(f"{chain}: {c.get('label', '')} / {c.get('name_tag', '')}")
    prompt = f"""Trace this wallet across chains:\n\nActivity: {chr(10).join(findings) if findings else "No cross-chain labels found. Wallet may be single-chain or new."}\n\nNarrate: What is this entity doing across chains? Bridge user? Arbitrage? Laundering?"""
    story = ask(prompt, "qwen2.5-coder:7b", 200)
    result["chains_found"] = len(findings)
    result["ai_story"] = story
    result["ai_model"] = "qwen2.5-coder:7b"
    result["auth"] = auth
    result["mcp"] = "rmi-chain-story"
    return result


AI_MCP = {
    "rmi-contract-explain": explain_contract,
    "rmi-wallet-narrator": narrate_wallet,
    "rmi-rug-predictor": predict_rug,
    "rmi-news-tldr": news_tldr,
    "rmi-wallet-profiler": profile_wallet,
    "rmi-code-auditor": audit_code,
    "rmi-sentiment-oracle": sentiment_oracle,
    "rmi-chain-story": trace_story,
}
