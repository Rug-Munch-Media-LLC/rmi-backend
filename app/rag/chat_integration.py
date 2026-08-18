"""
RAG Chat Integration
====================
Augments every chat query with RAG context, source citations, web search,
and tool results. The central hub that makes the chatbot actually smart.

Flow:
  1. Detect intent (tool vs general)
  2. If tool intent: run tool + search RAG for relevant context about result
  3. If general: search RAG across news_v2 + scam_intel + wallet_labels
  4. Optionally: web search via SearXNG for current events
  5. Build system prompt with context + sources
  6. Call LLM with context
  7. Return answer with citations
"""

from __future__ import annotations

import asyncio
import json
import os
import textwrap
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("rag_chat")

# ── RAG collections to search (in priority order) ──
CHAT_RAG_COLLECTIONS = [
    "news_v2",  # Our ingested Telegram news sweep — the freshest content
    "scam_intel",  # Security patterns, scam knowledge
    "wallet_labels",  # Wallet labeling, entity resolution
    "scanned_tokens",  # Past token scan results
    "stablecoin_market",  # Market liquidity signals
]

MAX_RAG_RESULTS_PER_COLLECTION = 3
MAX_RAG_TOTAL_RESULTS = 12
RAG_CONTEXT_MAX_CHARS = 6000

# SearXNG (self-hosted, free, no rate limits)
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://172.26.0.2:8080")  # SearXNG on fleet-infra_searxng-net
SEARXNG_AUTO = os.getenv("SEARXNG_AUTO", "true").lower() == "true"

# LLM config — uses backup account by default
OPENROUTER_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# Use a capable free model for chat with context
CHAT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
CHAT_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"  # paid, but the backup account has it


