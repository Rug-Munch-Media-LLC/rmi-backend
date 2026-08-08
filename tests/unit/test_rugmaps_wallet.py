"""Tests for RugMaps wallet enrichment — app/rugmaps_wallet.py.

Covers the two new OUR-data enrichment layers wired into
``/api/v1/rugmaps/wallet-labels`` and ``/api/v1/rugmaps/fund-flows``:

  - ``classify_label`` mapping (CEX / DEX / mixer / bridge / whale / bot /
    sniper / dev / normal)
  - ``batch_lookup_wallet_labels`` merging Qdrant wallet_v2 + ClickHouse +
    DataBus scanner with per-source failure tolerance
  - ``lookup_fund_flows`` splitting ClickHouse wallet_flow edges into
    inbound/outbound and attaching the cross-chain profile
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.rugmaps_wallet as wallet_module
from app.rugmaps_wallet import (
    _merge_label_sources,
    batch_lookup_wallet_labels,
    classify_label,
    lookup_fund_flows,
)

# ── classify_label ────────────────────────────────────────────────────────


def test_classify_label_cex():
    assert classify_label("Binance Hot Wallet 10", "exchange") == "CEX"
    assert classify_label("Coinbase", "") == "CEX"
    assert classify_label("OKX Cold", "cex") == "CEX"


def test_classify_label_dex():
    assert classify_label("Uniswap V3 Router", "router") == "DEX"
    assert classify_label("Raydium AMM", "dex") == "DEX"


def test_classify_label_mixer_and_bridge():
    assert classify_label("Tornado Cash", "mixer") == "mixer"
    assert classify_label("Wormhole Bridge", "bridge") == "bridge"


def test_classify_label_whale_bot_sniper_dev():
    assert classify_label("Whale", "whale") == "whale"
    assert classify_label("Sniper Bot", "sniper") == "sniper"
    assert classify_label("Arbitrage Bot", "bot") == "bot"
    assert classify_label("Deployer", "deployer") == "dev"
    assert classify_label("Team Wallet", "developer") == "dev"


def test_classify_label_normal():
    assert classify_label("", "") == "normal"
    assert classify_label("0x1234", "") == "normal"


# ── batch_lookup_wallet_labels ────────────────────────────────────────────


async def test_batch_lookup_merges_all_sources():
    """Qdrant + ClickHouse + scanner sources merge into one record."""
    qdrant = {
        "0xaaaa": {
            "label": "Qdrant Label",
            "label_type": "exchange",
            "chain": "ethereum",
            "entity_type": "exchange",
        }
    }
    clickhouse = {
        "0xbbbb": [
            {
                "address": "0xbbbb",
                "label": "CH Label",
                "category": "deployer",
                "source": "clickhouse",
                "sanctioned": False,
                "class": "dev",
            }
        ]
    }
    scanner = {
        "0xcccc": {
            "address": "0xcccc",
            "entity_name": "Scanner Entity",
            "entity_type": "whale",
            "persona": "Accumulator",
            "risk_score": 42.0,
            "source": "databus_wallet_scanner",
            "class": "whale",
        }
    }

    with (
        patch.object(wallet_module, "_scroll_wallet_v2", new=AsyncMock(return_value=qdrant)),
        patch.object(wallet_module, "_clickhouse_batch_labels", return_value=clickhouse),
        patch.object(wallet_module, "_scanner_labels", new=AsyncMock(return_value=scanner)),
    ):
        result = await batch_lookup_wallet_labels(["0xaaaa", "0xbbbb", "0xcccc"], chain="ethereum")

    assert result["total"] == 3
    assert result["labeled"] == 3
    assert result["labels"]["0xaaaa"]["label"] == "Qdrant Label"
    assert result["labels"]["0xbbbb"]["class"] == "dev"
    assert result["labels"]["0xcccc"]["entity_name"] == "Scanner Entity"
    assert result["labels"]["0xcccc"]["class"] == "whale"


async def test_batch_lookup_tolerates_failing_sources():
    """A dead Qdrant/ClickHouse must not kill the batch — scanner still works."""
    with (
        patch.object(wallet_module, "_scroll_wallet_v2", new=AsyncMock(side_effect=RuntimeError("qdrant down"))),
        patch.object(wallet_module, "_clickhouse_batch_labels", side_effect=ConnectionError("ch down")),
        patch.object(
            wallet_module,
            "_scanner_labels",
            new=AsyncMock(
                return_value={
                    "0xdddd": {
                        "address": "0xdddd",
                        "entity_name": "Only Scanner",
                        "entity_type": "cex",
                        "risk_score": 0.0,
                        "source": "databus_wallet_scanner",
                        "class": "CEX",
                    }
                }
            ),
        ),
    ):
        result = await batch_lookup_wallet_labels(["0xdddd"])

    assert result["labeled"] == 1
    assert result["labels"]["0xdddd"]["label"] == "Only Scanner"
    assert result["labels"]["0xdddd"]["class"] == "CEX"


async def test_batch_lookup_empty_input():
    result = await batch_lookup_wallet_labels([], chain="solana")
    assert result["total"] == 0
    assert result["labels"] == {}


def test_merge_label_sources_prefers_scanner_entity():
    """Scanner entity enrichment overrides ClickHouse display label."""
    clickhouse = {
        "0xeeee": [
            {
                "address": "0xeeee",
                "label": "CH Name",
                "category": "exchange",
                "source": "ch",
                "sanctioned": False,
                "class": "CEX",
            }
        ]
    }
    scanner = {
        "0xeeee": {
            "address": "0xeeee",
            "entity_name": "Real Entity Name",
            "entity_type": "deployer",
            "risk_score": 10.0,
            "source": "scanner",
            "class": "dev",
        }
    }
    merged = _merge_label_sources({}, clickhouse, scanner, ["0xeeee"])
    rec = merged["0xeeee"]
    assert rec["label"] == "Real Entity Name"
    assert rec["entity_type"] == "deployer"
    assert rec["class"] == "dev"


# ── lookup_fund_flows ──────────────────────────────────────────────────────


FLOW_GRAPH = {
    "result": "scanned",
    "edges": [
        {"from": "0x1111", "to": "0x9999", "token": "0xAAA", "usd": 500.0, "tx_count": 2, "tx_hashes": ["h1"]},
        {"from": "0x9999", "to": "0x1111", "token": "0xBBB", "usd": 1200.0, "tx_count": 1, "tx_hashes": ["h2"]},
        {"from": "0x2222", "to": "0x3333", "token": "0xCCC", "usd": 999.0, "tx_count": 1, "tx_hashes": []},
    ],
    "nodes": [
        {"id": "0x9999", "type": "CEX", "label": "Binance", "net_usd": -700.0, "tx_count": 3},
        {"id": "0x1111", "type": "EOA", "label": None, "net_usd": 700.0, "tx_count": 3},
    ],
}

CROSS_CHAIN_PROFILE = {
    "addresses": [{"chain": "bsc", "address": "0xcccd", "confidence": 0.9}],
    "chains": ["ethereum", "bsc"],
    "total_chains": 2,
    "label": "",
}


async def test_lookup_fund_flows_splits_inbound_outbound():
    """Edges touching the wallet split into inbound/outbound; others ignored."""
    with (
        patch(
            "app.domains.scanner.engines.wallet_flow.build_flow_graph",
            new=AsyncMock(return_value=FLOW_GRAPH),
        ),
        patch(
            "app.cross_chain.get_cross_chain_profile",
            new=AsyncMock(return_value=CROSS_CHAIN_PROFILE),
        ),
    ):
        result = await lookup_fund_flows("0xTOKEN", "0x9999", chain="ethereum")

    assert result["source"] == "wallet_flow_clickhouse"
    assert result["address"] == "0x9999"
    # inbound = funds TO 0x9999
    assert len(result["inbound"]) == 1
    assert result["inbound"][0]["from"] == "0x1111"
    assert result["inbound"][0]["usd"] == 500.0
    # outbound = funds FROM 0x9999
    assert len(result["outbound"]) == 1
    assert result["outbound"][0]["to"] == "0x1111"
    assert result["outbound"][0]["usd"] == 1200.0
    # unrelated edge dropped
    assert result["inbound_total_usd"] == 500.0
    assert result["outbound_total_usd"] == 1200.0
    # cross-chain profile attached
    assert result["cross_chain"]["total_chains"] == 2


async def test_lookup_fund_flows_degrades_gracefully():
    """Engine failure returns empty flows, no exception."""
    with (
        patch(
            "app.domains.scanner.engines.wallet_flow.build_flow_graph",
            new=AsyncMock(return_value={"result": "unavailable", "edges": [], "nodes": []}),
        ),
        patch("app.cross_chain.get_cross_chain_profile", new=AsyncMock(side_effect=OSError("no network"))),
    ):
        result = await lookup_fund_flows("0xTOKEN", "0x9999", chain="ethereum")

    assert result["inbound"] == []
    assert result["outbound"] == []
    assert result["cross_chain"] == {}
