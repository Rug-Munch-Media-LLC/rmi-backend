"""
RugMaps AI Analysis - DeepSeek Flash powered token investigation.
Uses our own data: FAISS indexes, wallet labels, clustering engine, RAG cache.
"""

import hashlib
import json
import os
import time

from app.core.logging import get_logger

logger = get_logger(__name__)

# LLM config - DeepSeek Flash for fast, cheap analysis
DEEPSEEK_KEY = (
    os.getenv("DEEPSEEK_API_KEY", "")
    or os.getenv("LLM_API_KEY", "")
    or os.getenv("OPENROUTER_API_KEY", "")
)
DEEPSEEK_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
AI_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")  # Flash by default

# Analysis cache (in-memory + Redis if available)
_analysis_cache: dict[str, dict] = {}
CACHE_TTL = 3600  # 1 hour


def _cache_key(token: str, chain: str) -> str:
    return hashlib.sha256(f"{chain}:{token}".encode()).hexdigest()[:16]


async def get_cached_analysis(token: str, chain: str) -> dict | None:
    """Check if we have a cached analysis for this token."""
    key = _cache_key(token, chain)
    # In-memory cache
    if key in _analysis_cache:
        entry = _analysis_cache[key]
        if time.time() - entry.get("_ts", 0) < CACHE_TTL:
            logger.info(f"RugMaps cache HIT for {token[:12]}...")
            return entry
    return None


def set_cached_analysis(token: str, chain: str, analysis: dict) -> None:
    """Cache analysis result."""
    key = _cache_key(token, chain)
    analysis["_ts"] = time.time()
    analysis["_cached"] = True
    _analysis_cache[key] = analysis
    # Prune old entries if memory grows
    if len(_analysis_cache) > 500:
        oldest = sorted(_analysis_cache.items(), key=lambda x: x[1].get("_ts", 0))[:100]
        for k, _ in oldest:
            del _analysis_cache[k]


async def get_token_holders(token_address: str, chain: str) -> list[dict]:
    """Get token holders from our data sources."""
    try:

        bm = get_rugmaps_engine()
        result = await bm.generate_map(center_wallet=token_address, depth=2, min_strength=0.1)
        d = result.to_dict()
        return d.get("nodes", [])
    except Exception as e:
        logger.warning(f"Holder fetch failed: {e}")
        return []


async def get_wallet_labels(addresses: list[str]) -> dict[str, dict]:
    """Get wallet labels from our ClickHouse/FAISS database."""
    labels = {}
    try:

        bm = get_rugmaps_engine()
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
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Label fetch error: {e}")
    return labels


async def search_similar_scams(token_address: str, chain: str) -> list[dict]:
    """Search FAISS indexes for similar scam patterns."""
    similar = []
    try:
        from app.ann_index import get_ann_index
        from app.crypto_embeddings import CryptoEmbedder

        ann = get_ann_index()
        embedder = CryptoEmbedder()

        # Embed token info
        query_text = f"token {token_address} on {chain} rug pull scam honeypot"
        embedding = await embedder.embed_query(query_text)

        # Search scam patterns index
        if "scam_patterns" in ann.indexes:
            results = ann.search("scam_patterns", embedding, k=5)
            for r in results:
                similar.append(
                    {
                        "pattern": r.get("metadata", {}).get("pattern", ""),
                        "description": r.get("metadata", {}).get("description", ""),
                        "similarity": float(r.get("score", 0)),
                    }
                )

        # Search known scams
        if "known_scams" in ann.indexes:
            results = ann.search("known_scams", embedding, k=5)
            for r in results:
                similar.append(
                    {
                        "scam_type": r.get("metadata", {}).get("type", ""),
                        "description": r.get("metadata", {}).get("description", ""),
                        "similarity": float(r.get("score", 0)),
                    }
                )

        # Search wallet profiles
        if "wallet_profiles" in ann.indexes:
            results = ann.search("wallet_profiles", embedding, k=5)
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

    # Deduplicate and sort by similarity
    seen = set()
    unique = []
    for s in sorted(similar, key=lambda x: x.get("similarity", 0), reverse=True):
        desc = s.get("description", "")
        if desc and desc not in seen:
            seen.add(desc)
            unique.append(s)

    return unique[:10]