async def search_rag(query: str) -> list[dict[str, Any]]:
    """Search our RAG knowledge base across all relevant collections.

    Returns unified, reranked results with source info for citations.
    """
    from app.rag._search import three_pillar_search

    all_hits: list[dict[str, Any]] = []
    seen_docs: set[str] = set()

    # Search each collection with the query
    tasks = []
    for collection in CHAT_RAG_COLLECTIONS:
        tasks.append(
            three_pillar_search(
                query=query,
                collection=collection,
                top_k=MAX_RAG_RESULTS_PER_COLLECTION,
                min_similarity=0.35,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, hits in enumerate(results):
        collection = CHAT_RAG_COLLECTIONS[idx]
        if isinstance(hits, Exception):
            logger.debug("rag_chat_search_failed collection=%s err=%s", collection, hits)
            continue
        for hit in hits:
            doc_id = hit.get("doc_id", "")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                hit["collection"] = collection
                all_hits.append(hit)

    # Sort by score descending, limit total
    all_hits.sort(key=lambda x: -x.get("score", 0))
    return all_hits[:MAX_RAG_TOTAL_RESULTS]


async def search_web(query: str) -> str:
    """Search the web via self-hosted SearXNG instance.

    Returns formatted text results or empty string if unavailable.
    """
    if not SEARXNG_AUTO:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "language": "en", "categories": "general,news"},
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            results = data.get("results", [])[:5]
            if not results:
                return ""
            parts = []
            for res in results:
                title = res.get("title", "")
                url = res.get("url", "")
                snippet = res.get("content", "")
                if title and snippet:
                    parts.append(f"• {title}\n  {snippet[:200]}\n  Source: {url}")
            return "\n\n".join(parts) if parts else ""
    except (httpx.HTTPError, httpx.TimeoutException, ConnectionRefusedError, OSError) as e:
        logger.debug("rag_chat_websearch_failed err=%s", e)
        return ""


async def chat_with_context(
    query: str,
    *,
    tool_result: str | None = None,
    intent: str | None = None,
    tier: str = "long",
) -> dict[str, Any]:
    """Answer a chat query with RAG context, web results, and optional tool output.

    Returns:
        {
            "answer": "...",
            "sources": [...],
            "rag_hits": [...],
            "has_web_results": bool,
            "has_tool_result": bool,
        }
    """
    # ── Step 1: Gather context in parallel ──
    rag_task = search_rag(query)
    web_task = search_web(query)
    rag_hits, web_text = await asyncio.gather(rag_task, web_task)

    # ── Step 2: Format context for the LLM ──
    context_parts = []
    sources = []

    # Tool result (structured data from price/scan/news tools)
    if tool_result:
        context_parts.append(f"[TOOL RESULT]\n{tool_result[:1500]}")
        sources.append({"type": "tool", "label": intent or "onchain data"})

    # RAG results
    if rag_hits:
        rag_ctx = []
        for hit in rag_hits:
            text = hit.get("text", "")[:500]
            coll = hit.get("collection", "kb")
            score = hit.get("score", 0)
            doc_id = hit.get("doc_id", "")[:20]
            rag_ctx.append(f"[{coll}] (score={score:.2f})\n{text}\n")
            sources.append({"type": "rag", "collection": coll, "doc_id": doc_id, "score": score})
        context_parts.append("[RAG KNOWLEDGE BASE]\n" + "\n".join(rag_ctx))

    # Web results
    if web_text:
        context_parts.append("[WEB SEARCH RESULTS]\n" + web_text)
        sources.append({"type": "web", "label": "web search"})

    # Truncate context to fit budget
    combined_context = "\n\n".join(context_parts)
    if len(combined_context) > RAG_CONTEXT_MAX_CHARS:
        combined_context = combined_context[:RAG_CONTEXT_MAX_CHARS] + "\n[...truncated]"

    # ── Step 3: Build system prompt with enterprise voice ──
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    system_prompt = textwrap.dedent(f"""\
    You are the RugMunch crypto intelligence assistant — an institutional-grade Web3 analyst.
    Today is {today}.

    RESPONSE FORMAT:
    - Begin with a concise, direct answer to the user's question (1-3 sentences).
    - Follow with structured details using bullet points or short paragraphs.
    - When citing on-chain data: include token address, chain, and confidence level.
    - When discussing risk: always flag the severity level (Critical/High/Medium/Low).
    - End with a forward-looking takeaway (1 sentence).

    CONTEXT RULES:
    - The context below comes from three sources:
      [RAG KNOWLEDGE BASE] = our ingested news, scam intel, and token analysis
      [TOOL RESULT] = live on-chain data from price/scan/news tools
      [WEB SEARCH RESULTS] = current web search results
    - Use ALL available context to form your answer.
    - Cite the source type where possible ("per our news feed", "on-chain data shows").
    - If RAG context is empty for a topic, do NOT fabricate data — offer to search.

    STYLE RULES:
    - Be direct and conversational. No fluff, no "delve", "tapestry", "robust", "however".
    - Use concrete numbers: percentages, dollar values, timestamps.
    - For token prices: include the 24h change and brief context.
    - For security/scam info: be clear about the risk and recommended action.

    CONTEXT:
    {combined_context}
    """)

    # ── Step 4: Call LLM ──
    from app.llm_config import chat_with_fallback

    answer = await chat_with_fallback(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        tier=tier,
    )

    # ── Step 5: Format response ──
    response = {
        "answer": answer,
        "sources": sources,
        "rag_hits": len(rag_hits),
        "has_web_results": bool(web_text),
        "has_tool_result": bool(tool_result),
    }
    return response


async def direct_chat(query: str, *, tier: str = "snappy") -> dict[str, Any]:
    """Fast, no-context chat for simple queries. No RAG, no tools, no web.

    Use this for greetings, simple Q&A, or when speed matters over depth.
    """
    from app.llm_config import chat_with_fallback

    answer = await chat_with_fallback(
        messages=[{"role": "user", "content": query}],
        tier=tier,
    )
    return {"answer": answer, "sources": [], "rag_hits": 0, "has_web_results": False, "has_tool_result": False}
