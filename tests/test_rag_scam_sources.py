"""Pure tests for the public scam/rug/exploit RAG incident sources.

No network: asserts the source registry parses and every new source carries
the required fields, ``normalize_incident`` maps raw records (DefiLlama /
REKT shapes) to the canonical shape and rejects malformed records, and the
URL-dedup helper collapses duplicates.
"""

from __future__ import annotations

import json

from app.rag._crawler_constants import (
    INCIDENT_CATEGORIES,
    INCIDENT_CATEGORY_BY_SOURCE,
    INCIDENT_COLLECTION_BY_CATEGORY,
    INCIDENT_SOURCES_FILE,
)
from app.rag.ingest_crypto_knowledge import (
    _incident_doc_id,
    collection_names_for_category,
    dedupe_incidents,
    load_incident_sources,
    normalize_incident,
)

# ── Source registry ───────────────────────────────────────────────────────


def _load_json() -> dict:
    return json.loads(INCIDENT_SOURCES_FILE.read_text(encoding="utf-8"))


def _raw_sources() -> list[dict]:
    block = _load_json()["incident_sources"]
    assert isinstance(block, dict), "incident_sources must be a dict (legacy load_sources ignores non-list values)"
    sources = block["sources"]
    assert isinstance(sources, list)
    return sources


def test_crawl_sources_parses() -> None:
    data = _load_json()
    assert "incident_sources" in data
    assert len(_raw_sources()) >= 4  # SlowMist, REKT, DefiLlama, CertiK


def test_every_incident_source_has_required_fields() -> None:
    for src in _raw_sources():
        for field in ("name", "description", "type", "url", "category", "collections", "enabled"):
            assert field in src, f"source {src.get('name')!r} missing required field {field!r}"
        assert isinstance(src["name"], str), src["name"]
        assert src["name"], src["name"]
        assert src["url"].startswith(("http://", "https://")), src["name"]
        assert isinstance(src["enabled"], bool), src["name"]
        assert isinstance(src["collections"], list), src["name"]
        assert src["collections"], src["name"]
        assert src["type"] in ("json_api", "html_page"), src["name"]


def test_source_categories_are_valid() -> None:
    for src in _raw_sources():
        assert src["category"] in INCIDENT_CATEGORIES, src["name"]
        # Category stamped in the registry must match the constants mapping.
        assert INCIDENT_CATEGORY_BY_SOURCE[src["name"]] == src["category"], src["name"]


def test_source_collections_are_known_rag_collections() -> None:
    known = set(INCIDENT_COLLECTION_BY_CATEGORY.values())
    for src in _raw_sources():
        for col in src["collections"]:
            assert col in {c for colls in known for c in colls}, f"{src['name']} targets unknown collection {col!r}"


def test_load_incident_sources_returns_only_valid_entries() -> None:
    sources = load_incident_sources()
    assert sources, "load_incident_sources returned empty for a valid registry"
    assert all(s["name"] in INCIDENT_CATEGORY_BY_SOURCE for s in sources)
    # DefiLlama hacks is the primary verified JSON source and must be enabled.
    defillama = next(s for s in sources if s["name"] == "defillama_hacks")
    assert defillama["enabled"] is True
    assert defillama["type"] == "json_api"
    assert "defi_exploits" in defillama["collections"]


def test_category_collection_mapping_covers_all_categories() -> None:
    assert set(INCIDENT_COLLECTION_BY_CATEGORY) == set(INCIDENT_CATEGORIES)
    for category in INCIDENT_CATEGORIES:
        assert collection_names_for_category(category)


# ── normalize_incident ────────────────────────────────────────────────────


