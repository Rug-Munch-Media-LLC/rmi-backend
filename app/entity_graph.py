"""Backward-compatibility shim — moved to app.domains.scanner.entity_graph."""

from __future__ import annotations

from app.domains.scanner.entity_graph import (
    Any,
    GraphEdge,
    GraphNode,
    annotations,
    build_entity_graph,
    dataclass,
    field,
    generate_svg,
    get_logger,
    logger,
)

__all__ = [
    "Any",
    "GraphEdge",
    "GraphNode",
    "annotations",
    "build_entity_graph",
    "dataclass",
    "field",
    "generate_svg",
    "get_logger",
    "logger",
]
