"""Collection-resolution honesty tests for the RAG stack.

Pins the audit fixes (2026-08-15):

  1. ``_resolve_qdrant_collection`` maps legacy / P1-KB names to the REAL,
     populated Qdrant collection via ``_COLLECTION_ALIASES`` instead of
     silently falling back to ``rag_documents`` — and every hit is stamped
     with ``metadata["resolved_collection"]`` so ``sources`` labels are
     truthful.
  2. ``hacks`` / ``hacks_v2`` (orphaned Rekt News corpus, source dead 2026)
     is routed into the live exploit DB (``defi_exploits``).
  3. The per-message fan-out lists (chat ``RAG_COLLECTIONS`` and bot
     ``KNOWLEDGE_COLLECTIONS``) contain ONLY real, populated collections —
     phantom names (``token_analysis``, ``wallet_profiles``,
     ``forensic_reports``) and empty collections (``scanned_tokens``,
     ``whale_transfers``) are not fanned out.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.rag import _qdrant as qdrant_mod
from app.rag.service import RAGService

# ── The requested → actual collection map ────────────────────────────────


def test_collection_aliases_map_requested_names_to_real_collections() -> None:
    """Every alias target is a real, populated Qdrant collection."""
    aliases = qdrant_mod._COLLECTION_ALIASES
    real = {
        "threat_intel",
        "scam_patterns",
        "defi_exploits",
        "wallet_v2",
        "news_v2",
        "coding_knowledge",
        "contract_audits",
    }
    assert aliases["wallet_profiles"] == "wallet_v2"
    assert aliases["token_analysis"] == "threat_intel"
    assert aliases["forensic_reports"] == "defi_exploits"
    for target in aliases.values():
        assert target in real, f"alias target {target!r} is not a real, populated collection"


def test_hacks_v2_disposition_routes_to_defi_exploits() -> None:
    """The orphaned Rekt News corpus is wired into the live exploit DB."""
    assert qdrant_mod._COLLECTION_ALIASES.get("hacks") == "defi_exploits"
    assert qdrant_mod._COLLECTION_ALIASES.get("hacks_v2") == "defi_exploits"


# ── _resolve_qdrant_collection ───────────────────────────────────────────


class _NoHttpClient:
    """Inert client — fails if any HTTP method is actually called.

    ``_resolve_qdrant_collection`` constructs its client before the probe
    cache short-circuits, so construction must succeed; only a real probe
    (GET/POST) indicates the cache was not honored.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _NoHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected HTTP GET (probe cache should have short-circuited)")

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unexpected HTTP POST (probe cache should have short-circuited)")


def _seed_probe_cache(monkeypatch: pytest.MonkeyPatch, collections: set[str]) -> None:
    monkeypatch.setattr(qdrant_mod, "_QDRANT_COLLECTIONS", set(collections))
    monkeypatch.setattr(qdrant_mod, "_QDRANT_PROBED", True)
    monkeypatch.setattr(qdrant_mod, "_QDRANT_RESOLVED", {})
    monkeypatch.setattr("httpx.AsyncClient", _NoHttpClient)


@pytest.mark.asyncio
async def test_resolve_uses_alias_to_real_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_probe_cache(monkeypatch, {"wallet_v2", "defi_exploits", "threat_intel"})
    assert await qdrant_mod._resolve_qdrant_collection("wallet_profiles") == "wallet_v2"
    assert await qdrant_mod._resolve_qdrant_collection("token_analysis") == "threat_intel"
    assert await qdrant_mod._resolve_qdrant_collection("forensic_reports") == "defi_exploits"
    assert await qdrant_mod._resolve_qdrant_collection("hacks") == "defi_exploits"
    assert await qdrant_mod._resolve_qdrant_collection("hacks_v2") == "defi_exploits"


@pytest.mark.asyncio
async def test_resolve_passthrough_for_real_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_probe_cache(monkeypatch, {"news_v2"})
    assert await qdrant_mod._resolve_qdrant_collection("news_v2") == "news_v2"


@pytest.mark.asyncio
async def test_resolve_unknown_collection_falls_back_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_probe_cache(monkeypatch, set())
    fake_log = MagicMock()
    monkeypatch.setattr(qdrant_mod, "log", fake_log)
    resolved = await qdrant_mod._resolve_qdrant_collection("totally_bogus")
    assert resolved == qdrant_mod._DEFAULT_QDRANT_COLLECTION
    # Honest log: the fallback must be visible, not silent.
    fake_log.warning.assert_called_once()
    args = fake_log.warning.call_args.args
    assert "rag_collection_resolve_fallback" in str(args[0])
    assert "totally_bogus" in str(args[1:])


# ── _qdrant_search stamps the resolved collection ────────────────────────


