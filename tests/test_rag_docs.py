"""Pure tests for the RAG crypto documentation/knowledge sources.

No network, no app imports: asserts ``crawl_sources.json`` parses, the five
wired doc sources carry the required fields with valid category/collection,
URLs and names stay unique, the deep-dive doc exists and covers the wired
sources, and the constants mapping (category → collections, source → category)
stays consistent with the registry.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRAWL_SOURCES = ROOT / "app" / "rag" / "crawl_sources.json"
DEEP_DIVE_DOC = ROOT / "docs" / "RAG_CRYPTO_DOCS_DEEP_DIVE.md"

REQUIRED_FIELDS = ("name", "description", "type", "url", "category", "collections", "enabled")
VALID_TYPES = ("json_api", "html_page")
VALID_CATEGORIES = ("rug", "exploit", "scam", "education")
KNOWN_COLLECTIONS = ("known_scams", "scam_patterns", "defi_exploits", "threat_intel", "coding_knowledge")

# Doc/knowledge sources wired by this task (P0 in docs/RAG_CRYPTO_DOCS_DEEP_DIVE.md).
WIRED_DOC_SOURCES = (
    "consensys_best_practices",
    "not_so_smart_contracts",
    "smart_contract_vulnerabilities",
    "solidity_security_considerations",
    "openzeppelin_upgradeable_docs",
)


def _load() -> dict:
    return json.loads(CRAWL_SOURCES.read_text(encoding="utf-8"))


def _sources() -> list[dict]:
    block = _load()["incident_sources"]
    assert isinstance(block, dict), "incident_sources must be a dict (legacy load_sources ignores non-list values)"
    srcs = block["sources"]
    assert isinstance(srcs, list)
    return srcs


def _doc_sources() -> list[dict]:
    return [s for s in _sources() if s.get("category") == "education"]


# ── Registry parses ───────────────────────────────────────────────────────


def test_crawl_sources_json_parses() -> None:
    data = _load()
    assert "incident_sources" in data
    block = data["incident_sources"]
    assert isinstance(block.get("version"), int)
    assert isinstance(block.get("sources"), list)


def test_doc_sources_are_wired() -> None:
    names = {s["name"] for s in _sources()}
    for name in WIRED_DOC_SOURCES:
        assert name in names, f"expected wired doc source {name} missing"


# ── Required fields ───────────────────────────────────────────────────────


def test_every_doc_source_has_required_fields() -> None:
    assert len(_doc_sources()) >= 5
    for src in _doc_sources():
        for field in REQUIRED_FIELDS:
            assert field in src, f"doc source {src.get('name')!r} missing required field {field!r}"
        assert isinstance(src["name"], str), src["name"]
        assert src["name"], src["name"]
        assert src["url"].startswith(("http://", "https://")), src["name"]
        assert src["type"] in VALID_TYPES, src["name"]
        assert src["category"] in VALID_CATEGORIES, src["name"]
        assert isinstance(src["collections"], list), src["name"]
        assert src["collections"], src["name"]
        assert set(src["collections"]) <= set(KNOWN_COLLECTIONS), f"{src['name']} targets unknown collection"
        assert isinstance(src["enabled"], bool), src["name"]


def test_doc_sources_target_coding_knowledge() -> None:
    # Every wired doc source must feed the code-level knowledge collection
    # (the whole point of this task) or its threat_intel sibling.
    for src in _doc_sources():
        assert "coding_knowledge" in src["collections"] or "threat_intel" in src["collections"], src["name"]


# ── Uniqueness ────────────────────────────────────────────────────────────


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def test_no_duplicate_urls_among_doc_sources() -> None:
    seen: set[str] = set()
    for src in _doc_sources():
        key = _norm_url(src["url"])
        assert key, src["name"]
        assert key not in seen, f"duplicate URL {src['url']} in doc sources"
        seen.add(key)


def test_no_duplicate_names() -> None:
    names = [s["name"] for s in _sources()]
    assert len(names) == len(set(names)), "duplicate source name"


# ── Deep-dive doc ─────────────────────────────────────────────────────────


def test_deep_dive_doc_exists_and_covers_wired_sources() -> None:
    assert DEEP_DIVE_DOC.is_file(), "docs/RAG_CRYPTO_DOCS_DEEP_DIVE.md missing"
    text = DEEP_DIVE_DOC.read_text(encoding="utf-8")
    for name in WIRED_DOC_SOURCES:
        assert name in text, f"deep-dive doc must mention {name}"
    for marker in ("P0", "P1", "P2", "License", "Target collection", "Rationale"):
        assert marker in text, f"doc missing marker {marker!r}"


# ── Constants consistency (registry ↔ _crawler_constants) ────────────────


def _constants() -> dict:
    ns: dict = {}
    const_file = ROOT / "app" / "rag" / "_crawler_constants.py"
    text = const_file.read_text(encoding="utf-8")
    # Minimal, safe extraction of the three registries we depend on.
    import ast

    tree = ast.parse(text)
    for node in tree.body:
        # Constants are annotated assignments (e.g. ``name: tuple[...] = ...``).
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        else:
            continue
        if not isinstance(target, ast.Name):
            continue
        if target.id in ("INCIDENT_CATEGORIES", "INCIDENT_COLLECTION_BY_CATEGORY", "INCIDENT_CATEGORY_BY_SOURCE"):
            with contextlib.suppress(ValueError, SyntaxError):  # pragma: no cover - defensive
                ns[target.id] = ast.literal_eval(value)
    return ns


def test_constants_cover_education_category_and_coding_knowledge() -> None:
    const = _constants()
    assert "education" in const.get("INCIDENT_CATEGORIES", ())
    colls = const.get("INCIDENT_COLLECTION_BY_CATEGORY", {})
    assert "coding_knowledge" in colls.get("education", ())
    for name in WIRED_DOC_SOURCES:
        assert const.get("INCIDENT_CATEGORY_BY_SOURCE", {}).get(name) == "education", name
