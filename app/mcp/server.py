"""T33 MCP Server - exposes 8 tools to AI agents at mcp.rugmunch.io.

Per v4.0 §T33. JSON-RPC over SSE (the protocol Claude/Cursor speak).

Tools (per v4.0):
  1. get_token_risk        - Real-time risk score (FREE 5/day or $0.01)
  2. get_wallet_analysis   - Wallet activity + reputation
  3. get_deployer_reputation - Deployer reputation (0-100)
  4. get_news_sentiment    - Latest news + sentiment
  5. generate_report       - Full AI research report ($5)
  6. query_catalog         - Natural language catalog query
  7. find_similar_tokens   - Vector-similar tokens
  8. resolve_entity        - Cross-chain entity resolution

Backend implementations: app/catalog/* + app/domain/reports/generator.py
"""

from __future__ import annotations
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# Tool catalog + versioning metadata now live in app.mcp._catalog to break
# the registry <-> server import cycle (registry needs TOOL_CATALOG at module
# load; server needs TOOL_CATEGORIES from registry — lazily, at runtime).
from app.mcp._catalog import (  # noqa: E402 — imported after logger init; kept below module header for readability
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_VERSION,
    TOOL_CATALOG,
    TOOL_DEPRECATED,
    TOOL_SUCCESSORS,
    TOOL_VERSIONS,
)


