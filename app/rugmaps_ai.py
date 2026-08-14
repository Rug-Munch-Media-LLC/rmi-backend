"""
RugMaps AI Analysis — DIRECT llama.cpp (local models, NO paid APIs).
Uses our own data: FAISS indexes, wallet labels, clustering engine, RAG cache.
Routing order: llama.cpp deepseek-analysis → llama.cpp qwen-chat → rule-based.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Direct llama.cpp config (NO litellm proxy, NO paid APIs) ──
# Backend runs in Docker on the rmi_network bridge (172.22.0.0/16); llama.cpp
# runs on the HOST, so the container reaches it via the docker bridge gateway
# 172.22.0.1 — NOT 127.0.0.1 (that's the container itself).
LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")
QWEN_PORT = 8085  # qwen-chat (Qwen3.5-4B-Abliterated, fast)
DEEPSEEK_PORT = 8086  # deepseek-analysis (Qwen3.5-9B-Abliterated, non-reasoning; legacy alias)
AI_MODEL = os.getenv("AI_ANALYSIS_MODEL", "deepseek-analysis")
# Fast model → token descriptions, simple labeling (Qwen3.5-4B llama.cpp :8085; Ollama fallback).
OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen3:4b")
# Complex model → cluster interpretation, anomaly detection, rug pull narratives
# (Qwen3.5-9B llama.cpp :8086; Ollama fallback — legacy tag).
OLLAMA_COMPLEX_MODEL = os.getenv("OLLAMA_COMPLEX_MODEL", "deepseek-r1:8b")
# Local inference is fast — 30s cap; anything slower means Ollama is wedged, fall back to paid.
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "30"))

# LiteLLM proxy + DeepSeek direct fallback (legacy callers; env-gated, empty = disabled)
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
DEEPSEEK_URL = f"http://{LLAMA_HOST}:{DEEPSEEK_PORT}/v1/chat/completions"
DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")
OLLAMA_BASE_URLS = [u for u in os.getenv("OLLAMA_BASE_URLS", "http://172.22.0.1:11434").split(",") if u]

# Analysis cache (in-memory + Redis if available)
_analysis_cache: dict[str, dict[str, Any]] = {}
CACHE_TTL = 3600  # 1 hour


def _cache_key(token: str, chain: str) -> str:
    return hashlib.sha256(f"{chain}:{token}".encode()).hexdigest()[:16]


async def get_cached_analysis(token: str, chain: str) -> dict[str, Any] | None:
    """Check if we have a cached analysis for this token."""
    key = _cache_key(token, chain)
    if key in _analysis_cache:
        entry = _analysis_cache[key]
        if time.time() - entry.get("_ts", 0) < CACHE_TTL:
            logger.info(f"RugMaps cache HIT for {token[:12]}...")
            return entry
    return None


def set_cached_analysis(token: str, chain: str, analysis: dict[str, Any]) -> None:
    """Cache analysis result."""
    key = _cache_key(token, chain)
    analysis["_ts"] = time.time()
    analysis["_cached"] = True
    _analysis_cache[key] = analysis
    if len(_analysis_cache) > 500:
        oldest = sorted(_analysis_cache.items(), key=lambda x: x[1].get("_ts", 0))[:100]
        for k, _ in oldest:
            del _analysis_cache[k]


async def get_token_holders(token_address: str, chain: str) -> list[dict[str, Any]]:
    """Get token holders from our data sources."""
    try:
        from app.domains.scanner.rugmaps import get_bubble_maps_pro

        bm = get_bubble_maps_pro()
        result = await bm.generate_map(center_wallet=token_address, depth=2, min_strength=0.1)
        d = result.to_dict()
        return d.get("nodes", [])
    except Exception as e:
        logger.warning(f"Holder fetch failed: {e}")
        return []


async def get_wallet_labels(addresses: list[str]) -> dict[str, dict[str, Any]]:
    """Get wallet labels from our ClickHouse/FAISS database."""
    labels = {}
    try:
        from app.domains.scanner.rugmaps import get_bubble_maps_pro

        bm = get_bubble_maps_pro()
        for addr in addresses[:50]:
            try:
                info = await bm._get_entity_info(addr)
                txs = await bm._get_transactions(addr)
                risk, level = bm._calculate_risk(txs, node_count=1)
                labels[addr] = {
                    "entity": info or {},
                    "risk_score": risk,
                    "risk_level": level,
                }
            except (ConnectionError, OSError, TimeoutError):
                pass
    except Exception as e:
        logger.warning(f"Label fetch error: {e}")
    return labels


async def search_similar_scams(token_address: str, chain: str) -> list[dict[str, Any]]:
    """Search FAISS indexes for similar scam patterns.

    DEPRECATED (FAISS → Qdrant): the app.rag v3 engine searches the Qdrant
    ``scams`` / ``scam_intel`` / ``threat_intel`` collections instead. To
    migrate, replace the body with::

        from app.rag.intelligence import RAGIntelligenceEnricher
        enricher = RAGIntelligenceEnricher(top_k=5)
        hits = await enricher.find_similar_scams(token_address, chain)

    Kept functional via the legacy in-process FAISS index for now.
    """
    similar = []
    try:
        from app.ann_index import get_ann_index
        from app.crypto_embeddings import CryptoEmbedder

        ann = get_ann_index()
        embedder = CryptoEmbedder()

        query_text = f"token {token_address} on {chain} rug pull scam honeypot"
        embedding = await embedder.embed_query(query_text)

        if "scam_patterns" in ann._indexes:
            results = await ann.search(embedding, "scam_patterns", limit=5)
            for r in results:
                similar.append(
                    {
                        "pattern": r.get("metadata", {}).get("pattern", ""),
                        "description": r.get("metadata", {}).get("description", ""),
                        "similarity": float(r.get("score", 0)),
                    }
                )

        if "known_scams" in ann._indexes:
            results = await ann.search(embedding, "known_scams", limit=5)
            for r in results:
                similar.append(
                    {
                        "scam_type": r.get("metadata", {}).get("type", ""),
                        "description": r.get("metadata", {}).get("description", ""),
                        "similarity": float(r.get("score", 0)),
                    }
                )

        if "wallet_profiles" in ann._indexes:
            results = await ann.search(embedding, "wallet_profiles", limit=5)
            for r in results:
                similar.append(
                    {
                        "wallet_type": r.get("metadata", {}).get("type", ""),
                        "description": r.get("metadata", {}).get("label", ""),
                        "similarity": float(r.get("score", 0)),
                    }
                )
    except Exception as e:
        logger.warning(f"FAISS search failed: {e}")

    seen = set()
    unique = []
    for s in sorted(similar, key=lambda x: x.get("similarity", 0), reverse=True):
        desc = s.get("description", "")
        if desc and desc not in seen:
            seen.add(desc)
            unique.append(s)
    return unique[:10]


# ── Core LiteLLM caller for RugMaps AI ─────────────────────────


async def _call_litellm_for_rugmaps(prompt: str, task_type: str = "analysis", max_tokens: int = 800) -> str | None:
    """Call LiteLLM proxy for RugMaps AI analysis.

    Args:
        prompt: The full analysis prompt with token data.
        task_type: "analysis" → deepseek-analysis (deep reasoning), "chat" → qwen-chat (fast).
        max_tokens: Max response tokens.

    Returns:
        Raw response text, or None on failure.
    """
    model = AI_MODEL if task_type == "analysis" else "qwen-chat"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{LITELLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {LITELLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a blockchain forensic expert. Return ONLY valid JSON, no markdown, no code blocks.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip()
            logger.warning("litellm_non_200", status=resp.status_code, body=resp.text[:200])
    except Exception as e:
        logger.warning("litellm_rugmaps_call_failed", error=str(e))
    return None


async def _call_deepseek_fallback(prompt: str, max_tokens: int = 800) -> str | None:
    """Fallback: call DeepSeek API directly if LiteLLM is unreachable."""
    if not DEEPSEEK_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a blockchain forensic expert. Return ONLY valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return content.strip()
    except Exception as e:
        logger.debug("deepseek_fallback_failed", error=str(e))
    return None


def _ollama_model_for_task(task_type: str) -> str:
    """Map a task type to the local Ollama model.

    - fast/chat/label/description → OLLAMA_FAST_MODEL (Qwen3.5-4B): cheap, fast
      labeling and token descriptions.
    - analysis/complex/anomaly/cluster (default) → OLLAMA_COMPLEX_MODEL
      (DeepSeek Distill Qwen-8B): cluster interpretation, anomaly detection,
      rug pull narratives.
    """
    if task_type in ("fast", "chat", "label", "description", "simple"):
        return OLLAMA_FAST_MODEL
    return OLLAMA_COMPLEX_MODEL


async def _call_ollama_for_rugmaps(
    prompt: str, task_type: str = "analysis", max_tokens: int = 800
) -> tuple[str | None, str]:
    """Call local Talos Ollama first (zero-cost, no external API).

    Tries every configured base URL in order. Returns ``(content, model_id)``;
    ``model_id`` is ``"ollama/<model>"`` (even on failure, so callers can log
    what was attempted).
    """
    model = _ollama_model_for_task(task_type)
    for base_url in OLLAMA_BASE_URLS:
        try:
            async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
                resp = await client.post(
                    f"{base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a blockchain forensic expert. "
                                    "Return ONLY valid JSON, no markdown, no code blocks."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.3, "num_predict": max_tokens},
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = (data.get("message", {}) or {}).get("content", "")
                    if content and content.strip():
                        content = content.strip()
                        logger.info("rugmaps_ai_ollama", model=model, base_url=base_url, task_type=task_type)
                        return content, f"ollama/{model}"
                else:
                    logger.warning(
                        "ollama_rugmaps_non_200", base_url=base_url, status=resp.status_code, body=resp.text[:200]
                    )
        except Exception as e:
            logger.warning("ollama_rugmaps_call_failed", base_url=base_url, error=str(e))
    return None, f"ollama/{model}"


async def _call_ai_with_ollama_priority(
    prompt: str, task_type: str = "analysis", max_tokens: int = 800
) -> tuple[str | None, str]:
    """Route RugMaps AI: local Ollama → LiteLLM → direct DeepSeek.

    Returns ``(content, model_id)`` where ``model_id`` is one of
    ``"ollama/<model>"``, ``"litellm/<model>"``, ``"deepseek/deepseek-chat"``
    or ``"none"`` — the caller logs it so we can track what served each response.
    """
    content, model = await _call_ollama_for_rugmaps(prompt, task_type=task_type, max_tokens=max_tokens)
    if content:
        return content, model

    content = await _call_litellm_for_rugmaps(prompt, task_type=task_type, max_tokens=max_tokens)
    if content:
        return content, f"litellm/{AI_MODEL}"

    content = await _call_deepseek_fallback(prompt, max_tokens=max_tokens)
    if content:
        return content, "deepseek/deepseek-chat"

    return None, "none"


def _parse_ai_json(content: str) -> dict[str, Any] | None:
    """Parse JSON from AI response, stripping markdown code blocks."""
    if not content:
        return None
    try:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        return json.loads(cleaned)
    except json.JSONDecodeError:
        import re

        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ── Holder Cluster Analysis (NEW — wired to LiteLLM) ──────────


async def analyze_holder_clusters(
    token_address: str,
    chain: str,
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """AI-powered wallet cluster analysis for suspicious patterns.

    Analyzes holder clusters for bundling, sniper coordination, wash trading,
    insider rings, and dev-linked wallet networks.
    """
    if not holders:
        return {"clusters": [], "suspicious_count": 0, "verdict": "UNKNOWN", "summary": "No holder data available."}

    # Filter out DEX/AMM/LP wallets from bundle analysis
    from app.domains.scanner.rugmaps.graph import is_dex_wallet

    filtered_holders = [h for h in holders if not is_dex_wallet(h.get("address", ""), h.get("label", ""))]
    dex_count = len(holders) - len(filtered_holders)

    holder_text = []
    for h in filtered_holders[:15]:
        addr = h.get("address", "")[:12]
        pct = h.get("supply_pct", h.get("pct", "?"))
        label = labels.get(h.get("address", ""), {}).get("entity", {}).get("name", "")
        risk = labels.get(h.get("address", ""), {}).get("risk_score", "?")
        holder_text.append(f"  {addr}... — {pct}% supply — label: {label or 'unknown'} — risk: {risk}/100")

    if dex_count > 0:
        holder_text.append(f"  [{dex_count} DEX/LP wallets excluded from analysis]")

    prompt = f"""You are RugMunch Intelligence's wallet cluster forensic analyst.

