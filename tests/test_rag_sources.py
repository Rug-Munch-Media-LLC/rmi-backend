"""Pure tests for the RAG source registry (crawl_sources.json) + deep-dive doc.

No network, no app imports: asserts the registry parses, every incident source
entry carries the required fields (name/description/type/url/category/
collections/enabled), categories and collections are valid, URLs and names are
unique across the file, the newly wired sources are present, and the lead-facing
doc (docs/RAG_SOURCE_DEEP_DIVE.md) exists with the priority table.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWL_SOURCES = ROOT / "app" / "rag" / "crawl_sources.json"
DEEP_DIVE_DOC = ROOT / "docs" / "RAG_SOURCE_DEEP_DIVE.md"

REQUIRED_FIELDS = ("name", "description", "type", "url", "category", "collections", "enabled")
VALID_TYPES = ("json_api", "html_page")
VALID_CATEGORIES = ("rug", "exploit", "scam", "education")
KNOWN_COLLECTIONS = ("known_scams", "scam_patterns", "defi_exploits", "threat_intel", "coding_knowledge")

# Sources wired by prior + this task (P0 in the deep-dive docs).
WIRED_SOURCES = ("metamask_eth_phishing_detect", "defihacklabs_incidents", "ethereum_labels_0x406")

TOP_LEVEL_LISTS = ("news", "security", "research", "onchain")


def _load() -> dict:
    return json.loads(CRAWL_SOURCES.read_text(encoding="utf-8"))


def _sources() -> list[dict]:
    block = _load()["incident_sources"]
    assert isinstance(block, dict), "incident_sources must be a dict (legacy load_sources ignores non-list values)"
    srcs = block["sources"]
    assert isinstance(srcs, list)
    return srcs


# ── Registry parses ───────────────────────────────────────────────────────


def test_crawl_sources_json_parses() -> None:
    data = _load()
    assert "incident_sources" in data
    for key in TOP_LEVEL_LISTS:
        assert isinstance(data[key], list), key
        assert all(isinstance(u, str) and u.startswith(("http://", "https://")) for u in data[key]), key


def test_incident_sources_has_version_and_sources() -> None:
    block = _load()["incident_sources"]
    assert isinstance(block.get("version"), int)
    assert len(_sources()) >= 7  # 4 original + 3 new


# ── Required fields ───────────────────────────────────────────────────────


def test_every_source_has_required_fields() -> None:
    for src in _sources():
        for field in REQUIRED_FIELDS:
            assert field in src, f"source {src.get('name')!r} missing required field {field!r}"
        assert isinstance(src["name"], str), src["name"]
        assert src["name"], src["name"]
        assert src["url"].startswith(("http://", "https://")), src["name"]
        assert isinstance(src["enabled"], bool), src["name"]
        assert isinstance(src["collections"], list), src["name"]
        assert src["collections"], src["name"]
        assert src["type"] in VALID_TYPES, src["name"]
        assert src["category"] in VALID_CATEGORIES, src["name"]
        assert set(src["collections"]) <= set(KNOWN_COLLECTIONS), f"{src['name']} targets unknown collection"


# ── Uniqueness ────────────────────────────────────────────────────────────


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def test_no_duplicate_urls_within_incident_sources() -> None:
    seen: set[str] = set()
    for src in _sources():
        key = _norm_url(src["url"])
        assert key, src["name"]
        assert key not in seen, f"duplicate URL {src['url']} in incident sources"
        seen.add(key)


def test_no_duplicate_names() -> None:
    names = [s["name"] for s in _sources()]
    assert len(names) == len(set(names)), "duplicate source name"


def test_no_url_overlap_with_top_level_feeds() -> None:
    data = _load()
    feed_urls = {_norm_url(u) for key in TOP_LEVEL_LISTS for u in data[key]}
    for src in _sources():
        assert _norm_url(src["url"]) not in feed_urls, f"{src['name']} duplicates a top-level feed URL"


# ── Newly wired sources ───────────────────────────────────────────────────


def test_new_sources_are_present_and_valid() -> None:
    by_name = {s["name"]: s for s in _sources()}
    expected = {
        "metamask_eth_phishing_detect": ("scam", {"known_scams", "scam_patterns"}, "json_api"),
        "defihacklabs_incidents": ("exploit", {"defi_exploits", "threat_intel"}, "html_page"),
        "ethereum_labels_0x406": ("scam", {"known_scams", "threat_intel"}, "html_page"),
    }
    for name, (category, collections, src_type) in expected.items():
        assert name in by_name, f"expected wired source {name} missing"
        src = by_name[name]
        assert src["category"] == category, name
        assert set(src["collections"]) == collections, name
        assert src["type"] == src_type, name
        assert src["enabled"] is False, f"{name} should stay disabled until a parser exists"


# ── Deep-dive doc ─────────────────────────────────────────────────────────


def test_deep_dive_doc_exists_and_covers_wired_sources() -> None:
    assert DEEP_DIVE_DOC.is_file(), "docs/RAG_SOURCE_DEEP_DIVE.md missing"
    text = DEEP_DIVE_DOC.read_text(encoding="utf-8")
    for name in WIRED_SOURCES:
        assert name in text, f"deep-dive doc must mention {name}"
    # Priority legend + license/collection columns present (lead approval doc).
    for marker in ("P0", "P1", "P2", "License", "Target collection", "Rationale"):
        assert marker in text, f"doc missing marker {marker!r}"
