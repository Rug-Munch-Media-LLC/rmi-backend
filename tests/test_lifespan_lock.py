"""Tests for the single-flight X-ingest lock."""

from __future__ import annotations

from app.lifespan import _acquire_x_shill_lock


def test_x_ingest_lock_is_single_owner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RMI_X_SHILL_LOCK_PATH", str(tmp_path / "x-ingest.lock"))

    first = _acquire_x_shill_lock()
    second = _acquire_x_shill_lock()

    assert first is not None
    assert second is None
    first.close()
