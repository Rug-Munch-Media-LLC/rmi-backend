"""Unit tests for app/rag/ingest/_seed_corpus.py — curated JSONL seed corpus.

Covers the pure loader (parse/validate/duplicate-id/unknown-category handling),
the stable synthetic URL + payload builders, and the ingest pipeline with the
dedup/ingest boundaries monkeypatched (no network, no Redis).
"""

from __future__ import annotations

import json

import pytest

import app.rag.ingest_crypto_knowledge as _ingest
from app.rag.ingest._seed_corpus import (
    SEED_CATEGORY_COLLECTIONS,
    SEED_CORPUS_DEFAULT_PATH,
    _seed_payload,
    _seed_synthetic_url,
    ingest_seed_corpus,
    load_seed_corpus,
)


def _write_corpus(tmp_path, lines: list[str]) -> object:
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _valid_entry(entry_id: str = "def:amm") -> str:
    return json.dumps(
        {"id": entry_id, "title": "AMM Basics", "content": "An automated market maker uses pools.", "category": "defi"}
    )


class TestLoadSeedCorpus:
    def test_missing_file_returns_error(self, tmp_path) -> None:
        entries, errors = load_seed_corpus(tmp_path / "nope.jsonl")
        assert entries == []
        assert errors and "seed_file_missing" in errors[0]

    def test_valid_entry_loaded_with_source_forced(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, [_valid_entry()])
        entries, errors = load_seed_corpus(path)
        assert errors == []
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == "def:amm"
        assert entry["source"] == "seed-corpus"
        assert entry["tags"] == []

    def test_comments_and_blank_lines_skipped(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, ["# comment", "", _valid_entry()])
        entries, errors = load_seed_corpus(path)
        assert len(entries) == 1
        assert errors == []

    def test_invalid_json_reported(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, ["{not json", _valid_entry()])
        entries, errors = load_seed_corpus(path)
        assert len(entries) == 1
        assert any("invalid JSON" in e for e in errors)

    def test_non_object_line_reported(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, ['["a", "b"]', _valid_entry()])
        entries, errors = load_seed_corpus(path)
        assert len(entries) == 1
        assert any("not a JSON object" in e for e in errors)

    def test_missing_required_field_reported(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, [json.dumps({"id": "x", "title": "no content or category"})])
        entries, errors = load_seed_corpus(path)
        assert entries == []
        assert any("missing required field(s)" in e for e in errors)

    def test_duplicate_ids_reported_and_skipped(self, tmp_path) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("dup"), _valid_entry("dup")])
        entries, errors = load_seed_corpus(path)
        assert len(entries) == 1
        assert any("duplicate id" in e for e in errors)

    def test_unknown_category_reported(self, tmp_path) -> None:
        line = json.dumps({"id": "x", "title": "T", "content": "C", "category": "bogus"})
        path = _write_corpus(tmp_path, [line])
        entries, errors = load_seed_corpus(path)
        assert entries == []
        assert any("unknown category" in e for e in errors)

    def test_string_tags_split_on_comma(self, tmp_path) -> None:
        line = json.dumps({"id": "x", "title": "T", "content": "C", "category": "defi", "tags": "one,two, three"})
        path = _write_corpus(tmp_path, [line])
        entries, errors = load_seed_corpus(path)
        assert errors == []
        assert entries[0]["tags"] == ["one", "two", "three"]

    def test_bad_tags_type_reported(self, tmp_path) -> None:
        line = json.dumps({"id": "x", "title": "T", "content": "C", "category": "defi", "tags": 42})
        path = _write_corpus(tmp_path, [line])
        entries, errors = load_seed_corpus(path)
        assert entries == []
        assert any("tags must be a list" in e for e in errors)


class TestSyntheticUrlAndPayload:
    def test_synthetic_url_uses_invalid_tld(self) -> None:
        url = _seed_synthetic_url("def:amm")
        assert url.startswith("https://seed-corpus.invalid/")
        assert url.endswith("def:amm")

    def test_payload_structure(self) -> None:
        entry = {"id": "def:amm", "title": "AMM", "content": "body", "category": "defi", "tags": ["amm"]}
        payload = _seed_payload(entry, "phishing_db")
        assert payload["doc_id"].startswith("seed:")
        assert "AMM" in payload["content"]
        assert payload["metadata"]["source"] == "seed-corpus"
        assert payload["metadata"]["content_type"] == "seed_knowledge"
        assert payload["metadata"]["ingested_via"] == "seed_corpus_loader"

    def test_category_collection_map_covers_categories(self) -> None:
        # Every seed category routes to a concrete collection
        for category in ("definition", "defi", "market", "tokenomics", "onchain", "scam", "security", "ta"):
            assert SEED_CATEGORY_COLLECTIONS[category]


class TestIngestSeedCorpus:
    @pytest.mark.asyncio
    async def test_dry_run_with_no_duplicates(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("def:amm")])
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(_ingest, "content_hash", lambda c: f"hash:{c[:8]}")

        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=True)
        assert summary["total_entries"] == 1
        assert summary["invalid_entries"] == 0
        assert summary["to_ingest"] == 1
        assert summary["ingested"] == 1
        assert "phishing_db" in summary["collections"]

    @pytest.mark.asyncio
    async def test_url_duplicate_skipped(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("def:amm")])
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: True)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)

        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=True)
        assert summary["skipped_url_duplicates"] == 1
        assert summary["to_ingest"] == 0

    @pytest.mark.asyncio
    async def test_content_duplicate_skipped(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("def:amm")])
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: True)

        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=True)
        assert summary["skipped_content_duplicates"] == 1
        assert summary["to_ingest"] == 0

    @pytest.mark.asyncio
    async def test_force_bypasses_dedup(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("def:amm")])
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: True)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: True)

        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=True, force=True)
        assert summary["skipped_url_duplicates"] == 0
        assert summary["skipped_content_duplicates"] == 0
        assert summary["to_ingest"] == 1

    @pytest.mark.asyncio
    async def test_malformed_corpus_returns_invalid_count(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, ["{bad json"])
        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=True)
        assert summary["total_entries"] == 0
        assert summary["invalid_entries"] == 1

    @pytest.mark.asyncio
    async def test_non_dry_run_marks_ingested(self, tmp_path, monkeypatch) -> None:
        path = _write_corpus(tmp_path, [_valid_entry("def:amm")])
        monkeypatch.setattr(_ingest, "is_duplicate_url", lambda url: False)
        monkeypatch.setattr(_ingest, "is_duplicate_content", lambda fp, col: False)
        monkeypatch.setattr(_ingest, "content_hash", lambda c: f"hash:{c[:8]}")
        marked_urls: list[str] = []
        marked_contents: list[tuple[str, str]] = []

        async def fake_ingest_collection(base_url, collection, items, dry_run=False):
            return {"collection": collection, "items": len(items), "ok": len(items), "status": "ok"}

        monkeypatch.setattr(_ingest, "mark_url_ingested", lambda url: marked_urls.append(url))
        monkeypatch.setattr(_ingest, "mark_content_ingested", lambda fp, col: marked_contents.append((fp, col)))
        monkeypatch.setattr("app.rag.ingest._seed_corpus.ingest_collection", fake_ingest_collection)

        summary = await ingest_seed_corpus(base_url="http://x", seed_file=path, dry_run=False)
        assert summary["ingested"] == 1
        assert len(marked_urls) == 1
        assert len(marked_contents) == 1
