"""T15 - Qdrant test_col_* cleanup guard.

CI runs this after integration tests to verify no test_col_* collections
are left behind. Test fixtures SHOULD use unique names (uuid-based) and
clean up in teardown - this test catches regressions where they don't.

Also includes a pytest fixture that creates a unique test collection,
yields its name, and drops it on teardown - even on test failure.
"""

from __future__ import annotations

import contextlib
import os
import uuid

import httpx
import pytest

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")


def _list_collections() -> list[str]:
    """Fetch all Qdrant collection names."""
    r = httpx.get(f"{QDRANT_URL}/collections", timeout=5.0)
    r.raise_for_status()
    return [c["name"] for c in r.json().get("result", {}).get("collections", [])]


def test_no_test_col_artifacts_after_test_run() -> None:
    """No test_col_* collections may exist after integration tests run.

    A test_col_* artifact means a test forgot to clean up. With many
    integration tests over time these accumulate (memory waste, query
    slowdown, MCP agent confusion). This test catches any regression
    where new tests don't clean up after themselves.
    """
    names = _list_collections()
    leaked = [n for n in names if n.startswith("test_col")]
    assert not leaked, (
        f"Found {len(leaked)} test_col_* collection artifacts left behind "
        f"by integration tests: {leaked}. "
        f"Fix: use the `clean_test_collection` fixture below, or add "
        f"explicit teardown in your test that drops the collection."
    )


def test_no_legacy_test_col_collection() -> None:
    """The bare 'test_col' collection (no suffix) is a known test artifact
    from before fixtures used uuid suffixes. Catch any regression.
    """
    names = _list_collections()
    assert "test_col" not in names, (
        "Bare 'test_col' collection found - this is a test artifact that "
        "should never be present in production. Use the "
        "`clean_test_collection` fixture for all Qdrant tests."
    )


@pytest.fixture
def clean_test_collection() -> str:
    """Yield a unique Qdrant collection name and auto-drop on teardown.

    Usage:
        def test_my_qdrant_feature(clean_test_collection):
            # collection is auto-created with a unique uuid name
            qdrant.upsert(clean_test_collection, points=[...])
            assert qdrant.search(clean_test_collection, vector=[...])

    The fixture handles:
    - Creates a fresh collection per test (no shared state)
    - UUID-based name so parallel test runs don't collide
    - Drops the collection on teardown, even if the test fails
    - Skips the test if Qdrant is unreachable (graceful)
    """
    name = f"test_col_{uuid.uuid4().hex[:12]}"
    try:
        httpx.put(
            f"{QDRANT_URL}/collections/{name}",
            json={"vectors": {"size": 4, "distance": "Cosine"}},
            timeout=10.0,
        ).raise_for_status()
    except (httpx.HTTPError, httpx.ConnectError) as e:
        pytest.skip(f"Qdrant unreachable at {QDRANT_URL}: {e}")
        return name  # unreachable, for type checker

    try:
        yield name
    finally:
        # Always drop, even on test failure
        with contextlib.suppress(Exception):
            httpx.delete(f"{QDRANT_URL}/collections/{name}", timeout=5.0)  # best-effort cleanup
