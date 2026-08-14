"""Pure tests for the raw-doc RAG ingest path (``ingest_doc_sources``).

No network, no DB, no real embedding: ``httpx.AsyncClient`` is mocked (GET =
raw doc text, POST = the bulk-ingest API the embedder lives behind) and the
Redis-backed dedup helpers are patched so nothing touches Redis/gopass. The
chunking helpers are pure and exercised directly.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx

from app.rag.ingest_crypto_knowledge import (
    content_hash,
    ingest_doc_sources,
    split_doc_into_chunks,
)

ROOT = Path(__file__).resolve().parents[1]
CRAWL_SOURCES = ROOT / "app" / "rag" / "crawl_sources.json"

DOC_SOURCE = "consensys_best_practices"

MARKDOWN_DOC = """\
# Reentrancy

Reentrancy is a vulnerability where a function makes an external call to another
contract before it resolves its own state. An attacker can use a fallback
function to re-enter the vulnerable function with the same state and drain
funds. The canonical fix is checks-effects-interactions.

## Mitigations

Use a reentrancy guard, apply checks-effects-interactions, or prefer
pull-over-push payments.

# Integer Overflow

Old Solidity versions allowed integers to wrap around silently. Arithmetic
should be checked or wrapped in SafeMath-style operations.
"""


# ── Helpers ───────────────────────────────────────────────────────────────


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return {"ok": 1, "total": 1}


class FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient (records calls, no network)."""

    def __init__(self, get_map: dict[str, FakeResponse] | None = None) -> None:
        self.get_map = get_map or {}
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.get_calls.append((url, kwargs))
        return self.get_map.get(url, FakeResponse(status_code=404))

    async def post(self, url: str, json: dict | None = None, **kwargs: object) -> FakeResponse:
        self.post_calls.append((url, json or {}))
        return FakeResponse(status_code=200)


@contextlib.contextmanager
def _mock_httpx(fake: FakeAsyncClient):
    with patch("httpx.AsyncClient", return_value=fake):
        yield fake


@contextlib.contextmanager
def _mock_dedup(url_duplicate: bool = False, content_duplicate: bool = False):
    """Fail-open Redis dedup: allow (or force) through without any backend."""
    with (
        patch("app.rag.ingest_crypto_knowledge.is_duplicate_url", return_value=url_duplicate),
        patch("app.rag.ingest_crypto_knowledge.mark_url_ingested") as mark_url,
        patch("app.rag.ingest_crypto_knowledge.is_duplicate_content", return_value=content_duplicate),
        patch("app.rag.ingest_crypto_knowledge.mark_content_ingested") as mark_content,
    ):
        yield mark_url, mark_content


def _source_url(name: str) -> str:
    data = json.loads(CRAWL_SOURCES.read_text(encoding="utf-8"))
    for src in data["incident_sources"]["sources"]:
        if src["name"] == name:
            return src["url"]
    raise AssertionError(f"source {name} missing from registry")


DOC_URL = _source_url(DOC_SOURCE)


# ── Registry: education sources must be enabled ───────────────────────────


def test_education_sources_are_enabled() -> None:
    data = json.loads(CRAWL_SOURCES.read_text(encoding="utf-8"))
    edu = [s for s in data["incident_sources"]["sources"] if s.get("category") == "education"]
    assert len(edu) >= 5
    for src in edu:
        assert src["enabled"] is True, f"education source {src['name']} must be enabled"


# ── ingest_doc_sources: fetch → chunk → dedup → ingest ────────────────────


async def test_ingest_doc_sources_fetches_chunks_dedupes_and_ingests() -> None:
    fake = FakeAsyncClient(get_map={DOC_URL: FakeResponse(text=MARKDOWN_DOC)})
    with _mock_httpx(fake), _mock_dedup() as (mark_url, mark_content):
        summary = await ingest_doc_sources(base_url="http://test", sources=[DOC_SOURCE])

    entry = summary["sources"][DOC_SOURCE]
    assert "error" not in entry, entry
    assert entry["chunks"] >= 2, "expected multiple heading-aware chunks"
    assert entry["deduped"] == 0
    assert entry["ingested"] > 0
    assert set(entry["collections"]) == {"coding_knowledge", "threat_intel"}

    # One bulk-ingest POST per target collection.
    assert len(fake.post_calls) == 2
    for url, body in fake.post_calls:
        assert url.endswith("/api/v1/rag/v2/bulk-ingest")
        assert body["collection"] in {"coding_knowledge", "threat_intel"}
        items = body["items"]
        assert len(items) == entry["chunks"]
        first = items[0]
        assert first["doc_id"].startswith("doc:")
        assert first["metadata"]["source"] == DOC_SOURCE
        assert first["metadata"]["source_url"] == DOC_URL
        assert first["metadata"]["content_type"] == "doc_chunk"
        assert "# Reentrancy" in first["content"]

    # URL + per-chunk content hashes recorded (fail-open marks).
    mark_url.assert_called_once_with(DOC_URL)
    assert mark_content.call_count >= entry["chunks"]


async def test_ingest_doc_sources_skips_failing_source() -> None:
    fake = FakeAsyncClient(get_map={})  # no matching URL → 404
    with _mock_httpx(fake), _mock_dedup():
        summary = await ingest_doc_sources(base_url="http://test", sources=[DOC_SOURCE])

    assert summary["sources"][DOC_SOURCE]["error"] == "fetch_failed"
    assert fake.post_calls == [], "failed source must not reach ingest"
    assert summary["total_chunks"] == 0


