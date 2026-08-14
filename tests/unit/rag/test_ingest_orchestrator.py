"""Unit tests for app/rag/ingest/_orchestrator.py — bulk-ingest client + doc sources.

Covers the write-header resolver, the bulk-ingest client (dry-run + error),
doc-source config loading + fetching (httpx monkeypatched), the raw-doc
ingest pipeline, and the knowledge-base ingest-all path (dry-run + dedup
monkeypatched). No network, no Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.rag.ingest_crypto_knowledge as _ingest
from app.rag.ingest._knowledge_bases import KNOWLEDGE_BASES
from app.rag.ingest._orchestrator import (
    _BULK_CHUNK_SIZE,
    _write_headers,
    fetch_doc_source,
    ingest_all,
    ingest_collection,
    ingest_doc_sources,
    load_doc_sources,
)


class TestWriteHeaders:
    def test_headers_include_auth_and_content_type(self) -> None:
        headers = _write_headers()
        assert headers["Authorization"] == "Bearer sk-local"
        assert headers["X-RMI-Key"]
        assert headers["Content-Type"] == "application/json"

    def test_raises_when_internal_key_unresolvable(self, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "app.core.internal_key":
                raise ImportError("simulated missing internal key module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
            _write_headers()


class TestIngestCollection:
    @pytest.mark.asyncio
    async def test_dry_run_returns_summary(self) -> None:
        result = await ingest_collection("http://x", "scam_intel", [{"doc_id": "a"}], dry_run=True)
        assert result == {"collection": "scam_intel", "items": 1, "status": "dry_run"}

    @pytest.mark.asyncio
    async def test_successful_post(self) -> None:
        mock_resp = Mock()
        mock_resp.raise_for_status = Mock()
        mock_resp.json = Mock(return_value={"ok": 1, "status": "ok"})
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
            result = await ingest_collection("http://x", "scam_intel", [{"doc_id": "a"}], dry_run=False)
        assert result == {"ok": 1, "status": "ok"}

    @pytest.mark.asyncio
    async def test_http_error_returns_error_dict(self) -> None:
        request = httpx.Request("POST", "http://x/api/v1/rag/v2/bulk-ingest")
        exc = httpx.HTTPStatusError("500", request=request, response=httpx.Response(500))
        with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=exc)):
            result = await ingest_collection("http://x", "scam_intel", [{"doc_id": "a"}], dry_run=False)
        assert "error" in result
        assert result["collection"] == "scam_intel"


class TestLoadDocSources:
    def test_loads_education_sources_only(self) -> None:
        sources = load_doc_sources()
        assert isinstance(sources, list)
        if sources:
            for s in sources:
                assert s["category"] == "education"
                assert s["type"] in ("json_api", "html_page")

    def test_consensys_source_present(self) -> None:
        names = {s["name"] for s in load_doc_sources()}
        assert "consensys_best_practices" in names


class TestFetchDocSource:
    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        mock_resp.text = "# Markdown doc\n\ncontent"
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            text = await fetch_doc_source({"name": "s", "url": "https://example.com/doc.md"})
        assert "# Markdown doc" in text

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self) -> None:
        request = httpx.Request("GET", "https://example.com/doc.md")
        exc = httpx.HTTPStatusError("404", request=request, response=httpx.Response(404))
        with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=exc)):
            text = await fetch_doc_source({"name": "s", "url": "https://example.com/doc.md"})
        assert text is None

    @pytest.mark.asyncio
    async def test_empty_body_returns_none(self) -> None:
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        mock_resp.text = "   "
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            text = await fetch_doc_source({"name": "s", "url": "https://example.com/doc.md"})
        assert text is None

    @pytest.mark.asyncio
    async def test_missing_url_returns_none(self) -> None:
        assert await fetch_doc_source({"name": "s"}) is None


class TestIngestDocSources:
    @pytest.mark.asyncio
    async def test_no_enabled_sources(self) -> None:
        with patch("app.rag.ingest._orchestrator.load_doc_sources", return_value=[]):
            summary = await ingest_doc_sources(base_url="http://x")
        assert summary["enabled"] == 0

    @pytest.mark.asyncio
    async def test_dry_run_ingests_fresh_chunks(self, monkeypatch) -> None:
        import hashlib

        cfg = {
            "name": "consensys_best_practices",
            "type": "json_api",
            "url": "https://example.com/doc.md",
            "category": "education",
            "collections": ["coding_knowledge", "threat_intel"],
            "enabled": True,
        }
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(
            _ingest,
            "content_hash",
            lambda c: hashlib.sha256(c.encode("utf-8", errors="ignore")).hexdigest()[:16],
        )
        with (
            patch("app.rag.ingest._orchestrator.load_doc_sources", return_value=[cfg]),
            patch(
                "app.rag.ingest._orchestrator.fetch_doc_source",
                new=AsyncMock(return_value="# Title\n\n" + ("body words " * 200)),
            ),
        ):
            summary = await ingest_doc_sources(base_url="http://x", dry_run=True)
        assert summary["enabled"] == 1
        src = summary["sources"]["consensys_best_practices"]
        assert src["chunks"] > 1
        assert src["ingested"] == src["chunks"]
        assert src["collections"] == ["coding_knowledge", "threat_intel"]

    @pytest.mark.asyncio
    async def test_fetch_failure_recorded(self, monkeypatch) -> None:
        cfg = {
            "name": "consensys_best_practices",
            "type": "json_api",
            "url": "https://example.com/doc.md",
            "category": "education",
            "collections": ["coding_knowledge"],
            "enabled": True,
        }
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        with (
            patch("app.rag.ingest._orchestrator.load_doc_sources", return_value=[cfg]),
            patch("app.rag.ingest._orchestrator.fetch_doc_source", new=AsyncMock(return_value=None)),
        ):
            summary = await ingest_doc_sources(base_url="http://x", dry_run=True)
        assert summary["sources"]["consensys_best_practices"]["error"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_url_duplicate_skipped(self, monkeypatch) -> None:
        cfg = {
            "name": "consensys_best_practices",
            "type": "json_api",
            "url": "https://example.com/doc.md",
            "category": "education",
            "collections": ["coding_knowledge"],
            "enabled": True,
        }
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: True)
        with (
            patch("app.rag.ingest._orchestrator.load_doc_sources", return_value=[cfg]),
            patch("app.rag.ingest._orchestrator.fetch_doc_source", new=AsyncMock()) as fetch,
        ):
            summary = await ingest_doc_sources(base_url="http://x", dry_run=True)
        fetch.assert_not_awaited()
        assert summary["sources"]["consensys_best_practices"]["skipped"] == "url_already_ingested"


class TestIngestAll:
    @pytest.mark.asyncio
    async def test_dry_run_returns_all_collections(self, monkeypatch) -> None:
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(_ingest, "content_hash", lambda c: "hash")

        results = await ingest_all(base_url="http://x", dry_run=True)
        for collection in KNOWLEDGE_BASES:
            assert collection in results
            assert "skipped_already_ingested" in results[collection]
        assert "_summary" in results

    @pytest.mark.asyncio
    async def test_already_ingested_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: True)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(_ingest, "content_hash", lambda c: "hash")

        results = await ingest_all(base_url="http://x", dry_run=True, collections=["scam_intel"])
        assert results["scam_intel"]["skipped_already_ingested"] == len(KNOWLEDGE_BASES["scam_intel"])
        assert results["_summary"]["total_skipped_already_ingested"] == len(KNOWLEDGE_BASES["scam_intel"])

    @pytest.mark.asyncio
    async def test_unknown_collection_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(_ingest, "content_hash", lambda c: "hash")

        results = await ingest_all(base_url="http://x", dry_run=True, collections=["does_not_exist"])
        assert results["does_not_exist"] == {"status": "skipped", "reason": "no knowledge base defined"}

    @pytest.mark.asyncio
    async def test_knowledge_bases_data_is_well_formed(self) -> None:
        for collection, items in KNOWLEDGE_BASES.items():
            assert items, f"collection {collection} should not be empty"
            for item in items:
                assert item["doc_id"]
                assert item["content"]
                assert isinstance(item.get("metadata"), dict)


class TestBulkChunkSize:
    def test_bulk_chunk_size_matches_api_limit(self) -> None:
        assert _BULK_CHUNK_SIZE == 500