# ── Tool implementations ──────────────────────────────────────────
async def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tool call to the appropriate backend."""
    from app.catalog.service import get_catalog

    catalog = get_catalog()
    await catalog._init_stores()

    if name == "get_token_risk":
        from app.catalog.models import Chain

        try:
            c = Chain(arguments["chain"])
        except ValueError:
            return {"error": f"unknown chain: {arguments['chain']}"}
        result = await catalog.get_token_risk(c, arguments["address"])
        return {"result": result, "tier": "free_or_pro"}

    if name == "get_wallet_analysis":
        from app.catalog.models import Chain

        try:
            c = Chain(arguments["chain"])
        except ValueError:
            return {"error": f"unknown chain: {arguments['chain']}"}
        w = await catalog.get_wallet(c, arguments["address"])
        if not w:
            return {"error": "wallet not found in catalog"}
        return {"result": w.model_dump(mode="json")}

    if name == "get_deployer_reputation":
        from app.catalog.models import Chain

        try:
            c = Chain(arguments["chain"])
        except ValueError:
            return {"error": f"unknown chain: {arguments['chain']}"}
        w = await catalog.get_wallet(c, arguments["address"])
        if not w:
            return {"error": "deployer wallet not found", "reputation_score": 50}
        # Compute reputation deterministically
        from app.catalog.models import Deployer
        from app.catalog.reputation import compute_deployer_reputation

        deployer = Deployer(
            wallet_id=w.wallet_id,
            chain=w.chain,
            address=w.address,
            first_seen=w.first_seen,
            last_seen=w.last_seen,
            tx_count=w.tx_count,
            total_volume_usd=w.total_volume_usd,
            is_deployer=True,
            reputation_score=w.reputation_score,
            deployments=getattr(w, "deployments", []),
            rug_count=getattr(w, "rug_count", 0),
        )
        score = await compute_deployer_reputation(deployer, catalog)
        return {"result": {"reputation_score": score, "tier": _tier_from_score(score)}}

    if name == "get_news_sentiment":
        subject = arguments.get("subject_id", "all")
        since = int(arguments.get("since_hours", 24))
        limit = int(arguments.get("limit", 10))
        if not catalog._health.postgres:
            return {"error": "postgres unavailable", "articles": []}
        try:
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT news_id, title, summary, source, published_at, sentiment_score
                       FROM news_items
                       WHERE published_at > NOW() - ($1 || ' hours')::interval
                       ORDER BY published_at DESC LIMIT $2""",
                    str(since),
                    limit,
                )
            articles = [
                {
                    "news_id": r["news_id"],
                    "title": r["title"],
                    "summary": (r["summary"] or "")[:200],
                    "source": r["source"],
                    "published_at": r["published_at"].isoformat(),
                    "sentiment_score": r["sentiment_score"],
                }
                for r in rows
            ]
            avg_sent = sum(a["sentiment_score"] or 0 for a in articles) / max(1, len(articles))
            return {
                "result": {
                    "subject": subject,
                    "article_count": len(articles),
                    "avg_sentiment": round(avg_sent, 3),
                    "articles": articles,
                }
            }
        except Exception as e:
            return {"error": f"news_query_fail: {e}"}

    if name == "generate_report":
        from app.domains.reports.generator import generate_token_report, generate_wallet_report

        chain, address = arguments["subject_id"].split(":", 1)
        try:
            if arguments["subject_type"] == "token":
                report = await generate_token_report(catalog, chain, address)
            else:
                report = await generate_wallet_report(catalog, chain, address)
            from app.domains.reports.generator import save_report

            await save_report(catalog, report)
            return {
                "result": {
                    "report_id": report.report_id,
                    "risk_score": report.risk_score,
                    "risk_tier": report.risk_tier.value,
                    "markdown": report.to_markdown(),
                    "paid_via_x402": None,  # MCP doesn't enforce payment in v1
                }
            }
        except Exception as e:
            return {"error": f"report_fail: {e}"}

    if name == "query_catalog":
        # NL query -> RAG search
        q = arguments.get("query", "")
        hits = await catalog.rag_search(query=q, top_k=5)
        return {"result": {"query": q, "hits": hits, "count": len(hits)}}

    if name == "find_similar_tokens":
        from app.catalog.models import Chain

        try:
            c = Chain(arguments["chain"])
        except ValueError:
            return {"error": f"unknown chain: {arguments['chain']}"}
        # Use token's rag_embedding_id to find similar via Qdrant
        token = await catalog.get_token(c, arguments["address"])
        if not token or not token.rag_embedding_id:
            return {"error": "token not in catalog or no RAG embedding", "similar": []}
        # Use RAG to search for similar by querying with the token's content
        rag_hits = await catalog.rag_search(query=token.symbol or "token", top_k=int(arguments.get("limit", 10)))
        return {"result": {"subject": arguments["address"], "similar": rag_hits[:10]}}

    if name == "resolve_entity":
        result = await catalog.resolve_entity(arguments["wallet_id"])
        return {"result": result}

    # ── TIERS 1-2 moat tools (June 23-24 2026) ───────────────────
    if name == "analytics_query":
        # T13: Run read-only SQL via embedded DuckDB
        # TODO: M3 moat TIER 2 - add API key check before opening to public
        from app.core.duckdb_analytics import DuckDBAnalytics

        sql = arguments.get("sql", "").strip()
        if not sql:
            return {"error": "sql parameter required"}
        # Safety: only allow SELECT / WITH statements, no writes
        sql_upper = sql.upper().lstrip()
        if not (sql_upper.startswith(("SELECT", "WITH", "SHOW", "DESCRIBE"))):
            return {"error": "only SELECT/WITH/SHOW/DESCRIBE queries are allowed"}
        max_rows = min(int(arguments.get("max_rows", 1000)), 10000)
        params = arguments.get("params", [])
        try:
            d = DuckDBAnalytics()
            rows = d.query(sql, params=params, max_rows=max_rows)
            return {
                "result": {"rows": rows, "count": len(rows), "truncated": len(rows) >= max_rows},
                "engine": "duckdb",
            }
        except Exception as exc:
            return {"error": f"duckdb query failed: {exc}"}

    if name == "eth_labels_query":
        # T22: Query eth-labels.db SQLite with SELECT-only safety
        sql = arguments.get("sql", "").strip()
        if not sql:
            return {"error": "sql parameter required"}

        # Safety: only allow SELECT statements
        sql_upper = sql.upper().lstrip()
        if not sql_upper.startswith("SELECT"):
            return {"error": "only SELECT queries allowed against eth-labels.db"}

        limit = min(int(arguments.get("limit", 1000)), 10000)

        try:
            from app.mcp.tools.eth_labels_tool import query_eth_labels_db_mcp

            result = await query_eth_labels_db_mcp(sql, limit)
            return {"result": result}
        except Exception as e:
            return {"error": f"eth_labels_query failed: {e!s}"}

    if name == "eth_labels_stats":
        # T22: Get statistics about eth-labels.db
        try:
            from app.mcp.tools.eth_labels_tool import get_eth_labels_stats_mcp

            result = await get_eth_labels_stats_mcp()
            return {"result": result}
        except Exception as e:
            return {"error": f"eth_labels_stats failed: {e!s}"}

    if name == "mcp_discover":
        # MCP catalog discovery with versioning + deprecation + category filtering
        from app.mcp.registry import TOOL_CATEGORIES  # lazy: avoid circular import

        include_deprecated = arguments.get("include_deprecated", False)
        category = arguments.get("category")
        tools = []
        for tool in TOOL_CATALOG:
            name = tool["name"]
            if not include_deprecated and name in TOOL_DEPRECATED:
                continue
            if category and category not in TOOL_CATEGORIES.get(name, []):
                continue
            entry = {
                "name": name,
                "version": TOOL_VERSIONS.get(name, "1.0.0"),
                "description": tool.get("description", ""),
                "input_schema": tool.get("inputSchema", {}),
                "deprecated": name in TOOL_DEPRECATED,
                "successor": TOOL_SUCCESSORS.get(name),
            }
            tools.append(entry)
        return {
            "result": {
                "server": "rugmunch-intelligence",
                "server_version": MCP_SERVER_VERSION,
                "protocol_version": MCP_PROTOCOL_VERSION,
                "tool_count": len(tools),
                "tools": tools,
            }
        }

    if name == "status_check":
        # M3 moat TIER 1 - unified health check across all subsystems
        # Use the async path directly (we're in an event loop already)
        from app.core.health import run_health_checks

        health = await run_health_checks()
        return {"result": health}

    return {"error": f"unknown tool: {name}"}


def _tier_from_score(score: int) -> str:
    if score < 25:
        return "low"
    if score < 50:
        return "medium"
    if score < 75:
        return "high"
    return "critical"