TOKEN: {token_address} on {chain}

WALLET CLUSTER DATA:
{chr(10).join(holder_text) if holder_text else "No holder data available"}

SUSPICIOUS PATTERNS TO IDENTIFY:
1. Bundled launches: multiple wallets funded from same source address
2. Sniper clusters: wallets buying in the same block with elevated gas
3. Wash trading rings: wallets trading tokens back and forth to fake volume
4. Insider coordination: wallets with identical buy/sell timing patterns
5. Dev-linked wallets: wallets showing funding linkage to deployer
6. Dormant activation: old wallets suddenly activated to trade this token

Return ONLY valid JSON with these fields:
{{
  "clusters": [
    {{
      "cluster_type": "<bundled|sniper|wash_trade|insider|dev_linked|dormant>",
      "wallet_count": <estimated number of wallets in cluster>,
      "supply_percentage": <estimated supply % controlled by cluster>,
      "confidence": <0-100>,
      "description": "<brief description of the suspicious pattern>"
    }}
  ],
  "suspicious_count": <number of clusters detected>,
  "verdict": "<CLEAN|MINOR_CONCERN|SUSPICIOUS|HIGHLY_COORDINATED>",
  "summary": "<2-3 sentence concise analysis of the cluster findings>"
}}"""

    content, ai_model = await _call_ai_with_ollama_priority(prompt, task_type="analysis", max_tokens=600)

    if content:
        parsed = _parse_ai_json(content)
        if parsed:
            parsed["ai_model"] = ai_model
            logger.info("rugmaps_ai_served", model=ai_model, task="holder_clusters")
            return parsed

    # Fallback: simple heuristic clustering
    result = _fallback_cluster_analysis(holders, labels)
    result["ai_model"] = "rule-based"
    return result


def _fallback_cluster_analysis(holders: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Rule-based cluster analysis when AI is unavailable."""
    clusters = []
    high_risk_addrs = [a for a, lb in labels.items() if lb.get("risk_score", 0) >= 70]
    bundled = [h for h in holders if h.get("bundled", False)]

    if bundled:
        clusters.append(
            {
                "cluster_type": "bundled",
                "wallet_count": len(bundled),
                "supply_percentage": sum(h.get("supply_pct", h.get("pct", 0)) for h in bundled),
                "confidence": 80,
                "description": f"{len(bundled)} wallets share funding source characteristics",
            }
        )
    if high_risk_addrs:
        clusters.append(
            {
                "cluster_type": "high_risk",
                "wallet_count": len(high_risk_addrs),
                "supply_percentage": sum(
                    next((h.get("supply_pct", h.get("pct", 0)) for h in holders if h.get("address", "") == a), 0)
                    for a in high_risk_addrs
                ),
                "confidence": 70,
                "description": f"{len(high_risk_addrs)} wallets flagged as high risk by our label system",
            }
        )

    suspicious_count = len(clusters)
    top_pct = sum(h.get("supply_pct", h.get("pct", 0)) for h in holders[:3])

    return {
        "clusters": clusters,
        "suspicious_count": suspicious_count,
        "verdict": "HIGHLY_COORDINATED"
        if suspicious_count >= 3
        else "SUSPICIOUS"
        if suspicious_count >= 1
        else "CLEAN",
        "summary": (
            f"Found {suspicious_count} suspicious wallet cluster(s). "
            f"Top 3 holders control {top_pct:.0f}% of supply. "
            + (
                "Extreme concentration — possible coordinated activity."
                if top_pct > 50
                else "Distribution appears normal."
            )
        ),
    }


