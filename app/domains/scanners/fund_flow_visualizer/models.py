"""
SENTINEL - Fund Flow Visualization Models
==========================================
Data models and helpers for fund flow visualization.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# ─── Output directory ────────────────────────────────────────────────

SVG_OUTPUT_DIR = "/root/backend/data/fund_flows"


def _ensure_output_dir() -> None:
    os.makedirs(SVG_OUTPUT_DIR, exist_ok=True)


# ─── Address shortener ──────────────────────────────────────────────


def shorten_address(addr: str) -> str:
    """Shorten address for display: 0x1234...abcd or So1a...2b3c."""
    if not addr:
        return "???"
    addr = addr.strip()
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


# ─── Report dataclasses ──────────────────────────────────────────────


@dataclass
class FundFlowNode:
    address: str
    role: str  # creator, funder, lp, seller, other
    amount_usd: float


@dataclass
class FundFlowEdge:
    from_addr: str
    to_addr: str
    amount_usd: float


@dataclass
class FundFlowReport:
    svg_content: str
    svg_path: str
    nodes: list[dict[str, Any]]  # {address, role, amount_usd}
    edges: list[dict[str, Any]]  # {from, to, amount_usd}
    risk_flags: list[str]
    risk_score: int  # 0-100
    risk_level: str  # low / moderate / high / critical
