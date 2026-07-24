"""
Knowledge Graph - Wallet -> Token -> Scam Relationship Engine
==============================================================
Builds and queries a knowledge graph from RAG document metadata.

Nodes: wallet, token, contract, scam_pattern, entity, chain
Edges: holds_token, deployed_by, involved_in, similar_to, flags, transfers_to

Usage:
    from app.knowledge_graph import KnowledgeGraph, get_knowledge_graph
    from app.knowledge_graph import NodeType, EdgeType
    from app.knowledge_graph import build_graph_from_rag
"""

from __future__ import annotations
from app.knowledge_graph.engine import (
    KnowledgeGraph,
    build_graph_from_rag,
    expand_query_entities,
    get_knowledge_graph,
    ingest_rag_document,
)
from app.knowledge_graph.models import EdgeType, NodeType

__all__ = [
    "EdgeType",
    "KnowledgeGraph",
    "NodeType",
    "build_graph_from_rag",
    "expand_query_entities",
    "get_knowledge_graph",
    "ingest_rag_document",
]
