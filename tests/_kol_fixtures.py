"""Hermetic test fixtures for gitignored private data files."""

from __future__ import annotations

import json
import pathlib

import pytest


@pytest.fixture(autouse=True)
def hermetic_kol_registry(tmp_path, monkeypatch):
    """Make the KOL registry hermetic.

    ``app/databus/influential_accounts.json`` is private and gitignored, so it
    is absent in every clean worktree/CI checkout and ``load_accounts()`` blew
    up with FileNotFoundError (assert 0 > 0 downstream). When the real file is
    missing, write a minimal valid registry to ``tmp_path`` and point the loader
    at it through its module ``__file__``, which is what ``load_accounts()``
    uses to resolve its default path. The private data file is never committed
    and never synthesised into the repo tree.
    """
    import app.databus.kol_wallets as kol

    if pathlib.Path(kol.__file__).with_name("influential_accounts.json").exists():
        return None
    target = tmp_path / "influential_accounts.json"
    target.write_text(
        json.dumps(
            {
                "accounts": [
                    {"handle": "alpha", "chain": "solana", "confidence": "strong_heuristic"},
                    {"handle": "beta", "chain": "ethereum", "confidence": "probabilistic"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kol, "__file__", str(target))
    return target
