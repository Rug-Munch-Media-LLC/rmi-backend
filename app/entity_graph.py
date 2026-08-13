"""Backward-compatibility shim — moved to app.domains.scanner.entity_graph."""

from __future__ import annotations

from app.domains.scanner.entity_graph import (
    GraphEdge as GraphEdge,
    GraphNode as GraphNode,
    build_entity_graph as build_entity_graph,
    generate_svg as generate_svg,
    logger as logger,
)

__all__ = [
    "GraphEdge",
    "GraphNode",
    "build_entity_graph",
    "generate_svg",
    "logger",
]
