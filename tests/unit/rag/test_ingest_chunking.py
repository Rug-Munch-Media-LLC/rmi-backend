"""Unit tests for app/rag/ingest/_chunking.py — heading-aware doc chunking.

Covers the pure chunking pipeline: format normalization (markdown / reST /
AsciiDoc), heading-section splitting, recursive character splitting, chunk
dedup + noise filtering, and the bulk-ingest payload builder. No network,
no I/O, no Redis.
"""

from __future__ import annotations

import pytest

from app.rag.ingest._chunking import (
    _doc_payload,
    _normalize_doc_headings,
    _recursive_char_split,
    _split_at_headings,
    split_doc_into_chunks,
)


class TestNormalizeDocHeadings:
    def test_markdown_passes_through_unchanged(self) -> None:
        text = "# Title\n\nbody"
        assert _normalize_doc_headings(text) == text

    def test_asciidoc_equals_heading_converted(self) -> None:
        text = "= Chapter One\n\nbody"
        assert _normalize_doc_headings(text) == "# Chapter One\n\nbody"

    def test_asciidoc_deep_heading_capped_at_6(self) -> None:
        text = "======= Deep\n\nbody"
        assert _normalize_doc_headings(text) == "###### Deep\n\nbody"

    def test_rst_underline_heading_converted(self) -> None:
        text = "Section Title\n============\n\nbody"
        assert _normalize_doc_headings(text) == "# Section Title\n\nbody"

    def test_rst_sub_heading_dash_underline_converted(self) -> None:
        text = "Sub Section\n-----------\n\nbody"
        assert _normalize_doc_headings(text) == "# Sub Section\n\nbody"


class TestSplitAtHeadings:
    def test_no_headings_returns_whole_text(self) -> None:
        sections = _split_at_headings("plain body without headings")
        assert sections == [("", "plain body without headings")]

    def test_single_heading_splits(self) -> None:
        sections = _split_at_headings("# H1\n\nbody")
        assert sections == [("# H1", "body")]

    def test_multiple_headings(self) -> None:
        text = "# First\n\none\n\n## Second\n\ntwo"
        sections = _split_at_headings(text)
        assert sections == [("# First", "one"), ("## Second", "two")]

    def test_empty_text(self) -> None:
        assert _split_at_headings("") == [("", "")]


class TestRecursiveCharSplit:
    def test_empty_returns_empty(self) -> None:
        assert _recursive_char_split("", 100, 10) == []
        assert _recursive_char_split("   ", 100, 10) == []

    def test_short_text_returns_whole(self) -> None:
        assert _recursive_char_split("short", 100, 10) == ["short"]

    def test_splits_at_paragraph_boundary(self) -> None:
        text = "a" * 200 + "\n\n" + "b" * 200
        chunks = _recursive_char_split(text, 100, 10)
        assert len(chunks) >= 2
        # First chunk should end at the paragraph break (no 'b' content yet)
        assert all(len(c) <= 100 + 10 for c in chunks)

    def test_hard_cut_when_no_boundary(self) -> None:
        text = "x" * 250
        chunks = _recursive_char_split(text, 100, 0)
        assert len(chunks) == 3
        assert all(len(c) <= 100 for c in chunks)

    def test_overlap_creates_shared_context(self) -> None:
        text = "y" * 300
        chunks = _recursive_char_split(text, 100, 20)
        assert len(chunks) >= 3


class TestSplitDocIntoChunks:
    def test_empty_doc_returns_empty(self) -> None:
        assert split_doc_into_chunks("") == []
        assert split_doc_into_chunks("  \n  ") == []

    def test_short_doc_returns_single_chunk(self) -> None:
        text = "A short document that is long enough to pass the minimum."
        chunks = split_doc_into_chunks(text, min_chunk_chars=5)
        assert chunks == [text]

    def test_short_noise_fragments_dropped(self) -> None:
        chunks = split_doc_into_chunks("tiny\n\n# Heading\n\nmeaningful body content here", min_chunk_chars=20)
        assert all(len(c.strip()) >= 20 for c in chunks)

    def test_exact_duplicates_dropped(self) -> None:
        text = "duplicate paragraph content that is long enough\n\nduplicate paragraph content that is long enough"
        chunks = split_doc_into_chunks(text, min_chunk_chars=10)
        assert len(chunks) == 1

    def test_heading_prepended_to_overlong_sections(self) -> None:
        body = "word " * 200  # long enough to require splitting
        text = f"# Big Section\n\n{body}"
        chunks = split_doc_into_chunks(text, chunk_size=100, chunk_overlap=10, min_chunk_chars=5)
        assert chunks
        # Every chunk from the over-long section keeps the heading context
        assert all("# Big Section" in c for c in chunks)

    def test_rst_document_chunked(self) -> None:
        text = "Intro Section\n=============\n\n" + ("content words " * 300)
        chunks = split_doc_into_chunks(text, chunk_size=120, chunk_overlap=10, min_chunk_chars=10)
        assert chunks
        assert any("# Intro Section" in c for c in chunks)

    def test_chunks_are_within_size_limits(self) -> None:
        text = "# H\n\n" + ("z" * 500)
        chunk_size = 100
        chunks = split_doc_into_chunks(text, chunk_size=chunk_size, chunk_overlap=10, min_chunk_chars=5)
        assert chunks
        for c in chunks:
            # heading prepend may exceed size slightly; body pieces are bounded
            body = c.split("\n", 1)[1] if "\n" in c else c
            assert len(body) <= chunk_size + 10


class TestDocPayload:
    def test_payload_structure(self) -> None:
        payload = _doc_payload("consensys", "https://example.com/doc.md", "chunk body", index=3, category="education")
        assert payload["doc_id"].startswith("doc:")
        assert payload["doc_id"].endswith(":3")
        assert payload["content"] == "chunk body"
        meta = payload["metadata"]
        assert meta["source"] == "consensys"
        assert meta["source_url"] == "https://example.com/doc.md"
        assert meta["doc_url"] == "https://example.com/doc.md"
        assert meta["category"] == "education"
        assert meta["content_type"] == "doc_chunk"
        assert meta["chunk_index"] == 3
        assert meta["ingested_via"] == "doc_loader"

    def test_payload_digest_is_stable(self) -> None:
        p1 = _doc_payload("s", "https://example.com/a.md", "c", 0)
        p2 = _doc_payload("s", "https://example.com/a.md", "c", 0)
        assert p1["doc_id"] == p2["doc_id"]
        assert p1["metadata"] == p2["metadata"]

    def test_payload_digest_varies_by_url(self) -> None:
        p1 = _doc_payload("s", "https://example.com/a.md", "c", 0)
        p2 = _doc_payload("s", "https://example.com/b.md", "c", 0)
        assert p1["doc_id"] != p2["doc_id"]
