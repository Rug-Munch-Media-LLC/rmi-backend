"""
Cross-Chain Trace — Fund Flow Tracer via Chain-Indexer ClickHouse
==================================================================
Wires the chain-indexer's 73M EVM transfer records into the scanner
pipeline for wallet-level fund flow tracing across chains.

All data is LOCAL — we OWN the ClickHouse tables. Zero external API calls.
"""

from __future__ import annotations

# Compatibility wrappers
from .analyze import analyze, analyze_wallet, get_analyzer

# Analyzer
from .analyzer import CrossChainTraceAnalyzer
from .models import CrossChainEdge, CrossChainNode, CrossChainTraceReport

__all__ = [
    "CrossChainEdge",
    "CrossChainNode",
    "CrossChainTraceAnalyzer",
    "CrossChainTraceReport",
    "analyze",
    "analyze_wallet",
    "get_analyzer",
]