async def test_ingest_doc_sources_skips_already_ingested_url() -> None:
    fake = FakeAsyncClient(get_map={DOC_URL: FakeResponse(text=MARKDOWN_DOC)})
    with _mock_httpx(fake), _mock_dedup(url_duplicate=True):
        summary = await ingest_doc_sources(base_url="http://test", sources=[DOC_SOURCE])

    assert summary["sources"][DOC_SOURCE]["skipped"] == "url_already_ingested"
    assert fake.get_calls == [], "URL dedup must gate before fetching"
    assert fake.post_calls == []


async def test_ingest_doc_sources_content_hash_dedup_skips_chunks() -> None:
    fake = FakeAsyncClient(get_map={DOC_URL: FakeResponse(text=MARKDOWN_DOC)})
    # Every chunk already ingested → nothing fresh reaches the API.
    with _mock_httpx(fake), _mock_dedup(content_duplicate=True) as (mark_url, mark_content):
        summary = await ingest_doc_sources(base_url="http://test", sources=[DOC_SOURCE])

    entry = summary["sources"][DOC_SOURCE]
    assert entry["chunks"] >= 2
    assert entry["deduped"] == entry["chunks"]
    assert entry["ingested"] == 0
    assert fake.post_calls == []
    mark_url.assert_not_called()
    mark_content.assert_not_called()


async def test_ingest_doc_sources_filters_to_requested_names() -> None:
    fake = FakeAsyncClient(get_map={DOC_URL: FakeResponse(text=MARKDOWN_DOC)})
    with _mock_httpx(fake), _mock_dedup():
        summary = await ingest_doc_sources(base_url="http://test", sources=["does_not_exist"])

    assert summary["sources"] == {}
    assert fake.get_calls == []
    assert fake.post_calls == []


# ── split_doc_into_chunks: heading-aware chunking (pure) ──────────────────


def test_split_doc_into_chunks_heading_aware_markdown() -> None:
    chunks = split_doc_into_chunks(MARKDOWN_DOC, chunk_size=512, chunk_overlap=0, min_chunk_chars=8)
    assert len(chunks) >= 3
    joined = "\n".join(chunks)
    for heading in ("# Reentrancy", "## Mitigations", "# Integer Overflow"):
        assert heading in joined
    # A heading boundary must never be buried mid-chunk: each chunk carries
    # at most one section heading.
    for chunk in chunks:
        assert chunk.count("\n# ") <= 1


def test_split_doc_into_chunks_recursively_splits_long_sections() -> None:
    long_doc = "# Long Section\n\n" + ("Reentrancy is dangerous. " * 40)
    chunks = split_doc_into_chunks(long_doc, chunk_size=128, chunk_overlap=0, min_chunk_chars=8)
    assert len(chunks) > 1
    assert all(len(c) <= 256 for c in chunks)  # ≤ chunk_size + heading context
    assert all("# Long Section" in c for c in chunks), "heading context must survive sub-splits"


def test_split_doc_into_chunks_handles_rst_underline_headings() -> None:
    rst = (
        "Security Considerations\n"
        "=======================\n\n"
        "Body one: pitfalls to avoid in Solidity code.\n\n"
        "Mitigations\n"
        "-----------\n\n"
        "Body two: apply the recommended patterns."
    )
    chunks = split_doc_into_chunks(rst, chunk_size=256, chunk_overlap=0, min_chunk_chars=8)
    joined = "\n".join(chunks)
    assert "# Security Considerations" in joined
    assert "# Mitigations" in joined


def test_split_doc_into_chunks_handles_asciidoc_headings() -> None:
    adoc = "= Upgradeable\n\nIntro to proxies.\n\n== Proxy Risks\n\nStorage collisions and unsafe upgrade flows."
    chunks = split_doc_into_chunks(adoc, chunk_size=256, chunk_overlap=0, min_chunk_chars=8)
    joined = "\n".join(chunks)
    assert "# Upgradeable" in joined
    assert "## Proxy Risks" in joined


def test_split_doc_into_chunks_drops_duplicates_and_noise() -> None:
    body = "Enough real content here to survive the minimum length filter."
    doc = f"# Keep\n{body}\n\n# Keep\n{body}"
    chunks = split_doc_into_chunks(doc, chunk_size=512, chunk_overlap=0, min_chunk_chars=8)
    # Identical sections (same heading + body) collapse to a single chunk.
    assert len(chunks) == 1
    # Noise fragments below min_chunk_chars are dropped.
    noisy = "# Header\n\nx\n\n# Other\n" + body
    chunks = split_doc_into_chunks(noisy, chunk_size=512, chunk_overlap=0, min_chunk_chars=64)
    assert all(len(c) >= 64 for c in chunks)
    assert all(c != "x" for c in chunks)


def test_split_doc_into_chunks_empty_input() -> None:
    assert split_doc_into_chunks("") == []
    assert split_doc_into_chunks("   \n\t ") == []


# ── dedup helpers stay wired to app.rag.dedup ─────────────────────────────


def test_doc_ingest_uses_dedup_content_hash() -> None:
    # content_hash is re-exported from app.rag.dedup into the ingest module,
    # so chunk-level dedup keys are computed with the shared normalizer.
    from app.rag import dedup

    assert content_hash == dedup.content_hash
    assert content_hash("Reentrancy  Attack") == content_hash("reentrancy attack")
