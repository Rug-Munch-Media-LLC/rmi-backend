"""Tests for the X shill ingest pipeline (isolated, no network).

``extract_token_signals`` is pure. ``ingest_x_shill`` is exercised with the
ApiPass client + evidence store monkeypatched so no network / Qdrant / bot DB
is touched; the dedup ledger uses a tmp_path SQLite.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.shill import x_ingest

# ── Signal extraction (pure) ───────────────────────────────────────────────


def test_extract_cashtags_skips_bluechip() -> None:
    out = x_ingest.extract_token_signals("ape into $PEPE and $BTC now")
    assert out == ["$PEPE"]


def test_extract_addresses_deduped() -> None:
    addr = "0x" + "a1b2c3d4" * 8
    out = x_ingest.extract_token_signals(f"ca: {addr} {addr} $WOJAK")
    assert out == ["$WOJAK", addr]


def test_extract_empty() -> None:
    assert x_ingest.extract_token_signals("") == []
    assert x_ingest.extract_token_signals("just vibes, no tickers here") == []


# ── Ingest (mocked I/O) ────────────────────────────────────────────────────


@pytest.fixture
def mock_stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[dict[str, Any]]:
    """Patch apipass fetch + evidence write; return the written records."""
    written: list[dict[str, Any]] = []
    tweets = [
        {"tweet_id": "t1", "text": "$PEPE is the play", "url": "https://x.com/kol/status/t1"},
        {"tweet_id": "t2", "text": "gm", "url": "https://x.com/kol/status/t2"},
        {"tweet_id": "t3", "text": "$PEPE again, huge", "url": "https://x.com/kol/status/t3"},
    ]

    async def fake_fetch(handle: str, limit: int = 10) -> list[dict[str, Any]]:
        return tweets

    async def fake_write(record: dict[str, Any]) -> bool:
        written.append(record)
        return True

    monkeypatch.setattr(x_ingest.apipass_x, "api_key_configured", lambda: True)
    monkeypatch.setattr(x_ingest.apipass_x, "fetch_user_tweets", fake_fetch)
    monkeypatch.setattr(x_ingest, "write_evidence", fake_write)
    monkeypatch.setattr(x_ingest, "_DEDUP_DB_PATH", str(tmp_path / "seen.db"))
    return written


async def test_ingest_writes_evidence_and_dedups(mock_stack: list[dict[str, Any]]) -> None:
    first = await x_ingest.ingest_x_shill(["kol"], tweets_per_handle=10)
    assert first["tweets_seen"] == 3
    assert first["evidence_written"] == 2  # t1 + t3 have tokens; t2 does not
    assert len(mock_stack) == 2
    assert mock_stack[0]["source"] == "x"
    assert mock_stack[0]["account"] == "kol"

    # Second pass: everything is a duplicate, nothing new written.
    second = await x_ingest.ingest_x_shill(["kol"], tweets_per_handle=10)
    assert second["duplicates"] == 3
    assert second["evidence_written"] == 0
    assert len(mock_stack) == 2


async def test_ingest_no_key_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(x_ingest.apipass_x, "api_key_configured", lambda: False)
    summary = await x_ingest.ingest_x_shill(["kol"])
    assert summary["tweets_seen"] == 0
    assert summary["errors"] == ["apipass_key_not_configured"]
