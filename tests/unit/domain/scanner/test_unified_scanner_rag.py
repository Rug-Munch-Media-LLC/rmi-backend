"""Tests for RAG read-back enrichment in the UnifiedTokenScanner v2.

Verifies:
  1. ``UnifiedTokenScanner.scan`` calls the RAG enricher after scoring and
     stores the context on ``ScanResult.rag_context``.
  2. RAG failure is best-effort — the scan completes and
     ``rag_context`` is ``None`` (e.g. embedder/Ollama down).
  3. ``RAGIntelligenceEnricher.enrich_scan_result`` accepts the unified
     ``ScanResult`` format (string ``modules_run`` + deployer under
     ``dev_reputation``).
  4. The v2 router serializes ``RAGContext`` to a JSON-safe dict (and
     returns ``None`` when RAG is unavailable).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.unified import UnifiedTokenScanner
from app.domains.scanner.unified.model import ScanResult
from app.rag.intelligence import RAGContext, RAGIntelligenceEnricher
from app.rag.retrieval import ScoredHit


def _unified_result(**overrides: object) -> ScanResult:
    """Build a completed unified ScanResult with the fields RAG needs."""
    defaults: dict[str, object] = {
        "token_address": "0xRAGTEST123",
        "chain": "ethereum",
        "symbol": "RAGT",
        "name": "Rag Test",
        "safety_score": 50.0,
        "risk_level": "medium",
        "risk_flags": ["HOLDER_CONCENTRATION_HIGH"],
        "modules_run": ["token_price", "token_security"],
        "dev_reputation": {"deployer_address": "0xDeployer", "risk_score": 90},
    }
    defaults.update(overrides)
    return ScanResult(**defaults)  # type: ignore[arg-type]


def _fake_context() -> RAGContext:
    return RAGContext(
        similar_scams=[
            ScoredHit(
                content="Past honeypot pattern on same deployer",
                score=0.7,
                final_score=0.8,
                collection="scams",
                doc_id="d1",
            )
        ],
        wallet_context=[ScoredHit(content="deployer wallet label", score=0.6, final_score=0.6, collection="wallet")],
        market_context=[ScoredHit(content="news hit", score=0.4, final_score=0.4, collection="news")],
        fusion_summary="RAG Intelligence for 0xRAGTEST123...",
        collections_searched=["scams", "wallet", "news"],
        took_ms=9,
        query_id="enrich:test:1",
    )


class _FakeEnricher:
    """Stand-in for RAGIntelligenceEnricher that records the scan result."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.seen: ScanResult | None = None

    async def enrich_scan_result(self, scan_result: ScanResult) -> RAGContext:
        self.seen = scan_result
        return _fake_context()


