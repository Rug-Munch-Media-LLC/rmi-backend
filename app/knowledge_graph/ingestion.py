"""
Knowledge Graph - RAG Document Ingestion
=========================================
Auto-extract graph relationships from RAG document metadata during
ingestion. Builds nodes and edges for wallets, tokens, scams, etc.
"""

import json
import logging
from typing import Any

from app.knowledge_graph.graph import KnowledgeGraph
from app.knowledge_graph.models import EdgeType, NodeType

logger = logging.getLogger(__name__)


async def ingest_rag_document(
    kg: KnowledgeGraph,
    collection: str, doc_id: str, content: str,
    metadata: dict[str, Any],
) -> int:
    """
    Auto-extract graph relationships from a RAG document's metadata.
    Returns number of edges created.
    """
    edges_created = 0
    _ = content  # May be used in future for content-based extraction

    address = metadata.get("address", "")
    chain = metadata.get("chain", "")
    name = metadata.get("name", "")
    symbol = metadata.get("symbol", "")
    labels = metadata.get("labels", [])
    severity = metadata.get("severity", "")
    source = metadata.get("source", "")

    # ── Wallet profiles ──
    if collection == "wallet_profiles" and address:
        await kg.add_node(
            NodeType.WALLET, address,
            label=name or address[:10] + "...",
            metadata={"chain": chain, "labels": labels, "source": source},
        )

        held_tokens = metadata.get("tokens", [])
        for token in held_tokens if isinstance(held_tokens, list) else []:
            token_addr = token.get("address", "") or token.get("mint", "")
            if token_addr:
                await kg.add_node(
                    NodeType.TOKEN, token_addr,
                    label=token.get("symbol", token_addr[:10]),
                )
                await kg.add_edge(
                    NodeType.WALLET, address, EdgeType.HOLDS,
                    NodeType.TOKEN, token_addr,
                    weight=float(token.get("amount_usd", 0))
                    / max(float(metadata.get("balance_usd", 1)), 1) or 0.5,
                    metadata={"symbol": token.get("symbol", ""), "chain": chain},
                )
                edges_created += 1

        if isinstance(labels, list):
            for label in labels:
                label_str = str(label).lower()
                scam_keywords = ["scam", "rug", "honeypot", "phish", "drainer", "exploit", "hack"]
                if any(kw in label_str for kw in scam_keywords):
                    await kg.add_node(
                        NodeType.SCAM, label_str, label=label_str,
                        metadata={"type": "label", "source": source},
                    )
                    await kg.add_edge(
                        NodeType.WALLET, address, EdgeType.INVOLVED_IN,
                        NodeType.SCAM, label_str, weight=0.8,
                        metadata={"label": label_str, "chain": chain},
                    )
                    edges_created += 1

    # ── Token analysis ──
    elif collection == "token_analysis" and address:
        await kg.add_node(
            NodeType.TOKEN, address,
            label=f"{name} ({symbol})" if name else address[:10],
            metadata={"chain": chain, "symbol": symbol, "source": source},
        )

        deployer = metadata.get("deployer", metadata.get("creator", ""))
        if deployer:
            await kg.add_node(NodeType.WALLET, deployer, label=deployer[:10] + "...")
            await kg.add_edge(
                NodeType.WALLET, deployer, EdgeType.DEPLOYED,
                NodeType.TOKEN, address, weight=1.0,
                metadata={"chain": chain, "role": "deployer"},
            )
            edges_created += 1

        if severity in ("high", "critical"):
            scam_label = f"{symbol or address[:8]} scam"
            await kg.add_node(
                NodeType.SCAM, scam_label, label=scam_label,
                metadata={"severity": severity, "type": "flagged_token"},
            )
            await kg.add_edge(
                NodeType.TOKEN, address, EdgeType.FLAGS,
                NodeType.SCAM, scam_label, weight=0.9,
                metadata={"severity": severity},
            )
            edges_created += 1

    # ── Known scams / scam patterns ──
    elif collection in ("known_scams", "scam_patterns"):
        await kg.add_node(
            NodeType.SCAM, doc_id,
            label=name or metadata.get("name", doc_id),
            metadata={"severity": severity, "type": collection, "source": source},
        )

        for addr in metadata.get("addresses", []):
            addr_str = str(addr)
            if addr_str:
                await kg.add_node(NodeType.ADDRESS, addr_str, label=addr_str[:10] + "...")
                await kg.add_edge(
                    NodeType.ADDRESS, addr_str, EdgeType.INVOLVED_IN,
                    NodeType.SCAM, doc_id, weight=0.7,
                    metadata={"role": "associated"},
                )
                edges_created += 1

        for token in metadata.get("tokens", []):
            token_addr = token.get("address", "") if isinstance(token, dict) else str(token)
            if token_addr:
                await kg.add_node(NodeType.TOKEN, token_addr, label=token_addr[:10])
                await kg.add_edge(
                    NodeType.SCAM, doc_id, EdgeType.FLAGS,
                    NodeType.TOKEN, token_addr, weight=0.8,
                    metadata={"severity": severity},
                )
                edges_created += 1

    # ── Forensic reports ──
    elif collection == "forensic_reports":
        await kg.add_node(
            NodeType.ENTITY, doc_id,
            label=name or f"Report {doc_id[:8]}",
            metadata={"type": "forensic_report", "severity": severity},
        )

        for addr in metadata.get("addresses", metadata.get("wallets", [])):
            addr_str = str(addr)
            if addr_str:
                await kg.add_node(NodeType.ADDRESS, addr_str, label=addr_str[:10] + "...")
                await kg.add_edge(
                    NodeType.ADDRESS, addr_str, EdgeType.INVOLVED_IN,
                    NodeType.ENTITY, doc_id, weight=0.6,
                )
                edges_created += 1

    # ── Contract audits ──
    elif collection == "contract_audits" and address:
        await kg.add_node(
            NodeType.CONTRACT, address,
            label=name or address[:10],
            metadata={"chain": chain, "source": source, "severity": severity},
        )

        auditor = metadata.get("auditor", "")
        if auditor:
            await kg.add_node(
                NodeType.ENTITY, auditor, label=auditor,
                metadata={"type": "auditor"},
            )
            await kg.add_edge(
                NodeType.ENTITY, auditor, EdgeType.ASSOCIATED,
                NodeType.CONTRACT, address, weight=0.5,
                metadata={"role": "auditor"},
            )

    return edges_created


