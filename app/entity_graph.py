"""Backward-compatibility shim — moved to app.domains.scanner.entity_graph."""

from __future__ import annotations

from app.domains.scanner.entity_graph import (
    annotations,
    dataclass,
    field,
    Any,
    get_logger,
    logger,
    GraphNode,
    GraphEdge,
    build_entity_graph,
    generate_svg,
)


__all__ = [
    "annotations",
    "dataclass",
    "field",
    "Any",
    "get_logger",
    "logger",
    "GraphNode",
    "GraphEdge",
    "build_entity_graph",
    "generate_svg",
]