class _BoomEnricher:
    """Enricher that simulates a dead embedder (e.g. Ollama /api/embed down)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def enrich_scan_result(self, scan_result: ScanResult) -> RAGContext:
        raise RuntimeError("embedder unavailable")


class TestUnifiedScannerRagEnrichment:
    """UnifiedTokenScanner.scan → RAG enrichment wiring."""

    @pytest.mark.asyncio
    async def test_scan_calls_rag_enricher_and_stores_context(self) -> None:
        fake = _FakeEnricher()
        with (
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(UnifiedTokenScanner, "_run_wallet_analysis", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_run_event_correlation", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_run_news_sentiment", new=AsyncMock()),
            patch("app.rag.intelligence.RAGIntelligenceEnricher", new=lambda *a, **k: fake),
        ):
            result = await UnifiedTokenScanner().scan("0xRAGTEST123", chain="ethereum", tier="free")

        # Enricher was called with the live scan result
        assert fake.seen is result
        assert fake.seen.token_address == "0xRAGTEST123"
        # Enricher output was stored on the result
        assert result.rag_context is not None
        assert result.rag_context.similar_scams[0].final_score == 0.8

    @pytest.mark.asyncio
    async def test_scan_rag_failure_is_best_effort(self) -> None:
        with (
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(UnifiedTokenScanner, "_run_wallet_analysis", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_run_event_correlation", new=AsyncMock()),
            patch.object(UnifiedTokenScanner, "_run_news_sentiment", new=AsyncMock()),
            patch("app.rag.intelligence.RAGIntelligenceEnricher", new=lambda *a, **k: _BoomEnricher()),
        ):
            result = await UnifiedTokenScanner().scan("0xRAGTEST123", chain="ethereum", tier="free")

        # Scan still completed and returned a scored result — just no RAG
        assert result is not None
        assert result.rag_context is None
        assert result.safety_score == 50.0
        assert result.token_address == "0xRAGTEST123"


class TestEnrichScanResultUnifiedFormat:
    """RAGIntelligenceEnricher.enrich_scan_result with unified ScanResult."""

    @pytest.mark.asyncio
    async def test_accepts_string_modules_run_and_dev_reputation_deployer(self) -> None:
        enricher = RAGIntelligenceEnricher()

        async def fake_retrieve(
            query: str, collection: str, top_k: int, expand: bool, rerank: bool = True
        ) -> list[ScoredHit]:
            return [
                ScoredHit(
                    content=f"{collection} hit for {query}",
                    score=0.5,
                    final_score=0.6,
                    collection=collection,
                    doc_id=f"{collection}-1",
                )
            ]

        with patch.object(enricher._retriever, "retrieve", new=fake_retrieve):
            ctx = await enricher.enrich_scan_result(_unified_result())

        # Deployer was pulled from unified dev_reputation → wallet context populated
        assert ctx.wallet_context, "expected wallet context from dev_reputation deployer"
        assert ctx.similar_scams, "expected similar scam hits"
        assert ctx.market_context, "expected market context"
        assert ctx.fusion_summary
        assert "scams" in ctx.collections_searched

    @pytest.mark.asyncio
    async def test_accepts_empty_unified_result_without_crashing(self) -> None:
        enricher = RAGIntelligenceEnricher()

        async def fake_retrieve(
            query: str, collection: str, top_k: int, expand: bool, rerank: bool = True
        ) -> list[ScoredHit]:
            return []

        with patch.object(enricher._retriever, "retrieve", new=fake_retrieve):
            ctx = await enricher.enrich_scan_result(
                ScanResult(token_address="0xEMPTY", chain="solana", risk_flags=None)  # type: ignore[arg-type]
            )

        # No deployer, no hits — but enrichment still returns a context, not an error
        assert ctx.similar_scams == []
        assert ctx.wallet_context == []
        assert ctx.market_context == []
        assert ctx.error == ""


class TestRouterRagContextSerialization:
    """v2 API response serialization of rag_context."""

    def test_serialize_rag_context_shape(self) -> None:
        from app.routers.unified_scanner_router import _serialize_rag_context

        out = _serialize_rag_context(_fake_context())

        assert out is not None
        assert out["similar_scams"][0]["content"] == "Past honeypot pattern on same deployer"
        assert out["similar_scams"][0]["score"] == 0.8  # final_score preferred
        assert out["similar_scams"][0]["collection"] == "scams"
        assert out["wallet_context"][0]["collection"] == "wallet"
        assert out["market_signals"] == 1
        assert out["fusion_summary"].startswith("RAG Intelligence")
        assert "scams" in out["collections_searched"]
        assert out["query_id"] == "enrich:test:1"

    def test_serialize_rag_context_none_when_unavailable(self) -> None:
        from app.routers.unified_scanner_router import _serialize_rag_context

        assert _serialize_rag_context(None) is None

    def test_serialize_rag_context_never_raises_on_garbage(self) -> None:
        from app.routers.unified_scanner_router import _serialize_rag_context

        # A malformed payload (missing dataclass attrs) degrades to None
        assert _serialize_rag_context(object()) is None