async def expand_query_entities(
    kg: KnowledgeGraph,
    entities: list[dict[str, str]],
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """
    Given extracted entities, expand them via graph traversal.
    Used by Pillar 3 (entity lookup) in three_pillar_search.
    """
    expanded = []

    for entity in entities:
        entity_type = entity.get("type", "address")
        entity_value = entity.get("value", "")

        node_type_map = {
            "evm_address": NodeType.WALLET,
            "solana_address": NodeType.WALLET,
            "token_symbol": NodeType.TOKEN,
            "chain_name": NodeType.CHAIN,
            "protocol_name": NodeType.ENTITY,
        }
        graph_type = node_type_map.get(entity_type, NodeType.ADDRESS)

        node = await kg.get_node(graph_type, entity_value)
        if not node:
            for alt_type in [NodeType.WALLET, NodeType.TOKEN, NodeType.CONTRACT, NodeType.ADDRESS]:
                node = await kg.get_node(alt_type, entity_value)
                if node:
                    graph_type = alt_type
                    break

        if node:
            neighbors = await kg.get_neighbors(graph_type, entity_value, direction="both", limit=20)
            for n in neighbors:
                expanded.append({
                    "doc_id": f"kg:{n['node_type']}:{n['node_id']}",
                    "id": f"kg:{n['node_type']}:{n['node_id']}",
                    "score": n.get("weight", 0.5),
                    "content": f"{n['label']} ({n['relation']} {n['direction']})",
                    "pillars": ["entity"],
                    "match_type": "knowledge_graph",
                    "relation": n["relation"],
                    "neighbor_type": n["node_type"],
                    "neighbor_id": n["node_id"],
                })

    return expanded


async def build_graph_from_rag(
    kg: KnowledgeGraph,
    redis_conn: Any,
    collections: list[str] | None = None,
    max_per_collection: int = 10000,
) -> dict[str, int]:
    """
    One-time build: scan RAG documents and build knowledge graph edges.
    Called on startup or as a background job.
    """
    if collections is None:
        collections = [
            "wallet_profiles", "token_analysis", "known_scams",
            "scam_patterns", "forensic_reports", "market_intel",
            "contract_audits", "transaction_patterns",
        ]

    total_edges = 0
    total_nodes = 0
    coll_stats = {}

    for coll in collections:
        idx_key = f"rag:idx:{coll}"
        doc_ids = await redis_conn.smembers(idx_key)
        if not doc_ids:
            coll_stats[coll] = {"docs": 0, "edges": 0}
            continue

        sample = list(doc_ids)[:max_per_collection]
        edges = 0

        batch_size = 500
        for i in range(0, len(sample), batch_size):
            batch = sample[i:i + batch_size]
            keys = [f"rag:{coll}:{did}" for did in batch]
            pipe = redis_conn.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()

            for did, data in zip(batch, raw_docs, strict=False):
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                    content = doc.get("content", "")
                    meta = doc.get("metadata", {}) or {}
                    doc_edges = await ingest_rag_document(
                        kg, collection=coll, doc_id=did,
                        content=content, metadata=meta,
                    )
                    edges += doc_edges
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug(f"KG ingest failed for {coll}/{did}: {e}")

        total_edges += edges
        total_nodes += len(sample)
        coll_stats[coll] = {"docs": len(sample), "edges": edges}
        logger.info(f"KG built for {coll}: {len(sample)} docs, {edges} edges")

    return {
        "nodes_created": total_nodes,
        "edges_created": total_edges,
        "collections": coll_stats,
    }
