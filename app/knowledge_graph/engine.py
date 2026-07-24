"""
Knowledge Graph - Engine
===========================
Redis connection config, singleton management, and convenience
functions for the knowledge graph.
"""

from __future__ import annotations
from app.knowledge_graph.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from app.knowledge_graph.graph import KnowledgeGraph
from app.knowledge_graph.ingestion import build_graph_from_rag, expand_query_entities, ingest_rag_document
from app.knowledge_graph.models import EdgeType, NodeType

# Redis config

# Singleton
_kg_instance: KnowledgeGraph | None = None

async def get_knowledge_graph() -> KnowledgeGraph:
    """Get or create the singleton KnowledgeGraph instance."""
    global _kg_instance
    if _kg_instance is None:
        _kg_instance = KnowledgeGraph()
    return _kg_instance

__all__ = [
    "EdgeType", "NodeType", "KnowledgeGraph",
    "get_knowledge_graph",
    "ingest_rag_document", "expand_query_entities", "build_graph_from_rag",
]
