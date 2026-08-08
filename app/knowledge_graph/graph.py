"""
Knowledge Graph - Core Graph Operations
========================================
Redis-backed directed graph with adjacency lists, pathfinding,
subgraph extraction, and maintenance operations.

Key naming:
  kg:node:{type}:{id}           -> hash  {label, metadata_json}
  kg:edges:{type}:{id}           -> sorted set  member=edge_key, score=weight
  kg:edge:{from_type}:{from_id}:{rel}:{to_type}:{to_id}  -> hash
  kg:edges_in:{type}:{id}        -> sorted set (incoming edges)
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)


class KnowledgeGraph:
    """
    Redis-backed knowledge graph for crypto entity relationships.
    Stores nodes and edges in Redis for fast graph traversal.
    """

    def __init__(self, redis: aioredis.Redis | None = None) -> None:
        self._redis = redis
        self._node_cache: dict[str, dict[str, Any]] = {}

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis:
            return self._redis
        from app.knowledge_graph.config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

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

    async def get_node(self, node_type: str, node_id: str) -> dict[str, Any] | None:
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

    async def delete_node(self, node_type: str, node_id: str) -> bool:
        """Delete a node and all its edges (cascade)."""
        r = await self._get_redis()
        node_key = f"kg:node:{node_type}:{node_id}"
        exists = await r.exists(node_key)
        if not exists:
            return False

        out_idx_key = f"kg:edges:{node_type}:{node_id}"
        out_edge_keys = await r.zrange(out_idx_key, 0, -1)

        in_idx_key = f"kg:edges_in:{node_type}:{node_id}"
        in_edge_keys = await r.zrange(in_idx_key, 0, -1)

        all_edge_keys = set(out_edge_keys) | set(in_edge_keys)

        pipe = r.pipeline()
        for ek in all_edge_keys:
            edge_data = await r.hgetall(ek)
            if edge_data:
                from_type = edge_data.get("from_type", "")
                from_id = edge_data.get("from_id", "")
                if from_type and from_id:
                    pipe.zrem(f"kg:edges:{from_type}:{from_id}", ek)
                to_type = edge_data.get("to_type", "")
                to_id = edge_data.get("to_id", "")
                if to_type and to_id:
                    pipe.zrem(f"kg:edges_in:{to_type}:{to_id}", ek)
            pipe.delete(ek)

        pipe.delete(node_key)
        pipe.delete(out_idx_key)
        pipe.delete(in_idx_key)

        cache_key = f"{node_type}:{node_id}"
        self._node_cache.pop(cache_key, None)

        await pipe.execute()
        return True

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
        """Add a directed edge between two nodes. Weight: 0.0-1.0."""
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

        from_edge_key = f"kg:edges:{from_type}:{from_id}"
        await r.zadd(from_edge_key, {edge_key: weight})

        to_edge_key = f"kg:edges_in:{to_type}:{to_id}"
        await r.zadd(to_edge_key, {edge_key: weight})

        return edge_key

    # ── Graph Traversal ──────────────────────────────────────────

    async def get_neighbors(
        self,
        node_type: str,
        node_id: str,
        relation: str | None = None,
        direction: str = "outgoing",
        min_weight: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get neighboring nodes of a given node."""
        r = await self._get_redis()
        results = []

        dirs = []
        if direction in ("outgoing", "both"):
            dirs.append(("kg:edges:", "outgoing"))
        if direction in ("incoming", "both"):
            dirs.append(("kg:edges_in:", "incoming"))

        for prefix, dir_label in dirs:
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

                if dir_label == "outgoing":
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
                        "label": neighbor_node.get("label", neighbor_id) if neighbor_node else neighbor_id,
                        "relation": edge_data.get("relation", ""),
                        "weight": weight,
                        "direction": dir_label,
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
    ) -> list[list[dict[str, Any]]]:
        """BFS pathfinding between two nodes."""
        r = await self._get_redis()
        paths = []

        queue = [(from_type, from_id, [])]
        visited = {(from_type, from_id)}

        while queue and len(paths) < max_paths:
            curr_type, curr_id, path_so_far = queue.pop(0)

            if len(path_so_far) >= max_depth:
                continue

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
        """Extract a subgraph around a node for visualization."""
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

    # ── Retrieval Integration ─────────────────────────────────────

    async def extract_entities(self, query: str) -> list[dict[str, str]]:
        """Extract crypto entities (addresses, symbols, chains) from *query*.

        Delegates to the regex-based ``app.entity_extraction`` extractor.
        Returns a list of ``{"type", "value"}`` dicts — empty when the query
        contains no entity mentions (callers should skip graph enrichment).
        """
        if not query or not query.strip():
            return []
        try:
            from app.entity_extraction import extract_entities as _extract

            extraction = _extract(query)
            return extraction.all_entities()
        except Exception as exc:
            logger.debug("kg_extract_entities_failed error=%s", exc)
            return []

    async def search_related(
        self,
        entities: list[dict[str, str]],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Expand *entities* through the graph and return related results.

        Reuses the entity-expansion traversal from ``ingestion`` (node
        lookup + neighbor walk) and returns at most *limit* result dicts in
        the RAG hit shape (``doc_id``, ``content``, ``score``). Best-effort:
        returns ``[]`` when the graph is unreachable or nothing matches.
        """
        if not entities:
            return []
        try:
            from app.knowledge_graph.ingestion import expand_query_entities

            expanded = await expand_query_entities(self, entities, max_depth=2)
            return expanded[:limit]
        except Exception as exc:
            logger.debug("kg_search_related_failed error=%s", exc)
            return []

    # ── Maintenance ────────────────────────────────────────────────

    async def prune_orphaned_edges(self) -> int:
        """Remove edges whose source or target node no longer exists."""
        r = await self._get_redis()
        removed = 0
        processed = 0

        async for edge_key in r.scan_iter(match="kg:edge:*", count=500):
            processed += 1
            edge_data = await r.hgetall(edge_key)
            if not edge_data:
                continue
            from_type = edge_data.get("from_type", "")
            from_id = edge_data.get("from_id", "")
            to_type = edge_data.get("to_type", "")
            to_id = edge_data.get("to_id", "")

            from_key = f"kg:node:{from_type}:{from_id}"
            to_key = f"kg:node:{to_type}:{to_id}"

            from_exists = await r.exists(from_key)
            to_exists = await r.exists(to_key)

            if not from_exists or not to_exists:
                pipe = r.pipeline()
                pipe.zrem(f"kg:edges:{from_type}:{from_id}", edge_key)
                pipe.zrem(f"kg:edges_in:{to_type}:{to_id}", edge_key)
                pipe.delete(edge_key)
                await pipe.execute()
                removed += 1

        logger.info("kg_prune_orphans scanned=%d removed=%d", processed, removed)
        return removed

    # ── Statistics ──────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Get knowledge graph statistics."""
        r = await self._get_redis()
        from app.knowledge_graph.models import NodeType

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

        edge_keys = []
        async for key in r.scan_iter(match="kg:edge:*", count=500):
            edge_keys.append(key)

        return {
            "nodes": node_counts,
            "total_nodes": sum(node_counts.values()),
            "total_edges": len(edge_keys),
            "node_types": list(node_counts.keys()),
        }
