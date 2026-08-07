"""
Knowledge Graph - Node and Edge Types
=======================================
Type definitions for the Redis-backed knowledge graph.
"""

from __future__ import annotations


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


class NodeType:
    WALLET = "wallet"
    TOKEN = "token"  # noqa: S105 — config default
    CONTRACT = "contract"
    SCAM = "scam"
    ENTITY = "entity"  # named entity (protocol, person, org)
    CHAIN = "chain"
    ADDRESS = "address"  # generic blockchain address