async def generate_ai_analysis(
    token_address: str,
    chain: str,
    graph_data: dict,
    holders: list[dict],
    labels: dict[str, dict],
    similar_scams: list[dict],
) -> dict:
    """Generate AI analysis using DeepSeek Flash with our own data."""
    import httpx

    # Build a data-rich prompt from our own systems
    holder_summary = []
    for h in holders[:10]:
        addr = h.get("address", "")[:12]
        pct = h.get("supply_pct", h.get("pct", "?"))
        label = labels.get(h.get("address", ""), {}).get("entity", {}).get("name", "")
        risk = labels.get(h.get("address", ""), {}).get("risk_score", "?")
        holder_summary.append(
            f"  {addr}... - {pct}% supply - label: {label or 'unknown'} - risk: {risk}/100"
        )

    similar_summary = (
        "\n".join(
            [
                f"  - {s.get('description', '')[:100]} (similarity: {s.get('similarity', 0):.2f})"
                for s in similar_scams[:5]
            ]
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
{json.dumps({a[:12]: line.get("entity", {}).get("name") or line.get("entity", {}).get("label") for a, line in list(labels.items())[:10]})}

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

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a crypto security expert. Return only valid JSON.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 800,
                },
            )

            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")

                # Try to parse the JSON response
                try:
                    # Strip any markdown code blocks
                    if "```" in content:
                        content = content.split("```")[1]
                        if content.startswith("json"):
                            content = content[4:]
                    analysis = json.loads(content.strip())
                    return analysis
                except json.JSONDecodeError:
                    # Fallback: return raw text as summary
                    return {
                        "verdict": "UNKNOWN",
                        "risk_score": risk_score,
                        "summary": content[:300],
                        "red_flags": [],
                        "green_flags": [],
                        "recommendation": "Manual review recommended - AI response was malformed.",
                        "confidence": 30,
                    }

            logger.warning(f"DeepSeek API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"AI analysis failed: {e}")

    # Fallback analysis from our own data (no AI needed)
    return generate_fallback_analysis(
        risk_score, holders, labels, similar_scams, node_count, link_count
    )


def generate_fallback_analysis(
    risk_score: int,
    holders: list[dict],
    labels: dict[str, dict],
    similar_scams: list[dict],
    node_count: int,
    link_count: int,
) -> dict:
    """Generate analysis purely from our own data when AI is unavailable."""

    red_flags = []
    green_flags = []
    dev_wallets = []
    bundle_wallets = []

    # Check for high concentration
    top_holder_pct = sum(h.get("supply_pct", h.get("pct", 0)) for h in holders[:3])
    if top_holder_pct > 50:
        red_flags.append(
            f"Top 3 holders control {top_holder_pct:.0f}% of supply - extreme concentration risk"
        )
    elif top_holder_pct < 20:
        green_flags.append(f"Well distributed: top 3 holders only control {top_holder_pct:.0f}%")

    # Check labels
    high_risk_count = 0
    for addr, label_data in labels.items():
        entity = label_data.get("entity", {})
        risk = label_data.get("risk_score", 0)

        if entity.get("type") in ("scammer", "phishing", "exploiter"):
            red_flags.append(f"Known malicious entity: {entity.get('name', addr[:12])}")
            high_risk_count += 1

        if risk >= 70:
            red_flags.append(
                f"High risk wallet ({risk}/100): {addr[:12]}... {entity.get('name', '')}"
            )
            high_risk_count += 1
            dev_wallets.append(addr)

        if entity.get("type") == "exchange":
            green_flags.append(f"Exchange wallet detected: {entity.get('name', addr[:12])}")

    # Similar scam patterns
    if similar_scams:
        top_match = similar_scams[0]
        if top_match.get("similarity", 0) > 0.7:
            red_flags.append(f"Matches known pattern: {top_match.get('description', '')[:80]}")
    else:
        green_flags.append("No matches in our scam pattern database")

    # Risk assessment
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
            "DO NOT INVEST - clear scam indicators"
            if verdict == "SCAM"
            else "Extreme caution - multiple high-risk signals"
            if verdict == "HIGH_RISK"
            else "Proceed with caution - some suspicious patterns"
            if verdict == "SUSPICIOUS"
            else "No major concerns detected from our analysis"
        ),
        "confidence": confidence,
        "source": "rmi_data_engine",  # Indicates this came from our own data
    }
