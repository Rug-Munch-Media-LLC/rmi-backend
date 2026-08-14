"""Unit tests for app/rag/ingest/_incidents.py — incident normalization + ingest.

Covers the source-registry loader, canonical record normalization (chain /
severity / amount / date / url), URL dedup, payload building, envelope
unwrapping, and the fetch+ingest pipeline with httpx monkeypatched (no
network).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

import app.rag.ingest_crypto_knowledge as _ingest
from app.rag.ingest._incidents import (
    _extract_records,
    _incident_doc_id,
    _incident_payload,
    _incident_url,
    _normalize_amount,
    _normalize_chain,
    _normalize_date,
    _normalize_severity,
    _synthetic_incident_url,
    collection_names_for_category,
    dedupe_incidents,
    fetch_incident_source,
    ingest_incidents_from_sources,
    load_incident_sources,
    normalize_incident,
)


class TestLoadIncidentSources:
    def test_loads_enabled_json_api_sources(self) -> None:
        sources = load_incident_sources()
        assert isinstance(sources, list)
        assert sources, "crawl_sources.json should declare incident sources"
        for s in sources:
            assert s["type"] in ("json_api", "html_page")
            assert s["category"] in ("rug", "exploit", "scam", "education")
            assert isinstance(s["collections"], list) and s["collections"]

    def test_defillama_hacks_present(self) -> None:
        names = {s["name"] for s in load_incident_sources()}
        assert "defillama_hacks" in names


class TestNormalizeIncident:
    def test_non_dict_returns_none(self) -> None:
        assert normalize_incident("not a dict", "src") is None

    def test_missing_title_returns_none(self) -> None:
        assert normalize_incident({"description": "x"}, "src") is None

    def test_title_from_name_and_project(self) -> None:
        out = normalize_incident({"name": "Orbit Bridge"}, "src")
        assert out is not None
        assert out["title"] == "Orbit Bridge"

    def test_description_fallback_chain(self) -> None:
        out = normalize_incident({"title": "T", "technique": "price oracle manipulation"}, "src")
        assert out is not None
        assert out["description"] == "price oracle manipulation"

    def test_category_defaults_from_source(self) -> None:
        out = normalize_incident({"title": "T"}, "defillama_hacks")
        assert out is not None
        assert out["category"] == "exploit"

    def test_category_preserved_when_valid(self) -> None:
        out = normalize_incident({"title": "T", "category": "rug"}, "defillama_hacks")
        assert out is not None
        assert out["category"] == "rug"

    def test_title_truncated_to_500(self) -> None:
        out = normalize_incident({"title": "x" * 600}, "src")
        assert out is not None
        assert len(out["title"]) == 500

    def test_url_from_link_field(self) -> None:
        out = normalize_incident({"title": "T", "link": "https://example.com/hack"}, "src")
        assert out is not None
        assert out["url"] == "https://example.com/hack"

    def test_synthetic_url_when_missing(self) -> None:
        out = normalize_incident({"title": "T"}, "defillama_hacks")
        assert out is not None
        assert out["url"].startswith("https://defillama_hacks/incident/")


class TestNormalizeHelpers:
    def test_chain_list_dedupes(self) -> None:
        assert _normalize_chain(["eth", "eth", "bsc"]) == "eth, bsc"
        assert _normalize_chain([]) == "unknown"
        assert _normalize_chain("solana") == "solana"
        assert _normalize_chain(None) == "unknown"

    def test_severity_validation(self) -> None:
        assert _normalize_severity("critical") == "critical"
        assert _normalize_severity("HIGH") == "high"
        assert _normalize_severity("bogus") == "medium"
        assert _normalize_severity(None) == "medium"

    def test_amount_normalization(self) -> None:
        assert _normalize_amount(1_000_000) == 1_000_000
        assert _normalize_amount("1,000,000") == 1_000_000
        assert _normalize_amount("$12.5") == 12
        assert _normalize_amount(None) == 0
        assert _normalize_amount(True) == 0
        assert _normalize_amount("n/a") == 0

    def test_date_normalization(self) -> None:
        assert _normalize_date(1_700_000_000) == "2023-11-14T22:13:20+00:00"
        assert _normalize_date("2024-01-02T00:00:00Z").startswith("2024-01-02T00:00:00")
        assert _normalize_date("not a date") == "not a date"
        assert _normalize_date(None) == ""

    def test_incident_url_validation(self) -> None:
        assert _incident_url({"url": "https://ok.example"}, "s", "t") == "https://ok.example"
        # non-http scheme is rejected → synthetic
        url = _incident_url({"url": "ftp://bad"}, "defillama_hacks", "title")
        assert url.startswith("https://defillama_hacks/incident/")

    def test_synthetic_incident_url_stable(self) -> None:
        assert _synthetic_incident_url("src", "title") == _synthetic_incident_url("src", "title")
        assert _synthetic_incident_url("src", "title") != _synthetic_incident_url("src", "other")

    def test_incident_doc_id_stable(self) -> None:
        assert _incident_doc_id("https://a.example") == _incident_doc_id("https://a.example")
        assert _incident_doc_id("https://a.example").startswith("incident:")


class TestDedupeAndCollections:
    def test_dedupe_keeps_first_by_url(self) -> None:
        a = {"url": "https://example.com/a", "title": "A"}
        b = {"url": "https://example.com/a/", "title": "B"}
        out = dedupe_incidents([a, b])
        assert len(out) == 1
        assert out[0]["title"] == "A"

    def test_dedupe_empty_url_dropped(self) -> None:
        out = dedupe_incidents([{"url": "", "title": "A"}, {"url": "https://x", "title": "B"}])
        assert len(out) == 1

    def test_collection_names_for_category(self) -> None:
        assert collection_names_for_category("rug") == ("known_scams", "scam_patterns")
        assert collection_names_for_category("exploit") == ("defi_exploits", "threat_intel")
        assert collection_names_for_category("unknown") == ("scam_patterns", "known_scams")


class TestPayloadAndEnvelope:
    def test_incident_payload_structure(self) -> None:
        incident = normalize_incident({"title": "Hack", "amount_lost": 500000, "chain": "eth"}, "defillama_hacks")
        assert incident is not None
        payload = _incident_payload(incident)
        assert payload["doc_id"].startswith("incident:")
        assert "Hack" in payload["content"]
        assert payload["metadata"]["content_type"] == "incident"
        assert payload["metadata"]["ingested_via"] == "incident_loader"
        assert payload["metadata"]["amount_lost"] == 500000

    def test_extract_records_envelopes(self) -> None:
        assert _extract_records([1, 2]) == [1, 2]
        assert _extract_records({"data": [1, 2]}) == [1, 2]
        assert _extract_records({"hacks": [1]}) == [1]
        assert _extract_records({"results": [1]}) == [1]
        assert _extract_records({"records": [1]}) == [1]
        assert _extract_records({"incidents": [1]}) == [1]
        assert _extract_records({"data": {"nested": 1}}) == []
        assert _extract_records("plain") == []


class TestFetchIncidentSource:
    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        cfg = {"name": "src", "url": "https://example.com/feed.json"}
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        mock_resp.json = Mock(return_value={"data": [{"title": "Hack A"}, {"title": "Hack B"}]})
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            records = await fetch_incident_source(cfg, max_records=10)
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_max_records_respected(self) -> None:
        cfg = {"name": "src", "url": "https://example.com/feed.json"}
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = Mock()
        mock_resp.json = Mock(return_value=[{"title": f"H{i}"} for i in range(20)])
        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            records = await fetch_incident_source(cfg, max_records=5)
        assert len(records) == 5

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        cfg = {"name": "src", "url": "https://example.com/feed.json"}
        import httpx

        with patch(
            "httpx.AsyncClient.get",
            new=AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500", request=httpx.Request("GET", cfg["url"]), response=httpx.Response(500)
                )
            ),
        ):
            records = await fetch_incident_source(cfg)
        assert records == []

    @pytest.mark.asyncio
    async def test_missing_url_returns_empty(self) -> None:
        assert await fetch_incident_source({"name": "src"}) == []


class TestIngestIncidentsFromSources:
    @pytest.mark.asyncio
    async def test_dry_run_pipeline(self, monkeypatch) -> None:
        monkeypatch.setattr(
            _ingest,
            "is_duplicate_url",
            lambda url: False,
        )
        source_cfg = {
            "name": "defillama_hacks",
            "type": "json_api",
            "url": "https://example.com/hacks.json",
            "category": "exploit",
            "collections": ["defi_exploits", "threat_intel"],
            "enabled": True,
        }
        with (
            patch("app.rag.ingest._incidents.load_incident_sources", return_value=[source_cfg]),
            patch(
                "app.rag.ingest._incidents.fetch_incident_source",
                new=AsyncMock(return_value=[{"title": "Hack A", "chain": "eth"}]),
            ),
        ):
            summary = await ingest_incidents_from_sources(base_url="http://x", dry_run=True, max_per_source=100)
        assert summary["enabled"] == 1
        assert summary["total_normalized"] == 1
        src_summary = summary["sources"]["defillama_hacks"]
        assert src_summary["fetched"] == 1
        assert src_summary["normalized"] == 1
        assert src_summary["ingested"] == 1

    @pytest.mark.asyncio
    async def test_already_ingested_url_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: True)
        source_cfg = {
            "name": "defillama_hacks",
            "type": "json_api",
            "url": "https://example.com/hacks.json",
            "category": "exploit",
            "collections": ["defi_exploits"],
            "enabled": True,
        }
        with (
            patch("app.rag.ingest._incidents.load_incident_sources", return_value=[source_cfg]),
            patch(
                "app.rag.ingest._incidents.fetch_incident_source",
                new=AsyncMock(return_value=[{"title": "Hack A"}]),
            ),
        ):
            summary = await ingest_incidents_from_sources(base_url="http://x", dry_run=True, max_per_source=100)
        assert summary["sources"]["defillama_hacks"]["skipped_already_ingested"] == 1
        assert summary["sources"]["defillama_hacks"]["ingested"] == 0

    @pytest.mark.asyncio
    async def test_no_enabled_sources(self) -> None:
        with patch("app.rag.ingest._incidents.load_incident_sources", return_value=[]):
            summary = await ingest_incidents_from_sources(base_url="http://x", dry_run=True)
        assert summary["enabled"] == 0

    @pytest.mark.asyncio
    async def test_source_filter_by_name(self, monkeypatch) -> None:
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        enabled = {
            "name": "defillama_hacks",
            "type": "json_api",
            "url": "https://example.com/hacks.json",
            "category": "exploit",
            "collections": ["defi_exploits"],
            "enabled": True,
        }
        disabled = {
            "name": "slowmist_hacked",
            "type": "json_api",
            "url": "https://example.com/slowmist.json",
            "category": "scam",
            "collections": ["scam_patterns"],
            "enabled": True,
        }
        with (
            patch("app.rag.ingest._incidents.load_incident_sources", return_value=[enabled, disabled]),
            patch(
                "app.rag.ingest._incidents.fetch_incident_source",
                new=AsyncMock(return_value=[]),
            ),
        ):
            summary = await ingest_incidents_from_sources(
                base_url="http://x", sources=["defillama_hacks"], dry_run=True, max_per_source=100
            )
        assert set(summary["sources"].keys()) == {"defillama_hacks"}
