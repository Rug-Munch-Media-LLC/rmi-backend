"""MCP tool catalog and versioning metadata (shared).

Extracted from ``app.mcp.server`` to break the import cycle:
``app.mcp.registry`` imports these constants, and ``app.mcp.server``
imports ``TOOL_CATEGORIES`` from ``app.mcp.registry``. Keeping the
pure-data catalog in its own module lets both depend on it without
importing each other at module load time.
"""

from __future__ import annotations

from typing import Any

# Tool catalog - inputSchema follows JSON Schema 2020-12
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
                "score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Risk score 0-100",
                },
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
                "subject_id": {
                    "type": "string",
                    "description": "chain:address, or 'all' for general news",
                },
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
                "paid_via_x402": {
                    "type": ["string", "null"],
                    "description": "x402 payment ref or null if free",
                },
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
                                "enum": [
                                    "SAME_AS",
                                    "FUNDED_BY_SAME",
                                    "CLONE_OF",
                                    "BEHAVIORAL_MATCH",
                                ],
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
                "api_key": {
                    "type": "string",
                    "description": "Admin API key (required — this tool is access-restricted)",
                },
                "params": {"type": "array", "items": {}, "default": []},
                "max_rows": {"type": "integer", "default": 1000, "maximum": 10000},
            },
            "required": ["sql", "api_key"],
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
                "sql": {
                    "type": "string",
                    "description": "SELECT query against eth-labels.db (SELECT only, no writes)",
                },
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
                "category": {
                    "type": "string",
                    "description": "filter by category (free, pro, enterprise, moat)",
                },
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
        "name": "parquet_query",
        "description": "Query blockchain data (transfers, swaps, deployments, native_transfers) from the R2 Parquet data lake via DuckDB httpfs. Supports Ethereum, BSC, Arbitrum, Polygon, and Base. Filter by address and block range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chain": {
                    "type": "string",
                    "enum": ["ethereum", "bsc", "arbitrum", "polygon", "base"],
                    "description": "Blockchain to query",
                },
                "table": {
                    "type": "string",
                    "enum": ["transfers", "swaps", "deployments", "native_transfers"],
                    "description": "Data table to query",
                },
                "address": {
                    "type": "string",
                    "description": "Optional address filter (from_address or to_address)",
                },
                "from_block": {
                    "type": "integer",
                    "description": "Optional starting block number (inclusive)",
                },
                "to_block": {
                    "type": "integer",
                    "description": "Optional ending block number (inclusive)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 100, max 1000)",
                },
            },
            "required": ["chain", "table"],
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "rows": {"type": "array"},
                "count": {"type": "integer"},
                "chain": {"type": "string"},
                "table": {"type": "string"},
                "truncated": {"type": "boolean"},
                "error": {"type": ["string", "null"]},
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
    "get_deployer_reputation": "2.0.0",  # M3 - Bayesian posterior
    "get_news_sentiment": "1.0.0",
    "generate_report": "2.0.0",  # M3 - RAG-grounded
    "query_catalog": "1.0.0",
    "find_similar_tokens": "1.0.0",
    "resolve_entity": "1.0.0",
    "analytics_query": "1.0.0",  # M3 moat TIER 1
    "eth_labels_query": "1.0.0",  # M3 moat TIER 2
    "eth_labels_stats": "1.0.0",  # M3 moat TIER 2
    "mcp_discover": "1.0.0",  # M3 moat TIER 1
    "parquet_query": "1.0.0",  # ported from stranded feat/scanners-p4-8-cont (R2 data lake)
    "status_check": "1.0.0",  # M3 moat TIER 1
}

TOOL_DEPRECATED: set[str] = set()  # empty - no deprecated tools yet
TOOL_SUCCESSORS: dict[str, str] = {}  # empty - no successor mappings yet

# Server version (single source of truth for /mcp/info)
MCP_SERVER_VERSION = "5.0.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
