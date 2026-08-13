"""Pure unit tests for the chat "trace" tool intent (wallet/entity forensics).

Covers ``app/domains/chat_tools.py``:
  - ``_detect_tool_intent`` routes address + forensics phrases (and address +
    a bare who/what/related/trace word) to "trace", keeps "scan" for
    address + security keywords, and returns None for a bare address without
    forensics intent.
  - ``_exec_trace`` renders a compact summary from a mocked cross-chain entity
    resolution; falls back to ``build_entity_graph``; returns None on failure
    or when both sources are empty.
  - ``_run_tool_for_intent`` routes "trace" → ``_exec_trace``.

No network or DB — ``resolve_cross_chain_entity`` and ``build_entity_graph``
are mocked.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app.domains import chat_tools
from app.domains.chat_tools import (
    _detect_tool_intent,
    _exec_trace,
    _run_tool_for_intent,
)

_EVM = "0x" + "a" * 40


# ── 1. Intent classifier ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # address + explicit forensics phrases → trace
        (f"trace {_EVM}", "trace"),
        (f"who funded {_EVM}", "trace"),
        (f"who is behind {_EVM}", "trace"),
        (f"who sent funds to {_EVM}", "trace"),
        (f"related wallets for {_EVM}", "trace"),
        (f"what entity controls {_EVM}", "trace"),
        (f"first funder of {_EVM}", "trace"),
        (f"funding source for {_EVM}", "trace"),
        (f"is {_EVM} a whale", "trace"),
        (f"syndicate behind {_EVM}", "trace"),
        # address + a bare who/what/related/trace word → trace
        (f"who is {_EVM}", "trace"),
        (f"what is {_EVM}", "trace"),
        (f"related to {_EVM}", "trace"),
        # scan still wins for address + security keywords
        (f"is {_EVM} a rug", "scan"),
        (f"scan {_EVM} for honeypot", "scan"),
        # bare address without forensics intent → None
        (_EVM, None),
        ("hello there", None),
        ("what is bitcoin", None),
        ("", None),
    ],
)
def test_detect_tool_intent_classifies_trace(message: str, expected: str | None) -> None:
    assert _detect_tool_intent(message) == expected


# ── 2. Executor ───────────────────────────────────────────────────────


def _mock_resolved_result() -> dict:
    return {
        "result": "scanned",
        "address": _EVM,
        "source_chain": "ethereum",
        "addresses": [
            {
                "chain": "bsc",
                "address": _EVM,
                "evidence_type": "same_deployer",
                "confidence": 0.95,
                "evidence": "0xdeploy",
                "details": {"tokens_deployed": 3, "first_deployed_ts": "2025-01-01T00:00:00Z"},
            },
            {
                "chain": "arbitrum",
                "address": "0x" + "b" * 40,
                "evidence_type": "same_funder",
                "confidence": 0.6,
                "evidence": "0xfund",
                "details": {"funder": "0x" + "f" * 40, "first_funder_ts": "2025-02-01T00:00:00Z"},
            },
        ],
        "entity_type": "multi_chain_actor",
        "risk": "high",
        "links_found": 2,
        "elapsed_ms": 12.3,
    }


def _mock_graph() -> dict:
    return {
        "center": {"id": "ethereum:" + _EVM, "address": _EVM, "chain": "ethereum"},
        "nodes": [
            {"id": "ethereum:" + _EVM, "label": "0xaaaa…", "type": "wallet"},
            {"id": "bsc:0xbbbb…", "label": "0xbbbb…", "type": "wallet"},
        ],
        "edges": [{"source": "ethereum:" + _EVM, "target": "bsc:0xbbbb…", "type": "transfer"}],
        "stats": {
            "total_nodes": 2,
            "total_edges": 1,
            "entity_types": ["wallet"],
            "risk_distribution": {"high": 0, "medium": 0, "low": 2},
        },
    }


async def test_exec_trace_renders_resolver_summary() -> None:
    resolve = mock.AsyncMock(return_value=_mock_resolved_result())
    with mock.patch(
        "app.domains.scanner.engines.cross_chain_entity.resolve_cross_chain_entity",
        new=resolve,
    ):
        text = await _exec_trace(f"who is behind {_EVM}")
    assert text is not None
    assert "multi_chain_actor" in text
    assert "high" in text
    assert "deployer on bsc" in text
    assert "first funder" in text
    assert "same-funder candidates on arbitrum" in text
    assert "2 cross-chain link(s): arbitrum, bsc" in text
    resolve.assert_awaited_once()


async def test_exec_trace_returns_none_without_address() -> None:
    assert await _exec_trace("who funded the project") is None


async def test_exec_trace_falls_back_to_entity_graph_when_resolution_unavailable() -> None:
    with (
        mock.patch(
            "app.domains.scanner.engines.cross_chain_entity.resolve_cross_chain_entity",
            new=mock.AsyncMock(return_value={"result": "unavailable", "reason": "clickhouse_unavailable"}),
        ),
        mock.patch("app.domains.scanner.entity_graph.build_entity_graph", return_value=_mock_graph()),
    ):
        text = await _exec_trace(f"trace {_EVM}")
    assert text is not None
    assert "2 node(s)" in text
    assert "1 edge(s)" in text
    assert "wallet" in text


async def test_exec_trace_returns_none_when_resolver_fails_and_graph_raises() -> None:
    with (
        mock.patch(
            "app.domains.scanner.engines.cross_chain_entity.resolve_cross_chain_entity",
            new=mock.AsyncMock(side_effect=RuntimeError("boom")),
        ),
        mock.patch(
            "app.domains.scanner.entity_graph.build_entity_graph",
            side_effect=RuntimeError("graph boom"),
        ),
    ):
        assert await _exec_trace(f"trace {_EVM}") is None


async def test_exec_trace_returns_none_when_resolver_empty_and_graph_empty() -> None:
    with (
        mock.patch(
            "app.domains.scanner.engines.cross_chain_entity.resolve_cross_chain_entity",
            new=mock.AsyncMock(return_value={"result": "unavailable"}),
        ),
        mock.patch(
            "app.domains.scanner.entity_graph.build_entity_graph",
            return_value={"center": {"address": _EVM}, "nodes": [], "edges": []},
        ),
    ):
        assert await _exec_trace(f"trace {_EVM}") is None


# ── 3. Dispatcher routing ─────────────────────────────────────────────


async def test_run_tool_for_intent_routes_trace_to_exec_trace() -> None:
    assert chat_tools._EXECUTORS["trace"] is chat_tools._exec_trace
    fake = mock.AsyncMock(return_value="Trace summary")
    with mock.patch.dict(chat_tools._EXECUTORS, {"trace": fake}):
        result = await _run_tool_for_intent(f"who funded {_EVM}", "trace")
    assert result == "Trace summary"
    fake.assert_awaited_once_with(f"who funded {_EVM}")