# ── Contract Anomaly Detection (NEW — wired to LiteLLM) ───────


async def detect_contract_anomalies(
    token_address: str,
    chain: str,
    holder_data: dict[str, Any],
    scanner_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """AI-powered contract anomaly detection for rug pull indicators.

    Reviews token holder distribution, contract behavior, and on-chain patterns
    to identify rug pull indicators, honeypots, and manipulation.
    """
    holder_summary = []
    holders_list = holder_data.get("holders", holder_data.get("nodes", []))
    for h in holders_list[:10] if isinstance(holders_list, list) else []:
        addr = h.get("address", h.get("id", "?"))[:12]
        pct = h.get("pct", h.get("supply_pct", "?"))
        label = h.get("label", h.get("entity", "unknown"))
        holder_summary.append(f"  {addr}... — {pct}% — {label}")

    scanner_summary = ""
    if scanner_data and isinstance(scanner_data, dict):
        risk_score = scanner_data.get("risk_score", scanner_data.get("safety_score", "?"))
        risk_level = scanner_data.get("risk_level", "?")
        flags = scanner_data.get("risk_flags", [])
        lp = scanner_data.get("liquidity_usd", scanner_data.get("liquidity", "?"))
        sell_tax = scanner_data.get("tax_sell", scanner_data.get("sell_tax", 0))
        scanner_summary = f"""
SCANNER RESULTS:
- Risk score: {risk_score}/100 ({risk_level})
- Liquidity: ${lp}
- Sell tax: {sell_tax}%
- Risk flags: {json.dumps(flags) if flags else "None"}
"""

    prompt = f"""You are RugMunch Intelligence's smart-contract anomaly detection specialist.

TOKEN: {token_address} on {chain}

HOLDER DISTRIBUTION:
{chr(10).join(holder_summary) if holder_summary else "No holder data available"}

{scanner_summary}

RUG PULL INDICATORS TO CHECK:
1. Concentration risk: top holders controlling excessive supply (>50%)
2. Honeypot patterns: buy enabled but sell restricted
3. High sell tax: taxes that prevent profitable exits (>10%)
4. Unverified contract: source code hidden — cannot audit
5. Unlocked liquidity: LP tokens not burned or locked
6. Fresh wallet clusters: wallets created same day as token launch
7. Ownership retention: deployer retains mint/pause/blacklist privileges
8. Hidden mint functions: ability to create unlimited new tokens
9. Transfer pausability: owner can freeze all transfers
10. Wallet blacklisting: owner can block specific addresses from selling

Return ONLY valid JSON with these fields:
{{
  "anomalies": [
    {{
      "type": "<string label of the anomaly>",
      "severity": "<CRITICAL|HIGH|MEDIUM|LOW>",
      "description": "<detailed explanation of the finding>",
      "confidence": <0-100>
    }}
  ],
  "rug_pull_probability": <0-100>,
  "honeypot_likelihood": <0-100>,
  "verdict": "<SAFE|SUSPICIOUS|HIGH_RISK|LIKELY_RUG_PULL>",
  "summary": "<2-3 sentence concise analysis>",
  "recommendation": "<one sentence action recommendation>"
}}"""

    content, ai_model = await _call_ai_with_ollama_priority(prompt, task_type="analysis", max_tokens=800)

    if content:
        parsed = _parse_ai_json(content)
        if parsed:
            parsed["ai_model"] = ai_model
            logger.info("rugmaps_ai_served", model=ai_model, task="contract_anomalies")
            return parsed

    result = _fallback_anomaly_detection(holder_data, scanner_data)
    result["ai_model"] = "rule-based"
    return result


def _fallback_anomaly_detection(holder_data: dict[str, Any], scanner_data: dict[str, Any] | None) -> dict[str, Any]:
    """Rule-based anomaly detection when AI is unavailable."""
    anomalies: list[dict[str, Any]] = []
    rug_prob = 0
    honeypot_prob = 0

    holders_list = holder_data.get("holders", holder_data.get("nodes", []))
    if not isinstance(holders_list, list):
        holders_list = []

    top_pct = sum(h.get("pct", h.get("supply_pct", 0)) for h in holders_list[:3])
    if top_pct > 50:
        anomalies.append(
            {
                "type": "Concentration Risk",
                "severity": "CRITICAL" if top_pct > 80 else "HIGH",
                "description": f"Top 3 holders control {top_pct:.0f}% of supply",
                "confidence": 95,
            }
        )
        rug_prob += 30

    if scanner_data and isinstance(scanner_data, dict):
        sell_tax = scanner_data.get("tax_sell", scanner_data.get("sell_tax", 0))
        if isinstance(sell_tax, (int, float)) and sell_tax > 50:
            anomalies.append(
                {
                    "type": "High Sell Tax",
                    "severity": "CRITICAL",
                    "description": f"{sell_tax}% sell tax — effectively prevents selling",
                    "confidence": 95,
                }
            )
            rug_prob += 40
            honeypot_prob += 50
        elif isinstance(sell_tax, (int, float)) and sell_tax > 10:
            anomalies.append(
                {
                    "type": "Elevated Sell Tax",
                    "severity": "HIGH",
                    "description": f"{sell_tax}% sell tax — significantly above normal",
                    "confidence": 85,
                }
            )
            rug_prob += 15

        verif = scanner_data.get("contract_verified", scanner_data.get("verified", None))
        if verif is False:
            anomalies.append(
                {
                    "type": "Unverified Contract",
                    "severity": "HIGH",
                    "description": "Contract source code is not verified — hidden functionality possible",
                    "confidence": 95,
                }
            )
            rug_prob += 20

    return {
        "anomalies": anomalies,
        "rug_pull_probability": min(100, rug_prob),
        "honeypot_likelihood": min(100, honeypot_prob),
        "verdict": (
            "LIKELY_RUG_PULL"
            if rug_prob >= 70
            else "HIGH_RISK"
            if rug_prob >= 40
            else "SUSPICIOUS"
            if anomalies
            else "SAFE"
        ),
        "summary": f"Found {len(anomalies)} anomaly(s). "
        + (anomalies[0]["description"] if anomalies else "No concerning patterns detected."),
        "recommendation": (
            "DO NOT INVEST — strong rug pull indicators"
            if rug_prob >= 70
            else "Extreme caution — multiple red flags"
            if rug_prob >= 40
            else "Proceed with caution"
            if anomalies
            else "No action needed"
        ),
    }


# ── Unified Token Graph Analysis (existing — rewired to LiteLLM) ─


async def generate_ai_analysis(
    token_address: str,
    chain: str,
    graph_data: dict[str, Any],
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    similar_scams: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate AI analysis via local Ollama first, then paid fallback, then rule-based engine."""
    holder_summary = []
    for h in holders[:10]:
        addr = h.get("address", "")[:12]
        pct = h.get("supply_pct", h.get("pct", "?"))
        label = labels.get(h.get("address", ""), {}).get("entity", {}).get("name", "")
        risk = labels.get(h.get("address", ""), {}).get("risk_score", "?")
        holder_summary.append(f"  {addr}... — {pct}% supply — label: {label or 'unknown'} — risk: {risk}/100")

    similar_summary = (
        "\n".join(
            f"  - {s.get('description', '')[:100]} (similarity: {s.get('similarity', 0):.2f})"
            for s in similar_scams[:5]
        )
        or "No similar scam patterns found in our database."
    )

    graph_stats = graph_data.get("stats", {})
    node_count = graph_data.get("node_count", len(holders))
    link_count = graph_data.get("link_count", 0)
    risk_score = graph_data.get("risk_score", 0)

    prompt = f"""You are RugMunch Intelligence's AI security analyst. Analyze this token using our proprietary on-chain data.

TOKEN: {token_address} on {chain}

GRAPH DATA:
- {node_count} connected wallets
- {link_count} transaction links
- Graph risk score: {risk_score}/100
- Graph stats: {json.dumps(graph_stats)}

TOP HOLDERS (from our systems):
{chr(10).join(holder_summary) if holder_summary else "No holder data available"}

SIMILAR SCAM PATTERNS (from our FAISS vector database):
{similar_summary}

WALLET LABELS (from our 393K-label database):
{json.dumps({a[:12]: lb.get("entity", {}).get("name") or lb.get("entity", {}).get("label") for a, lb in list(labels.items())[:10]})}

Based on our data, provide a JSON analysis with these fields:
- "verdict": (SAFE / SUSPICIOUS / HIGH_RISK / SCAM)
- "risk_score": (0-100)
- "summary": (2-3 sentence analysis for users)
- "red_flags": (list of specific concerning findings, empty if none)
- "green_flags": (list of positive indicators, empty if none)
- "dev_wallets": (list of addresses likely belonging to developer/team)
- "bundle_wallets": (list of addresses in coordinated bundles)
- "recommendation": (one sentence: what should a user do?)
- "confidence": (0-100, how confident are you in this analysis?)

Return ONLY valid JSON, no markdown, no code blocks."""

    # Try local Ollama first, then LiteLLM, then direct DeepSeek
    content, ai_model = await _call_ai_with_ollama_priority(prompt, task_type="analysis", max_tokens=800)

    if content:
        parsed = _parse_ai_json(content)
        if parsed:
            parsed["ai_model"] = ai_model
            logger.info("rugmaps_ai_served", model=ai_model, task="token_graph")
            return parsed
        # Partial parse: use raw text as summary
        return {
            "verdict": "UNKNOWN",
            "risk_score": risk_score,
            "summary": content[:300],
            "red_flags": [],
            "green_flags": [],
            "dev_wallets": [],
            "bundle_wallets": [],
            "recommendation": "Manual review recommended — AI response was malformed.",
            "confidence": 30,
            "ai_model": ai_model,
        }

    logger.warning("AI analysis unavailable — using fallback engine")
    fallback = generate_fallback_analysis(risk_score, holders, labels, similar_scams, node_count, link_count)
    fallback["ai_model"] = "rule-based"
    return fallback


def generate_fallback_analysis(
    risk_score: int,
    holders: list[dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    similar_scams: list[dict[str, Any]],
    node_count: int,
    link_count: int,
) -> dict[str, Any]:
    """Generate analysis purely from our own data when AI is unavailable."""

    red_flags = []
    green_flags = []
    dev_wallets = []
    bundle_wallets = []

    top_holder_pct = sum(h.get("supply_pct", h.get("pct", 0)) for h in holders[:3])
    if top_holder_pct > 50:
        red_flags.append(f"Top 3 holders control {top_holder_pct:.0f}% of supply — extreme concentration risk")
    elif top_holder_pct < 20:
        green_flags.append(f"Well distributed: top 3 holders only control {top_holder_pct:.0f}%")

    high_risk_count = 0
    for addr, label_data in labels.items():
        entity = label_data.get("entity", {})
        risk = label_data.get("risk_score", 0)

        if entity.get("type") in ("scammer", "phishing", "exploiter"):
            red_flags.append(f"Known malicious entity: {entity.get('name', addr[:12])}")
            high_risk_count += 1

        if risk >= 70:
            red_flags.append(f"High risk wallet ({risk}/100): {addr[:12]}... {entity.get('name', '')}")
            high_risk_count += 1
            dev_wallets.append(addr)

        if entity.get("type") == "exchange":
            green_flags.append(f"Exchange wallet detected: {entity.get('name', addr[:12])}")

    if similar_scams:
        top_match = similar_scams[0]
        if top_match.get("similarity", 0) > 0.7:
            red_flags.append(f"Matches known pattern: {top_match.get('description', '')[:80]}")
    else:
        green_flags.append("No matches in our scam pattern database")

    if risk_score >= 80 or high_risk_count >= 3:
        verdict = "SCAM"
    elif risk_score >= 50 or high_risk_count >= 1:
        verdict = "HIGH_RISK"
    elif risk_score >= 25 or top_holder_pct > 40:
        verdict = "SUSPICIOUS"
    else:
        verdict = "SAFE"

    confidence = min(90, 40 + len(red_flags) * 10 + len(green_flags) * 5)

    return {
        "verdict": verdict,
        "risk_score": risk_score,
        "summary": f"Analysis based on {node_count} connected wallets across {link_count} transaction links. "
        f"Top holders control {top_holder_pct:.0f}% of supply. "
        f"{len(red_flags)} red flags, {len(green_flags)} green flags detected.",
        "red_flags": red_flags,
        "green_flags": green_flags,
        "dev_wallets": dev_wallets[:5],
        "bundle_wallets": bundle_wallets[:5],
        "recommendation": (
            "DO NOT INVEST — clear scam indicators"
            if verdict == "SCAM"
            else "Extreme caution — multiple high-risk signals"
            if verdict == "HIGH_RISK"
            else "Proceed with caution — some suspicious patterns"
            if verdict == "SUSPICIOUS"
            else "No major concerns detected from our analysis"
        ),
        "confidence": confidence,
        "source": "rmi_data_engine",
    }
