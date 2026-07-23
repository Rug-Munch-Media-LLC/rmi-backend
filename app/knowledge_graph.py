#!/usr/bin/env python3
"""
Knowledge Graph - Wallet → Token → Scam Relationship Engine
=============================================================
Builds and queries a knowledge graph from RAG document metadata.

Nodes:  wallet, token, contract, scam_pattern, entity, chain
Edges:  holds_token, deployed_by, involved_in, similar_to, flags, transfers_to

Powered by Redis for real-time graph storage + traversal.
Used by:
  - three_pillar_search() as enhanced entity lookup (Pillar 3+)
  - Relationship queries ("who is connected to this wallet?")
  - Forensic tracing (find paths between addresses)

Architecture:
  - Redis graph stored as adjacency lists (sorted sets with typed edges)
  - Edge types: HOLDS, DEPLOYED, INVOLVED_IN, SIMILAR_TO, FLAGS, TRANSFERS
  - Each edge has a weight (confidence 0-1) and metadata
  - Auto-built from document metadata during ingestion
  - Supports: neighbor queries, pathfinding (BFS up to depth 4), subgraph extraction

Key naming:
  kg:node:{type}:{id}           -> hash  {label, metadata_json}
  kg:edges:{type}:{id}           -> sorted set  member=edge_key,  score=weight
  kg:edge:{from_type}:{from_id}:{rel}:{to_type}:{to_id}  -> hash  {weight, metadata_json, created_at}
"""

import contextlib
import json
import os
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")

# ── Edge Types ────────────────────────────────────────────────────


class EdgeType:
    HOLDS = "holds"  # wallet holds token
    DEPLOYED = "deployed"  # wallet deployed contract/token
    INVOLVED_IN = "involved"  # wallet involved in scam/event
    SIMILAR_TO = "similar"  # token/wallet similar to another
    FLAGS = "flags"  # entity flags another as suspicious
    TRANSFERS = "transfers"  # wallet transfers to another wallet
    OWNS = "owns"  # wallet owns NFT/token
    MIGRATED = "migrated"  # token migrated to new contract
    ASSOCIATED = "associated"  # generic association


# ── Node Types ────────────────────────────────────────────────────


class NodeType:
    WALLET = "wallet"
    TOKEN = "token"
    CONTRACT = "contract"
    SCAM = "scam"
    ENTITY = "entity"  # named entity (protocol, person, org)
    CHAIN = "chain"
    ADDRESS = "address"  # generic blockchain address


# ── Knowledge Graph Engine ─────────────────────────────────────────


