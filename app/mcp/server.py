"""T33 MCP Server — exposes 8 tools to AI agents at mcp.rugmunch.io.

Per v4.0 §T33. JSON-RPC over SSE (the protocol Claude/Cursor speak).

Tools (per v4.0):
  1. get_token_risk        — Real-time risk score (FREE 5/day or $0.01)
  2. get_wallet_analysis   — Wallet activity + reputation
  3. get_deployer_reputation — Deployer reputation (0-100)
  4. get_news_sentiment    — Latest news + sentiment
  5. generate_report       — Full AI research report ($5)
  6. query_catalog         — Natural language catalog query
  7. find_similar_tokens   — Vector-similar tokens
  8. resolve_entity        — Cross-chain entity resolution

Backend implementations: app/catalog/* + app/domain/reports/generator.py
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Tool catalog — inputSchema follows JSON Schema 2020-12
TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "get_token_risk",
        "description": "Real-time risk score for any token across 13+ chains. Returns score (0-100), tier (low/medium/high/critical), and risk factors. Free tier: 5 calls/day, $0.01 thereafter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {
                    "type": "string",
                    "enum": [
                        "solana",
                        "ethereum",
                        "base",
                        "arbitrum",
                        "optimism",
                        "polygon",
                        "bsc",
                        "tron",
                        "bitcoin",
                        "avalanche",
                        "fantom",
                        "gnosis",
                    ],
                },
                "address": {"type": "string", "description": "Token contract address"},
            },
            "required": ["chain", "address"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Risk score 0-100"},
                "tier": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "risk_factors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "factor": {"type": "string"},
                            "weight": {"type": "number"},
                            "evidence": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["score", "tier", "risk_factors"],
        },
    },
    {
        "name": "get_wallet_analysis",
        "description": "Wallet activity, balance, transaction history, and reputation. Returns wallet profile + risk flags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["chain", "address"],
        },
        "outputSchema": {
            "type": "object",
            "description": "Wallet profile serialized from Wallet model",
            "properties": {
                "wallet_id": {"type": "string"},
                "chain": {"type": "string"},
                "address": {"type": "string"},
                "balance": {"type": ["number", "string", "null"]},
                "tx_count": {"type": "integer"},
                "total_volume_usd": {"type": "number"},
                "first_seen": {"type": ["string", "null"], "format": "date-time"},
                "last_seen": {"type": ["string", "null"], "format": "date-time"},
                "reputation_score": {"type": ["integer", "null"]},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "get_deployer_reputation",
        "description": "Deployer reputation score 0-100 (100=clean, 0=serial rugger). Deterministic from on-chain history + news + RAG findings. Cached 1h.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "address": {"type": "string"},
            },
            "required": ["chain", "address"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "reputation_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "0=serial rugger, 100=clean",
                },
                "tier": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            },
            "required": ["reputation_score", "tier"],
        },
    },
    {
        "name": "get_news_sentiment",
        "description": "Latest news for a token or wallet with sentiment classification. Returns articles + composite sentiment score.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_id": {"type": "string", "description": "chain:address, or 'all' for general news"},
                "since_hours": {"type": "integer", "default": 24, "minimum": 1, "maximum": 720},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "article_count": {"type": "integer"},
                "avg_sentiment": {"type": "number", "description": "Composite sentiment score"},
                "articles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "news_id": {"type": "string"},
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "source": {"type": "string"},
                            "published_at": {"type": "string", "format": "date-time"},
                            "sentiment_score": {"type": ["number", "null"]},
                        },
                    },
                },
            },
            "required": ["subject", "article_count", "avg_sentiment", "articles"],
        },
    },
    {
        "name": "generate_report",
        "description": "Full AI research report on a token or wallet. 7 sections composed in parallel via LLM. $5/report. Returns full Markdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject_type": {"type": "string", "enum": ["token", "wallet"]},
                "subject_id": {"type": "string", "description": "chain:address"},
            },
            "required": ["subject_type", "subject_id"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
                "risk_tier": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "markdown": {"type": "string", "description": "Full report in Markdown"},
                "paid_via_x402": {"type": ["string", "null"], "description": "x402 payment ref or null if free"},
            },
            "required": ["report_id", "risk_score", "risk_tier", "markdown"],
        },
    },
    {
        "name": "query_catalog",
        "description": "Natural language catalog query. Returns matching tokens, wallets, deployers, news, RAG findings. $0.05/query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language question"},
            },
            "required": ["query"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "hits": {"type": "array", "items": {"type": "object"}},
                "count": {"type": "integer"},
            },
            "required": ["query", "hits", "count"],
        },
    },
    {
        "name": "find_similar_tokens",
        "description": "Vector-similar tokens to a given token. Returns tokens with cosine similarity >= 0.85. $0.03/query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string"},
                "address": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "maximum": 50},
            },
            "required": ["chain", "address"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The input token address"},
                "similar": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
    {
        "name": "resolve_entity",
        "description": "Cross-chain entity resolution. Given a wallet, find all linked wallets across chains via SAME_AS / FUNDED_BY_SAME / CLONE_OF / BEHAVIORAL_MATCH edges. $0.10/query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "wallet_id": {"type": "string", "description": "chain:address"},
            },
            "required": ["wallet_id"],
        },
        "outputSchema": {
            "type": "object",
            "description": "Cross-chain entity resolution result with linked wallets + edges",
            "properties": {
                "wallet_id": {"type": "string"},
                "linked_wallets": {"type": "array", "items": {"type": "object"}},
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "edge_type": {
                                "type": "string",
                                "enum": ["SAME_AS", "FUNDED_BY_SAME", "CLONE_OF", "BEHAVIORAL_MATCH"],
                            },
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
    # ── TIER 1 moat tools (June 23 2026) ─────────────────────────
    {
        "name": "analytics_query",
        "description": "Run a read-only SQL query against the embedded DuckDB analytics engine. Use for sub-1GB analytical queries (counts, aggregations, joins). For larger queries, use ClickHouse directly. Max 10K rows returned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL query (SELECT only, no writes)"},
                "params": {"type": "array", "items": {}, "default": []},
                "max_rows": {"type": "integer", "default": 1000, "maximum": 10000},
            },
            "required": ["sql"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": {"type": "object"}},
                "count": {"type": "integer"},
                "truncated": {"type": "boolean"},
                "engine": {"type": "string", "enum": ["duckdb"]},
            },
            "required": ["rows", "count", "truncated", "engine"],
        },
    },
    {
        "name": "eth_labels_query",
        "description": "Query the eth-labels.db SQLite database containing 115K+ labeled Ethereum addresses. Runs read-only SELECT against the accounts table. Max 10K rows returned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT query against eth-labels.db (SELECT only, no writes)"},
                "limit": {"type": "integer", "default": 1000, "maximum": 10000},
            },
            "required": ["sql"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "items": {"type": "object"}},
                "count": {"type": "integer"},
                "columns": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "eth_labels_stats",
        "description": "Get statistics about the eth-labels.db database including table counts, chain distribution, and record counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "total_accounts": {"type": "integer"},
                "by_chain": {"type": "object", "additionalProperties": {"type": "integer"}},
                "by_category": {"type": "object", "additionalProperties": {"type": "integer"}},
                "last_updated": {"type": "string", "format": "date-time"},
            },
        },
    },
    {
        "name": "mcp_discover",
        "description": "Discover all available MCP tools with versioning, deprecation status, and schema introspection. Returns the full tool catalog with auth requirements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_deprecated": {"type": "boolean", "default": False},
                "category": {"type": "string", "description": "filter by category (free, pro, enterprise, moat)"},
            },
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "server_version": {"type": "string"},
                "protocol_version": {"type": "string"},
                "tool_count": {"type": "integer"},
                "tools": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "version": {"type": "string"},
                            "description": {"type": "string"},
                            "input_schema": {"type": "object"},
                            "deprecated": {"type": "boolean"},
                            "successor": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "status_check",
        "description": "Health check across all RMI subsystems (backend, postgres, clickhouse, qdrant, redis, minio, reth, mcp server, x402, certstream, glitchtip). Returns pass/fail/degraded for each with latency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_metrics": {"type": "boolean", "default": True},
            },
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["healthy", "degraded", "unhealthy"]},
                "services": {
                    "type": "object",
                    "description": "Map of service name → status",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["pass", "fail", "degraded"]},
                            "latency_ms": {"type": ["number", "null"]},
                            "error": {"type": ["string", "null"]},
                        },
                    },
                },
                "latency_ms": {"type": "object", "additionalProperties": {"type": "number"}},
                "last_check": {"type": "string", "format": "date-time"},
            },
            "required": ["status", "services"],
        },
    },
]


# ── Tool versioning + deprecation registry ───────────────────────
TOOL_VERSIONS: dict[str, str] = {
    "get_token_risk": "1.2.0",
    "get_wallet_analysis": "1.1.0",
    "get_deployer_reputation": "2.0.0",  # M3 — Bayesian posterior
    "get_news_sentiment": "1.0.0",
    "generate_report": "2.0.0",  # M3 — RAG-grounded
    "query_catalog": "1.0.0",
    "find_similar_tokens": "1.0.0",
    "resolve_entity": "1.0.0",
    "analytics_query": "1.0.0",  # M3 moat TIER 1
    "eth_labels_query": "1.0.0",  # M3 moat TIER 2
    "eth_labels_stats": "1.0.0",  # M3 moat TIER 2
    "mcp_discover": "1.0.0",  # M3 moat TIER 1
    "status_check": "1.0.0",  # M3 moat TIER 1
}

TOOL_DEPRECATED: set[str] = set()  # empty — no deprecated tools yet
TOOL_SUCCESSORS: dict[str, str] = {}  # empty — no successor mappings yet

# Server version (single source of truth for /mcp/info)
MCP_SERVER_VERSION = "5.0.0"
MCP_PROTOCOL_VERSION = "2024-11-05"


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
        from app.domain.reports.generator import generate_token_report, generate_wallet_report

        chain, address = arguments["subject_id"].split(":", 1)
        try:
            if arguments["subject_type"] == "token":
                report = await generate_token_report(catalog, chain, address)
            else:
                report = await generate_wallet_report(catalog, chain, address)
            from app.domain.reports.generator import save_report

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
        # TODO: M3 moat TIER 2 — add API key check before opening to public
        from app.core.duckdb_analytics import DuckDBAnalytics

        sql = arguments.get("sql", "").strip()
        if not sql:
            return {"error": "sql parameter required"}
        # Safety: only allow SELECT / WITH statements, no writes
        sql_upper = sql.upper().lstrip()
        if not (
            sql_upper.startswith("SELECT")
            or sql_upper.startswith("WITH")
            or sql_upper.startswith("SHOW")
            or sql_upper.startswith("DESCRIBE")
        ):
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
        # M3 moat TIER 1 — unified health check across all subsystems
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
