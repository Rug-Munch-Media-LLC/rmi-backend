"""Regression: ``rugbrain_search`` returns ``list[dict]`` — callers must not treat it as a dict.

Both the Qdrant-direct lane and the rugbrain HTTP lane return a hit list
(``content`` / ``score`` / ``metadata`` / ...). Three call sites used
``.get("results")`` / ``["results"]`` and would raise ``AttributeError`` /
``TypeError`` on every non-empty *and* empty response. These tests pin the
contract on the call paths themselves, with a stubbed client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

HITS: list[dict[str, Any]] = [
    {"content": "flash-loan governance attack on DAO X", "score": 0.91, "metadata": {"title": "t"}},
    {"content": "rug pull deployer funded via mixer", "score": 0.72, "metadata": {}},
]


def _patch_search(monkeypatch: pytest.MonkeyPatch, payload: list[dict[str, Any]]) -> None:
    """Stub the lazily-imported client attribute."""
    import app.integrations.rugbrain_client as client

    async def _fake(**_kwargs: Any) -> list[dict[str, Any]]:
        return payload

    monkeypatch.setattr(client, "rugbrain_search", _fake)


# ── investigation_narratives._execute_hop ───────────────────────


async def test_execute_hop_accepts_hit_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.investigation_narratives import _execute_hop

    _patch_search(monkeypatch, HITS)
    hop = await _execute_hop({"hop": "trace", "goal": "find deployer"}, "0xdead")

    assert hop["success"] is True
    assert hop["results_count"] == 2
    assert [r["content"] for r in hop["results"]] == [h["content"] for h in HITS]
    assert hop["confidence"] == {"score": 0.91}


async def test_execute_hop_accepts_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.investigation_narratives import _execute_hop

    _patch_search(monkeypatch, [])
    hop = await _execute_hop({"hop": "trace", "goal": "find deployer"}, "0xdead")

    assert hop["success"] is False
    assert hop["results_count"] == 0
    assert hop["results"] == []
    assert hop["confidence"] == {"score": 0.0}