class KnowledgeGraph:
    """
    Redis-backed knowledge graph for crypto entity relationships.

    Stores nodes and edges in Redis for fast graph traversal.
    Auto-built from RAG document metadata during ingestion.
    """

    def __init__(self, redis: aioredis.Redis = None) -> None:
        self._redis = redis
        self._node_cache: dict[str, dict] = {}

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis:
            return self._redis
        self._redis = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
        return self._redis

    # ── Node Operations ───────────────────────────────────────────

    async def add_node(
        self,
        node_type: str,
        node_id: str,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add or update a node in the knowledge graph."""
        r = await self._get_redis()
        key = f"kg:node:{node_type}:{node_id}"
        data = {
            "label": label or node_id,
            "type": node_type,
            "metadata": json.dumps(metadata or {}),
            "updated_at": str(int(time.time())),
        }
        await r.hset(key, mapping=data)
        self._node_cache[f"{node_type}:{node_id}"] = data
        return key

    async def get_node(self, node_type: str, node_id: str) -> dict | None:
        """Get a node by type and ID."""
        r = await self._get_redis()
        key = f"kg:node:{node_type}:{node_id}"
        data = await r.hgetall(key)
        if not data:
            return None
        result = dict(data)
        if "metadata" in result and isinstance(result["metadata"], str):
            with contextlib.suppress(json.JSONDecodeError):
                result["metadata"] = json.loads(result["metadata"])
        return result

    # ── Edge Operations ────────────────────────────────────────────

    async def add_edge(
        self,
        from_type: str,
        from_id: str,
        relation: str,
        to_type: str,
        to_id: str,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add a directed edge between two nodes.
        Weight: 0.0-1.0 confidence/strength score.
        """
        r = await self._get_redis()
        edge_key = f"kg:edge:{from_type}:{from_id}:{relation}:{to_type}:{to_id}"

        edge_data = {
            "from_type": from_type,
            "from_id": from_id,
            "relation": relation,
            "to_type": to_type,
            "to_id": to_id,
            "weight": str(weight),
            "metadata": json.dumps(metadata or {}),
            "created_at": str(int(time.time())),
        }
        await r.hset(edge_key, mapping=edge_data)

        # Add to from-node's edge index (sorted set, score=weight)
        from_edge_key = f"kg:edges:{from_type}:{from_id}"
        await r.zadd(from_edge_key, {edge_key: weight})

        # Add reverse reference for to-node (for incoming edge queries)
        to_edge_key = f"kg:edges_in:{to_type}:{to_id}"
        await r.zadd(to_edge_key, {edge_key: weight})

        return edge_key

    async def get_neighbors(
        self,
        node_type: str,
        node_id: str,
        relation: str | None = None,
        direction: str = "outgoing",
        min_weight: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get neighboring nodes of a given node.

        Args:
            direction: 'outgoing' (default), 'incoming', or 'both'
            relation: filter by edge type (None = all)
            min_weight: minimum edge weight threshold
            limit: max results
        """
        r = await self._get_redis()
        results = []

        # Build list of direction prefixes to search (fixes Python ternary precedence bug)
        dirs = []
        if direction in ("outgoing", "both"):
            dirs.append(("kg:edges:", "outgoing"))
        if direction in ("incoming", "both"):
            dirs.append(("kg:edges_in:", "incoming"))
        for dir_key in dirs:
            prefix = dir_key[0]
            idx_key = f"{prefix}{node_type}:{node_id}"
            edge_keys = await r.zrevrangebyscore(idx_key, "+inf", min_weight)
            for ek in edge_keys[:limit]:
                edge_data = await r.hgetall(ek)
                if not edge_data:
                    continue
                if relation and edge_data.get("relation") != relation:
                    continue
                weight = float(edge_data.get("weight", 0))
                if weight < min_weight:
                    continue
                # Determine the neighbor
                if dir_key[1] == "outgoing":
                    neighbor_type = edge_data.get("to_type", "")
                    neighbor_id = edge_data.get("to_id", "")
                else:
                    neighbor_type = edge_data.get("from_type", "")
                    neighbor_id = edge_data.get("from_id", "")

                neighbor_node = await self.get_node(neighbor_type, neighbor_id)
                results.append(
                    {
                        "node_type": neighbor_type,
                        "node_id": neighbor_id,
                        "label": neighbor_node.get("label", neighbor_id)
                        if neighbor_node
                        else neighbor_id,
                        "relation": edge_data.get("relation", ""),
                        "weight": weight,
                        "direction": dir_key[1],
                        "metadata": neighbor_node.get("metadata", {}) if neighbor_node else {},
                        "edge_metadata": json.loads(edge_data.get("metadata", "{}")),
                    }
                )
                if len(results) >= limit:
                    return results

        return results

    async def find_paths(
        self,
        from_type: str,
        from_id: str,
        to_type: str,
        to_id: str,
        max_depth: int = 4,
        max_paths: int = 5,
    ) -> list[list[dict]]:
        """
        BFS pathfinding between two nodes.
        Returns up to max_paths shortest paths.
        """
        r = await self._get_redis()
        paths = []

        # BFS
        queue = [(from_type, from_id, [])]
        visited = {(from_type, from_id)}

        while queue and len(paths) < max_paths:
            curr_type, curr_id, path_so_far = queue.pop(0)

            if len(path_so_far) >= max_depth:
                continue

            # Get all edges from this node
            idx_key = f"kg:edges:{curr_type}:{curr_id}"
            edge_keys = await r.zrevrangebyscore(idx_key, "+inf", "0")

            for ek in edge_keys:
                edge_data = await r.hgetall(ek)
                if not edge_data:
                    continue

                neighbor_type = edge_data.get("to_type", "")
                neighbor_id = edge_data.get("to_id", "")

                if (neighbor_type, neighbor_id) in visited:
                    continue

                step = {
                    "from_type": curr_type,
                    "from_id": curr_id,
                    "relation": edge_data.get("relation", ""),
                    "to_type": neighbor_type,
                    "to_id": neighbor_id,
                    "weight": float(edge_data.get("weight", 0)),
                }

                new_path = [*path_so_far, step]

                # Check if we reached the target
                if neighbor_type == to_type and neighbor_id == to_id:
                    paths.append(new_path)
                    continue

                visited.add((neighbor_type, neighbor_id))
                queue.append((neighbor_type, neighbor_id, new_path))

        return paths

    async def get_subgraph(
        self,
        node_type: str,
        node_id: str,
        depth: int = 2,
        max_nodes: int = 100,
    ) -> dict[str, Any]:
        """
        Extract a subgraph around a node for visualization/analysis.
        Returns nodes and edges for graph rendering.
        """
        nodes = {}
        edges = []
        visited = set()
        queue = [(node_type, node_id, 0)]

        while queue and len(nodes) < max_nodes:
            curr_type, curr_id, curr_depth = queue.pop(0)

            if (curr_type, curr_id) in visited:
                continue
            if curr_depth > depth:
                continue

            visited.add((curr_type, curr_id))
            node_data = await self.get_node(curr_type, curr_id)
            nodes[f"{curr_type}:{curr_id}"] = {
                "type": curr_type,
                "id": curr_id,
                "label": node_data.get("label", curr_id) if node_data else curr_id,
                "metadata": node_data.get("metadata", {}) if node_data else {},
            }

            # Get neighbors
            neighbors = await self.get_neighbors(curr_type, curr_id, direction="outgoing", limit=20)
            for n in neighbors:
                edge = {
                    "from": f"{curr_type}:{curr_id}",
                    "to": f"{n['node_type']}:{n['node_id']}",
                    "relation": n["relation"],
                    "weight": n["weight"],
                }
                edges.append(edge)

                if (n["node_type"], n["node_id"]) not in visited:
                    queue.append((n["node_type"], n["node_id"], curr_depth + 1))

        return {
            "center": f"{node_type}:{node_id}",
            "nodes": nodes,
            "edges": edges,
            "depth": depth,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        }

    # ── Ingestion from RAG Metadata ────────────────────────────────

    async def ingest_rag_document(
        self,
        collection: str,
        doc_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> int:
        """
        Auto-extract graph relationships from a RAG document's metadata.
        Returns number of edges created.
        """
        edges_created = 0

        # Common metadata fields for crypto documents
        address = metadata.get("address", "")
        chain = metadata.get("chain", "")
        name = metadata.get("name", "")
        symbol = metadata.get("symbol", "")
        labels = metadata.get("labels", [])
        severity = metadata.get("severity", "")
        source = metadata.get("source", "")

        # ── Wallet profiles ──
        if collection == "wallet_profiles" and address:
            await self.add_node(
                NodeType.WALLET,
                address,
                label=name or address[:10] + "...",
                metadata={"chain": chain, "labels": labels, "source": source},
            )

            # Wallet holds tokens (if metadata has token info)
            held_tokens = metadata.get("tokens", [])
            for token in held_tokens if isinstance(held_tokens, list) else []:
                token_addr = token.get("address", "") or token.get("mint", "")
                if token_addr:
                    await self.add_node(
                        NodeType.TOKEN, token_addr, label=token.get("symbol", token_addr[:10])
                    )
                    await self.add_edge(
                        NodeType.WALLET,
                        address,
                        EdgeType.HOLDS,
                        NodeType.TOKEN,
                        token_addr,
                        weight=float(token.get("amount_usd", 0))
                        / max(float(metadata.get("balance_usd", 1)), 1)
                        or 0.5,
                        metadata={"symbol": token.get("symbol", ""), "chain": chain},
                    )
                    edges_created += 1

            # Labels indicate involvement in scams/protocols
            if isinstance(labels, list):
                for label in labels:
                    label_str = str(label).lower()
                    scam_keywords = [
                        "scam",
                        "rug",
                        "honeypot",
                        "phish",
                        "drainer",
                        "exploit",
                        "hack",
                    ]
                    if any(kw in label_str for kw in scam_keywords):
                        await self.add_node(
                            NodeType.SCAM,
                            label_str,
                            label=label_str,
                            metadata={"type": "label", "source": source},
                        )
                        await self.add_edge(
                            NodeType.WALLET,
                            address,
                            EdgeType.INVOLVED_IN,
                            NodeType.SCAM,
                            label_str,
                            weight=0.8,
                            metadata={"label": label_str, "chain": chain},
                        )
                        edges_created += 1

        # ── Token analysis ──
        elif collection == "token_analysis" and address:
            await self.add_node(
                NodeType.TOKEN,
                address,
                label=f"{name} ({symbol})" if name else address[:10],
                metadata={"chain": chain, "symbol": symbol, "source": source},
            )

            # Deployer info
            deployer = metadata.get("deployer", metadata.get("creator", ""))
            if deployer:
                await self.add_node(NodeType.WALLET, deployer, label=deployer[:10] + "...")
                await self.add_edge(
                    NodeType.WALLET,
                    deployer,
                    EdgeType.DEPLOYED,
                    NodeType.TOKEN,
                    address,
                    weight=1.0,
                    metadata={"chain": chain, "role": "deployer"},
                )
                edges_created += 1

            # Scam flags
            if severity in ("high", "critical"):
                scam_label = f"{symbol or address[:8]} scam"
                await self.add_node(
                    NodeType.SCAM,
                    scam_label,
                    label=scam_label,
                    metadata={"severity": severity, "type": "flagged_token"},
                )
                await self.add_edge(
                    NodeType.TOKEN,
                    address,
                    EdgeType.FLAGS,
                    NodeType.SCAM,
                    scam_label,
                    weight=0.9,
                    metadata={"severity": severity},
                )
                edges_created += 1

        # ── Known scams / scam patterns ──
        elif collection in ("known_scams", "scam_patterns"):
            scam_id = doc_id
            await self.add_node(
                NodeType.SCAM,
                scam_id,
                label=name or metadata.get("name", scam_id),
                metadata={"severity": severity, "type": collection, "source": source},
            )

            # Associated addresses
            for addr in metadata.get("addresses", []):
                addr_str = str(addr)
                if addr_str:
                    await self.add_node(NodeType.ADDRESS, addr_str, label=addr_str[:10] + "...")
                    await self.add_edge(
                        NodeType.ADDRESS,
                        addr_str,
                        EdgeType.INVOLVED_IN,
                        NodeType.SCAM,
                        scam_id,
                        weight=0.7,
                        metadata={"role": "associated"},
                    )
                    edges_created += 1

            # Associated tokens
            for token in metadata.get("tokens", []):
                token_addr = token.get("address", "") if isinstance(token, dict) else str(token)
                if token_addr:
                    await self.add_node(NodeType.TOKEN, token_addr, label=token_addr[:10])
                    await self.add_edge(
                        NodeType.SCAM,
                        scam_id,
                        EdgeType.FLAGS,
                        NodeType.TOKEN,
                        token_addr,
                        weight=0.8,
                        metadata={"severity": severity},
                    )
                    edges_created += 1

        # ── Forensic reports ──
        elif collection == "forensic_reports":
            report_id = doc_id
            await self.add_node(
                NodeType.ENTITY,
                report_id,
                label=name or f"Report {report_id[:8]}",
                metadata={"type": "forensic_report", "severity": severity},
            )

            # Connect mentioned addresses
            for addr in metadata.get("addresses", metadata.get("wallets", [])):
                addr_str = str(addr)
                if addr_str:
                    await self.add_node(NodeType.ADDRESS, addr_str, label=addr_str[:10] + "...")
                    await self.add_edge(
                        NodeType.ADDRESS,
                        addr_str,
                        EdgeType.INVOLVED_IN,
                        NodeType.ENTITY,
                        report_id,
                        weight=0.6,
                    )
                    edges_created += 1

        # ── Contract audits ──
        elif collection == "contract_audits" and address:
            await self.add_node(
                NodeType.CONTRACT,
                address,
                label=name or address[:10],
                metadata={"chain": chain, "source": source, "severity": severity},
            )

            # Auditor
            auditor = metadata.get("auditor", "")
            if auditor:
                await self.add_node(
                    NodeType.ENTITY, auditor, label=auditor, metadata={"type": "auditor"}
                )
                await self.add_edge(
                    NodeType.ENTITY,
                    auditor,
                    EdgeType.ASSOCIATED,
                    NodeType.CONTRACT,
                    address,
                    weight=0.5,
                    metadata={"role": "auditor"},
                )

        return edges_created

    # ── Search Enhancement ──────────────────────────────────────────

    async def expand_query_entities(
        self,
        entities: list[dict[str, str]],
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        """
        Given extracted entities, expand them via graph traversal.
        Returns related entities and their relationships.

        This is used by Pillar 3 (entity lookup) in three_pillar_search
        to find not just the exact entity but its graph neighborhood.
        """
        expanded = []

        for entity in entities:
            entity_type = entity.get("type", "address")
            entity_value = entity.get("value", "")

            # Map entity types to graph node types
            node_type_map = {
                "evm_address": NodeType.WALLET,
                "solana_address": NodeType.WALLET,
                "token_symbol": NodeType.TOKEN,
                "chain_name": NodeType.CHAIN,
                "protocol_name": NodeType.ENTITY,
            }
            graph_type = node_type_map.get(entity_type, NodeType.ADDRESS)

            # Check if this entity exists in the graph
            node = await self.get_node(graph_type, entity_value)
            if not node:
                # Also try wallet/address types
                for alt_type in [
                    NodeType.WALLET,
                    NodeType.TOKEN,
                    NodeType.CONTRACT,
                    NodeType.ADDRESS,
                ]:
                    node = await self.get_node(alt_type, entity_value)
                    if node:
                        graph_type = alt_type
                        break

            if node:
                # Get neighbors
                neighbors = await self.get_neighbors(
                    graph_type,
                    entity_value,
                    direction="both",
                    limit=20,
                )
                for n in neighbors:
                    expanded.append(
                        {
                            "doc_id": f"kg:{n['node_type']}:{n['node_id']}",
                            "id": f"kg:{n['node_type']}:{n['node_id']}",
                            "score": n.get("weight", 0.5),
                            "content": f"{n['label']} ({n['relation']} {n['direction']})",
                            "pillars": ["entity"],
                            "match_type": "knowledge_graph",
                            "relation": n["relation"],
                            "neighbor_type": n["node_type"],
                            "neighbor_id": n["node_id"],
                        }
                    )

        return expanded

    # ── Statistics ──────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics."""
        r = await self._get_redis()

        # Count nodes by type
        node_counts = {}
        for ntype in [
            NodeType.WALLET,
            NodeType.TOKEN,
            NodeType.CONTRACT,
            NodeType.SCAM,
            NodeType.ENTITY,
            NodeType.CHAIN,
            NodeType.ADDRESS,
        ]:
            pattern = f"kg:node:{ntype}:*"
            keys = []
            async for key in r.scan_iter(match=pattern, count=500):
                keys.append(key)
            node_counts[ntype] = len(keys)

        # Count edges
        edge_keys = []
        async for key in r.scan_iter(match="kg:edge:*", count=500):
            edge_keys.append(key)

        return {
            "nodes": node_counts,
            "total_nodes": sum(node_counts.values()),
            "total_edges": len(edge_keys),
            "node_types": list(node_counts.keys()),
        }


# ── Singleton ──────────────────────────────────────────────────────

_kg_instance: KnowledgeGraph | None = None


async def get_knowledge_graph() -> KnowledgeGraph:
    """Get or create the singleton KnowledgeGraph instance."""
    global _kg_instance
    if _kg_instance is None:
        _kg_instance = KnowledgeGraph()
    return _kg_instance


async def build_graph_from_rag(
    collections: list[str] | None = None,
    max_per_collection: int = 10000,
) -> dict[str, int]:
    """
    One-time build: scan RAG documents and build knowledge graph edges.
    Called on startup or as a background job.

    Returns: {"edges_created": N, "nodes_created": M, "collections": {...}}
    """

    if collections is None:
        collections = [
            "wallet_profiles",
            "token_analysis",
            "known_scams",
            "scam_patterns",
            "forensic_reports",
            "market_intel",
            "contract_audits",
            "transaction_patterns",
        ]

    kg = await get_knowledge_graph()
    r = await kg._get_redis()  # Reuse singleton's Redis connection
    total_edges = 0
    total_nodes = 0
    coll_stats = {}

    for coll in collections:
        idx_key = f"rag:idx:{coll}"
        doc_ids = await r.smembers(idx_key)
        if not doc_ids:
            coll_stats[coll] = {"docs": 0, "edges": 0}
            continue

        sample = list(doc_ids)[:max_per_collection]
        edges = 0

        # Batch fetch documents
        batch_size = 500
        for i in range(0, len(sample), batch_size):
            batch = sample[i : i + batch_size]
            keys = [f"rag:{coll}:{did}" for did in batch]
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()

            for did, data in zip(batch, raw_docs, strict=False):
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                    content = doc.get("content", "")
                    metadata = doc.get("metadata", {}) or {}
                    doc_edges = await kg.ingest_rag_document(
                        collection=coll,
                        doc_id=did,
                        content=content,
                        metadata=metadata,
                    )
                    edges += doc_edges
                except (json.JSONDecodeError, Exception) as e:
                    logger.debug(f"KG ingest failed for {coll}/{did}: {e}")

        total_edges += edges
        total_nodes += len(sample)  # approximate
        coll_stats[coll] = {"docs": len(sample), "edges": edges}
        logger.info(f"KG built for {coll}: {len(sample)} docs, {edges} edges")

    # Connection is managed by the singleton - do not close here

    return {
        "nodes_created": total_nodes,
        "edges_created": total_edges,
        "collections": coll_stats,
    }
