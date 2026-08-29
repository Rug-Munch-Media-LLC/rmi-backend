"""Unit tests — Qwen3 reranker 4B-only lanes (2026-08-29 consolidation).

Offline: all HTTP is stubbed at ``_post_rerank``'s httpx boundary via
monkeypatching :func:`app.rag.rerank._post_rerank` (or httpx itself for
the single-shot paths). Contracts:

* live tier caps candidates at LIVE_CANDIDATE_CAP and routes to the 4B
  (:8003) — the 0.6B lane (:8008) is retired, so live == deep endpoint
* live lane down → single-shot fail-open [] (no second rung anymore)
* strain breaker opens after _STRAIN_THRESHOLD failures in window,
  clears on success / clear_strain()
* deep tier unchanged single-shot behavior (default arg compat)
"""

from __future__ import annotations

import httpx
import pytest

from app.rag import rerank as rr


@pytest.fixture(autouse=True)
def _clean_strain():
    rr.clear_strain()
    yield
    rr.clear_strain()


def _ok_results(n: int, score: float = 0.9) -> list[dict]:
    return [{"index": i, "relevance_score": score} for i in range(n)]


# ── Tier selection ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_live_tier_caps_candidates_and_hits_4b(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_post(url, query, documents, top_n, timeout, model_label):
        calls.append((url, len(documents)))
        return _ok_results(len(documents))

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [f"doc{i}" for i in range(100)]
    out = await rr.rerank_results("rug pull", docs, top_n=5, tier="live")
    assert out
    assert calls[0][0] == rr.RERANK_URL_LIVE
    # Live routes to the 4B endpoint (0.6B :8008 retired 2026-08-29).
    assert calls[0][0] == rr.RERANK_URL
    # Candidate cap enforced on the live path.
    assert calls[0][1] == rr.LIVE_CANDIDATE_CAP


@pytest.mark.asyncio
async def test_live_lane_down_fails_open_single_shot(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    async def fake_post(url, query, documents, top_n, timeout, model_label):
        calls.append((url, len(documents)))
        return None  # 4B strained/unreachable

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [f"doc{i}" for i in range(60)]
    out = await rr.rerank_results("honeypot check", docs, top_n=5, tier="live")
    # Fail-open: caller keeps pre-rerank order.
    assert out == []
    # Single rung on the 4B — no second fallback call (live == deep
    # endpoint since the 0.6B lane was retired).
    assert calls == [(rr.RERANK_URL_LIVE, rr.LIVE_CANDIDATE_CAP)]


@pytest.mark.asyncio
async def test_live_fail_open_when_tier_down(monkeypatch) -> None:
    async def fake_post(url, query, documents, top_n, timeout, model_label):
        return None

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    out = await rr.rerank_results("scam", ["a", "b"], top_n=2, tier="live")
    assert out == []  # caller keeps pre-rerank order


@pytest.mark.asyncio
async def test_deep_tier_default_single_shot(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_post(url, query, documents, top_n, timeout, model_label):
        calls.append(url)
        return _ok_results(len(documents))

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    # No tier arg → deep behavior preserved for existing callers.
    out = await rr.rerank_results("audit", ["a", "b"], top_n=2)
    assert out
    assert calls == [rr.RERANK_URL]


# ── Strain breaker ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strain_breaker_opens_after_threshold(monkeypatch) -> None:
    """Full-stack: real _post_rerank + httpx stubbed. The shared 4B
    endpoint (:8003, also the live URL) always fails. After
    _STRAIN_THRESHOLD failures the breaker opens and the server is never
    contacted again (live fails open to [] each time)."""
    http_calls: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def post(self, url, json=None):
            http_calls.append(url)
            raise httpx.ConnectError("4B down")

    monkeypatch.setattr(rr.httpx, "AsyncClient", FakeClient)
    for _ in range(rr._STRAIN_THRESHOLD):
        out = await rr.rerank_results("q", ["a"], top_n=1, tier="live")
        assert out == [], "fail-open while the 4B is down"
    assert len(http_calls) == rr._STRAIN_THRESHOLD
    assert rr._is_strained(rr.RERANK_URL_LIVE)

    # Breaker open → endpoint skipped at the HTTP layer entirely.
    http_calls.clear()
    await rr.rerank_results("q", ["a"], top_n=1, tier="live")
    assert not http_calls, "strained endpoint must be skipped without HTTP"


@pytest.mark.asyncio
async def test_success_clears_strain(monkeypatch) -> None:
    rr._record_strain(rr.RERANK_URL_LIVE)
    rr._record_strain(rr.RERANK_URL_LIVE)

    async def ok_post(url, query, documents, top_n, timeout, model_label):
        return _ok_results(len(documents))

    monkeypatch.setattr(rr, "_post_rerank", ok_post)
    await rr.rerank_results("q", ["a"], top_n=1, tier="live")
    assert not rr._is_strained(rr.RERANK_URL_LIVE)


def test_clear_strain_all() -> None:
    rr._record_strain("http://a")
    rr._record_strain("http://b")
    rr.clear_strain()
    assert not rr._is_strained("http://a")
    assert not rr._is_strained("http://b")


# ── Noise floor & empty guards ───────────────────────────────────────


@pytest.mark.asyncio
async def test_noise_floor_filters_confident_irrelevant(monkeypatch) -> None:
    """Noise-floor filtering lives inside _post_rerank — stub at the HTTP
    boundary (httpx.AsyncClient) to exercise it end-to-end."""

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.95},
                    {"index": 1, "relevance_score": 0.01},  # below floor
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(rr.httpx, "AsyncClient", FakeClient)
    out = await rr.rerank_results("q", ["a", "b"], top_n=2, tier="deep")
    assert [r["index"] for r in out] == [0]


@pytest.mark.asyncio
async def test_empty_inputs_short_circuit(monkeypatch) -> None:
    called = False

    async def boom(*a, **k):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(rr, "_post_rerank", boom)
    assert await rr.rerank_results("", ["a"]) == []
    assert await rr.rerank_results("q", []) == []
    assert called is False
