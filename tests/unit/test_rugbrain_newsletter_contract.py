"""Regression (HELD BACK): ``generate_newsletter`` must consume the hit LIST.

Committed alongside the content_platform.py fix once the methodology gate is
cleared for that file (pre-existing /srv/secrets literal). Until then this test
fails against the un-fixed caller, by design.
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
    import app.integrations.rugbrain_client as client

    async def _fake(**_kwargs: Any) -> list[dict[str, Any]]:
        return payload

    monkeypatch.setattr(client, "rugbrain_search", _fake)
# ── content_platform.generate_newsletter ────────────────────────



async def test_generate_newsletter_accepts_hit_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.content_platform as cp

    _patch_search(monkeypatch, HITS)

    async def _fake_post(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    monkeypatch.setattr(cp, "create_post", _fake_post)
    post = await cp.generate_newsletter()

    assert HITS[0]["content"] in post["content"]
    assert "Top Threats" in post["content"]


async def test_generate_newsletter_accepts_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.content_platform as cp

    _patch_search(monkeypatch, [])

    async def _fake_post(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    monkeypatch.setattr(cp, "create_post", _fake_post)
    post = await cp.generate_newsletter()

    assert "Top Threats" not in post["content"]
    assert post["post_type"] == "newsletter"