def test_normalize_incident_defillama_shape() -> None:
    raw = {
        "date": 1711065600,
        "name": "Super Sushi Samurai",
        "classification": "Protocol Logic",
        "technique": "Infinite Mint and Dump",
        "amount": 4800000,
        "chain": ["Blast"],
        "bridgeHack": False,
        "targetType": "Gaming",
        "source": "",
        "language": "Solidity",
    }
    incident = normalize_incident(raw, "defillama_hacks")
    assert incident is not None
    assert incident["title"] == "Super Sushi Samurai"
    assert incident["chain"] == "Blast"
    assert incident["amount_lost"] == 4800000
    assert incident["category"] == "exploit"
    assert incident["severity"] == "medium"  # no explicit severity → default
    assert incident["source"] == "defillama_hacks"
    assert incident["date"].startswith("2024-03-22")  # 1711065600 → 2024-03-22T00:00:00Z
    assert "Infinite Mint and Dump" in incident["description"]
    assert incident["url"].startswith("https://")  # no explicit URL → synthetic


def test_normalize_incident_rekt_shape() -> None:
    raw = {
        "title": "Wormhole — $326,000,000",
        "url": "https://rekt.news/wormhole-rekt/",
        "description": "Signature verification flaw drained 120k wETH.",
        "date": "2022-02-03T00:00:00Z",
        "amount": "$326,000,000",
        "chain": "Solana",
        "severity": "Critical",
    }
    incident = normalize_incident(raw, "rekt_news_leaderboard")
    assert incident is not None
    assert incident["title"] == "Wormhole — $326,000,000"
    assert incident["url"] == "https://rekt.news/wormhole-rekt/"
    assert incident["amount_lost"] == 326000000
    assert incident["chain"] == "Solana"
    assert incident["severity"] == "critical"  # normalized lowercase
    assert incident["category"] == "exploit"  # from source mapping, raw has none
    assert "wETH" in incident["description"]


def test_normalize_incident_rejects_malformed() -> None:
    assert normalize_incident(None, "defillama_hacks") is None
    assert normalize_incident("not a dict", "defillama_hacks") is None
    assert normalize_incident({}, "defillama_hacks") is None
    assert normalize_incident({"title": "   "}, "defillama_hacks") is None
    assert normalize_incident({"name": 123}, "defillama_hacks") is None  # non-string title


def test_normalize_incident_synthetic_url_is_stable() -> None:
    raw = {"name": "Some Hack", "amount": 1000}
    first = normalize_incident(raw, "defillama_hacks")
    second = normalize_incident(dict(raw), "defillama_hacks")
    assert first is not None
    assert second is not None
    assert first["url"] == second["url"]  # deterministic dedup key
    assert first["url"].startswith("https://")


def test_normalize_incident_explicit_category_wins() -> None:
    raw = {"name": "Token Exit Scam", "category": "rug", "url": "https://example.com/rug"}
    incident = normalize_incident(raw, "defillama_hacks")
    assert incident is not None
    assert incident["category"] == "rug"


# ── URL dedup ─────────────────────────────────────────────────────────────


def test_dedupe_incidents_by_url() -> None:
    incidents = [
        {"url": "https://example.com/hack/1", "title": "A"},
        {"url": "https://example.com/hack/1", "title": "A duplicate"},
        {"url": "https://example.com/hack/1/", "title": "A with trailing slash"},
        {"url": "https://example.com/hack/2", "title": "B"},
        {"url": "", "title": "no url — dropped"},
    ]
    deduped = dedupe_incidents(incidents)
    assert [i["title"] for i in deduped] == ["A", "B"]
    assert all(i["url"] for i in deduped)


def test_dedupe_incidents_is_case_insensitive() -> None:
    incidents = [
        {"url": "https://example.com/Hack", "title": "A"},
        {"url": "https://example.com/hack", "title": "A duplicate"},
    ]
    deduped = dedupe_incidents(incidents)
    assert len(deduped) == 1


def test_incident_doc_id_is_stable_and_prefixed() -> None:
    url = "https://example.com/hack/1"
    assert _incident_doc_id(url) == _incident_doc_id(url)
    assert _incident_doc_id(url).startswith("incident:")
    assert _incident_doc_id(url) != _incident_doc_id("https://example.com/hack/2")
