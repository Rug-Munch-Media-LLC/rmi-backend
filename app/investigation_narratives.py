"""
Investigation Narratives - Agentic multi-hop forensic tracing.

"Follow the money from this scam token 5 hops → tell me the story."
Combines multi-hop RAG retrieval, LLM planning, and narrative generation
to produce human-readable investigation reports.

Pipeline:
  1. LLM plans investigation hops (scam → deployer → funder → mixer → exit)
  2. Execute each hop via three_pillar_search + entity extraction
  3. Accumulate evidence across hops
  4. LLM synthesizes narrative with chronological flow
  5. Return structured report with confidence scoring
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── LLM config (shared with other RAG modules) ──────────────────
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
if os.getenv("LLM_API_KEY_B64"):
    import base64 as _b64

    os.environ["LLM_API_KEY"] = _b64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
AI_BASE = LLM_BASE_URL if LLM_API_KEY else "https://openrouter.ai/api/v1/chat/completions"
AI_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")

# ── Investigation templates ──────────────────────────────────────

HOP_TEMPLATES = {
    "rug_pull": [
        {
            "hop": "scam_identification",
            "goal": "Identify the scam token: deployer, creation time, initial liquidity",
        },
        {
            "hop": "deployer_trace",
            "goal": "Trace the deployer: other tokens deployed, wallet age, funding source",
        },
        {
            "hop": "funding_flow",
            "goal": "Follow the money: where did deployer get funds? CEX, mixer, or other scams?",
        },
        {
            "hop": "liquidity_exit",
            "goal": "Trace where the liquidity went after the rug: recipient wallets, DEX swaps",
        },
        {
            "hop": "cross_chain_escape",
            "goal": "Check if funds were bridged to other chains (Solana, BSC, Arbitrum, etc.)",
        },
        {
            "hop": "victim_impact",
            "goal": "Estimate victim count and total losses from on-chain data",
        },
    ],
    "honeypot": [
        {
            "hop": "contract_analysis",
            "goal": "Check contract for transfer restrictions, blacklist, trading pause",
        },
        {
            "hop": "deployer_history",
            "goal": "Has deployer created other honeypots? Check behavioral fingerprint",
        },
        {
            "hop": "victim_identification",
            "goal": "Find wallets that bought but couldn't sell - estimate losses",
        },
    ],
    "phishing": [
        {
            "hop": "site_analysis",
            "goal": "Identify the phishing site: domain age, hosting, similar domains",
        },
        {"hop": "wallet_drain", "goal": "Trace the drainer wallet: where did stolen funds go?"},
        {
            "hop": "drainer_network",
            "goal": "Is this drainer part of a known phishing ring? Check wallet clusters",
        },
    ],
    "general": [
        {
            "hop": "entity_lookup",
            "goal": "Find all known information about the target address/token",
        },
        {
            "hop": "transaction_analysis",
            "goal": "Analyze recent transactions: patterns, counterparties, unusual activity",
        },
        {
            "hop": "risk_assessment",
            "goal": "Synthesize risk signals from all available data sources",
        },
    ],
}


async def _plan_investigation(
    query: str,
    evidence_so_far: list[dict[str, Any]],
    available_templates: list[str],
) -> list[dict[str, str]]:
    """Use LLM to plan the next investigation hops based on evidence so far."""
    if not LLM_API_KEY:
        # Return default template
        return HOP_TEMPLATES.get("general", HOP_TEMPLATES["rug_pull"])

    context = json.dumps(
        {
            "query": query,
            "evidence_count": len(evidence_so_far),
            "last_findings": [e.get("summary", "")[:100] for e in evidence_so_far[-3:]],
        }
    )

    prompt = f"""You are a crypto forensics investigator. Based on the evidence so far,
plan the next investigation steps. Return a JSON array of {{"hop": "name", "goal": "what to investigate"}}.

Evidence so far:
{context}

Return ONLY the JSON array. Maximum 4 hops."""

    try:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                # Extract JSON array from response
                import re

                match = re.search(r"\[.*\]", content, re.DOTALL)
                if match:
                    return json.loads(match.group())
    except Exception as e:
        logger.debug(f"Investigation planning failed: {e}")

    return HOP_TEMPLATES.get("general", HOP_TEMPLATES["rug_pull"])


async def _execute_hop(
    hop: dict[str, str],
    query: str,
    collection: str = "known_scams",
) -> dict[str, Any]:
    """Execute one investigation hop via RAG search."""
    hop_query = f"{query} {hop.get('goal', '')}"
    hop_name = hop.get("hop", "unknown")

    try:
        from app.rag_service import three_pillar_search

        results = await three_pillar_search(
            query=hop_query,
            collections=[collection],
            limit=10,
            use_mmr=True,
            use_kg=True,
        )

        return {
            "hop": hop_name,
            "goal": hop.get("goal", ""),
            "query": hop_query,
            "results_count": len(results.get("results", [])),
            "results": results.get("results", [])[:5],  # Keep top 5 per hop
            "confidence": results.get("confidence", {}),
            "success": len(results.get("results", [])) > 0,
        }
    except Exception as e:
        logger.warning(f"Hop '{hop_name}' failed: {e}")
        return {
            "hop": hop_name,
            "goal": hop.get("goal", ""),
            "error": str(e),
            "success": False,
        }


async def _synthesize_narrative(
    query: str,
    hops: list[dict[str, Any]],
    cross_chain_matches: dict[str, Any] | None = None,
) -> str:
    """Use LLM to synthesize investigation hops into a narrative."""
    evidence = []
    for hop in hops:
        if hop.get("success"):
            results = hop.get("results", [])
            snippets = [r.get("content", "")[:200] for r in results[:3] if r.get("content")]
            evidence.append(f"[{hop['hop']}] {hop['goal']}: {'; '.join(snippets)}")
        else:
            evidence.append(f"[{hop['hop']}] FAILED: {hop.get('error', 'unknown error')}")

    if cross_chain_matches and cross_chain_matches.get("resolved"):
        cc = cross_chain_matches
        evidence.append(f"[CROSS_CHAIN] Found {cc.get('total_matches', 0)} matches on other chains")

    context = "\n".join(evidence)

    if not LLM_API_KEY and not OPENROUTER_KEY:
        return f"Investigation results for '{query}':\n\n{context}"

    prompt = f"""You are a crypto forensics analyst. Synthesize the following investigation