class _FakeResp:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, path: str) -> _FakeResp:
        if path.startswith("/collections/"):
            # named-vector collection config → named dense vector used
            return _FakeResp({"result": {"config": {"params": {"vectors": {"dense": {"size": 1024}}}}}})
        return _FakeResp({"result": {"collections": []}})

    async def post(self, path: str, json: dict[str, Any] | None = None) -> _FakeResp:
        return _FakeResp(
            {
                "result": [
                    {
                        "id": "p1",
                        "score": 0.9,
                        "payload": {"content": "hit text", "source": "src", "metadata": {"k": "v"}},
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_qdrant_search_stamps_resolved_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qdrant_mod, "_QDRANT_COLLECTIONS", {"wallet_v2"})
    monkeypatch.setattr(qdrant_mod, "_QDRANT_PROBED", True)
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    hits = await qdrant_mod._qdrant_search([0.1, 0.2], "wallet_v2", limit=5)
    assert hits
    assert hits[0].metadata["resolved_collection"] == "wallet_v2"
    assert hits[0].metadata["source"] == "src"  # pre-existing provenance preserved
    assert hits[0].metadata["k"] == "v"  # payload metadata preserved


# ── RAGService._wrap_hit labels truthfully ───────────────────────────────


def test_wrap_hit_prefers_resolved_collection_over_requested_name() -> None:
    raw = {
        "content": "wallet data",
        "score": 0.8,
        "metadata": {"resolved_collection": "wallet_v2", "source": "x"},
        "doc_id": "d1",
    }
    hit = RAGService._wrap_hit(raw, "wallet_profiles")
    assert hit.collection == "wallet_v2"  # NOT the requested phantom name


def test_wrap_hit_object_prefers_resolved_collection() -> None:
    raw = SimpleNamespace(
        content="exploit data",
        score=0.7,
        doc_id="d2",
        metadata={"resolved_collection": "defi_exploits"},
    )
    hit = RAGService._wrap_hit(raw, "forensic_reports")
    assert hit.collection == "defi_exploits"


def test_wrap_hit_falls_back_to_requested_name_when_unlabeled() -> None:
    raw = {"content": "local hit", "score": 0.6, "metadata": {}, "doc_id": "d3"}
    hit = RAGService._wrap_hit(raw, "threat_intel")
    assert hit.collection == "threat_intel"  # in-process hits come from the requested collection


# ── Fan-out lists contain only real, populated collections ───────────────


def test_fanout_lists_only_real_populated_collections() -> None:
    from app.domains.telegram.rugmunchbot.tools.rag_lookup import KNOWLEDGE_COLLECTIONS
    from app.routers.chat._rag import RAG_COLLECTIONS

    banned = {
        "token_analysis",  # no Qdrant collection (silent rag_documents fallback before)
        "wallet_profiles",  # no Qdrant collection
        "forensic_reports",  # no Qdrant collection
        "scanned_tokens",  # empty (9 pts)
        "whale_transfers",  # empty (0 pts)
        "hacks",  # orphaned (source dead) → routed via alias
        "hacks_v2",  # orphaned (source dead) → routed via alias
    }
    for lst in (RAG_COLLECTIONS, KNOWLEDGE_COLLECTIONS):
        assert not banned.intersection(lst), f"phantom/empty collections still fanned out: {banned & set(lst)}"

    # FOCUSED KNOWLEDGE SET (consolidated 2026-08-15): the fan-out is pruned
    # to the 5 collections that cover the bot/chat knowledge intents without
    # redundancy. Previously-populated data/market/scanner collections
    # (threat_intel, scam_intel, news_v2, wallet_v2, contract_audits,
    # coding_knowledge, regulation_enforcement, stablecoin_market) are no
    # longer fanned out per message — scanner_v2 covers token-address asks,
    # live tools cover market/news, and the pattern/exploit intent space is
    # served by the kept set (see _rag.py RAG_COLLECTIONS rationale).
    real = {
        "security_patterns",  # curated vuln patterns (408 pts)
        "codebase_reference",  # secure reference snippets (997 pts)
        "defi_exploits",  # exploit writeups / PoCs (13,575 pts)
        "contract_security",  # verdict-labeled good/bad contracts (221 pts)
        "scam_patterns",  # scam pattern docs + rug_timeline alias (9,921 pts)
    }
    for lst in (RAG_COLLECTIONS, KNOWLEDGE_COLLECTIONS):
        missing = real - set(lst)
        assert not missing, f"real, populated collections dropped from fan-out: {missing}"

    # Both lists stay in sync with the chat router.
    assert set(KNOWLEDGE_COLLECTIONS) == set(RAG_COLLECTIONS)


def test_standalone_bot_rag_lookup_matches_vendored_copy() -> None:
    """The standalone rugmunchbot repo's fan-out stays in sync with the
    vendored copy in rmi-backend (they must search the same intel)."""
    import re

    vendored = "/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot/tools/rag_lookup.py"
    standalone = "/srv/work/repos/rugmunchbot/src/rugmunchbot/tools/rag_lookup.py"

    def _extract(path: str) -> set[str]:
        with open(path) as f:
            content = f.read()
        match = re.search(r"KNOWLEDGE_COLLECTIONS: list\[str\] = \[(.*?)\]", content, re.S)
        assert match, f"KNOWLEDGE_COLLECTIONS not found in {path}"
        return set(re.findall(r'"([a-z_0-9]+)"', match.group(1)))

    assert _extract(standalone) == _extract(vendored)