evidence into a clear, chronological narrative report. Use plain English. Include:

1. Summary: What happened? (2-3 sentences)
2. Timeline: Key events in order
3. Key entities: Addresses, tokens, exchanges involved
4. Fund flow: How the money moved
5. Risk assessment: How confident are these findings?
6. Recommendations: What should the user do?

Query: {query}

Evidence:
{context}

Write the report now:"""

    try:
        from app.llm_config import generate_content

        return await generate_content(
            prompt,
            system="You are a blockchain forensics investigator. Be precise, chronological, and evidence-based.",
            max_tokens=2048,
            temperature=0.4,
        )
    except Exception as e:
        return f"Narrative generation error: {e}\n\nRaw evidence:\n{context}"


# ── Main API ─────────────────────────────────────────────────────


async def investigate(
    query: str,
    investigation_type: str = "auto",
    max_hops: int = 5,
    collections: list[str] | None = None,
    cross_chain: bool = False,
    cross_chain_address: str = "",
    cross_chain_chain: str = "",
) -> dict[str, Any]:
    """
    Run a multi-hop forensic investigation.

    Parameters
    ----------
    query: The investigation query (address, token, or natural language)
    investigation_type: "rug_pull", "honeypot", "phishing", "general", or "auto"
    max_hops: Maximum investigation hops
    collections: RAG collections to search
    cross_chain: Enable cross-chain entity resolution
    cross_chain_address: Address for cross-chain resolution
    cross_chain_chain: Chain for cross-chain resolution

    Returns
    -------
    Dict with:
        - narrative: Full investigation report (LLM-synthesized)
        - hops: List of executed investigation hops
        - cross_chain: Cross-chain resolution results
        - confidence: Overall confidence score
        - evidence_count: Total evidence items gathered
    """
    import time

    start_time = time.time()

    if not collections:
        collections = [
            "known_scams",
            "forensic_reports",
            "scam_patterns",
            "wallet_profiles",
            "token_analysis",
        ]

    # Auto-detect investigation type
    if investigation_type == "auto":
        ql = query.lower()
        if any(w in ql for w in ["rug", "pull", "liquidity", "exit"]):
            investigation_type = "rug_pull"
        elif any(w in ql for w in ["honeypot", "cant sell", "transfer restriction"]):
            investigation_type = "honeypot"
        elif any(w in ql for w in ["phish", "drain", "fake", "impersonat"]):
            investigation_type = "phishing"
        else:
            investigation_type = "general"

    # Get investigation plan
    hops_plan = HOP_TEMPLATES.get(investigation_type, HOP_TEMPLATES["general"])[:max_hops]

    # Execute hops sequentially (each feeds context to the next)
    hops_executed = []
    for hop in hops_plan:
        result = await _execute_hop(hop, query, collections[0])
        hops_executed.append(result)
        if result.get("success"):
            # Feed evidence into next hop's query
            top_content = result.get("results", [{}])[0].get("content", "")
            if top_content:
                query += f" (related: {top_content[:80]})"

    # Cross-chain resolution
    cc_result = None
    if cross_chain and cross_chain_address:
        try:
            from app.cross_chain import resolve_cross_chain_identity

            cc_result = resolve_cross_chain_identity(
                address=cross_chain_address,
                chain=cross_chain_chain or "ethereum",
                funding_sources=[
                    {
                        "address": r.get("id", ""),
                        "chain": r.get("metadata", {}).get("chain", "ethereum"),
                        "amount": 0,
                        "hop_distance": 1,
                    }
                    for hop in hops_executed
                    for r in hop.get("results", [])[:3]
                ],
            )
        except Exception as e:
            logger.debug(f"Cross-chain resolution failed: {e}")

    # Synthesize narrative
    narrative = await _synthesize_narrative(query, hops_executed, cc_result)

    # Aggregate confidence
    confidence_scores = [
        h.get("confidence", {}).get("score", 0) for h in hops_executed if h.get("success") and h.get("confidence")
    ]
    avg_confidence = round(sum(confidence_scores) / len(confidence_scores)) if confidence_scores else 0

    elapsed = time.time() - start_time

    return {
        "query": query,
        "investigation_type": investigation_type,
        "narrative": narrative,
        "hops": [
            {
                "hop": h["hop"],
                "goal": h.get("goal", ""),
                "success": h.get("success", False),
                "results_count": h.get("results_count", 0),
            }
            for h in hops_executed
        ],
        "cross_chain": cc_result,
        "confidence": {
            "score": avg_confidence,
            "label": "HIGH" if avg_confidence > 60 else "MEDIUM" if avg_confidence > 30 else "LOW",
        },
        "evidence_count": sum(h.get("results_count", 0) for h in hops_executed),
        "elapsed_seconds": round(elapsed, 1),
        "hops_executed": len(hops_executed),
        "summary": narrative.split("\n")[0] if narrative else "Investigation complete",
    }
