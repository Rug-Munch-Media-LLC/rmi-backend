"""Smoke tests for WalletManagerV2 — regression for the VAULT_PATH attribute
mismatch and the broken legacy shim imports.

The manager mixins read VAULT_PATH / KEYSTORE_PATH / PAYMENTS_PATH /
SHARES_PATH / encryption_password — those must be set by __init__ and the
legacy shim (app.wallet_manager_v2) must import cleanly.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — used in fixture signatures at runtime

import pytest


@pytest.fixture
def manager(tmp_path: Path):
    from app.domains.wallet.manager import WalletManagerV2

    m = WalletManagerV2(vault_path=str(tmp_path), password="test-pass")
    return m


def test_init_sets_mixin_attributes(manager) -> None:
    """All attributes the DB/analysis mixins read must exist after __init__."""
    assert manager.VAULT_PATH.endswith("vault.json")
    assert manager.KEYSTORE_PATH.endswith("keystore.json")
    assert manager.PAYMENTS_PATH.endswith("payments.jsonl")
    assert manager.SHARES_PATH.endswith("shares")
    assert manager.encryption_password == "test-pass"
    assert manager._wallets == {}
    assert manager._payments == []
    assert manager._initialized is False


def test_initialize_and_close_do_not_crash(manager) -> None:
    """initialize/close must not await sync methods (regression: await None)."""
    import asyncio

    async def _cycle() -> None:
        await manager.initialize()
        assert manager._initialized is True
        await manager.close()
        assert manager._initialized is False

    asyncio.run(_cycle())


def test_legacy_shim_imports_cleanly() -> None:
    """app.wallet_manager_v2 must import (regression: ImportError on
    _wallet_manager_instance / logger)."""
    import app.wallet_manager_v2 as shim

    assert shim.WalletManagerV2 is not None
    assert shim.get_wallet_manager_v2 is not None
    assert shim._manager_instance is None


def test_get_wallet_manager_v2_password_keyword(tmp_path: Path) -> None:
    """Password must land in encryption_password, not vault_path."""
    from app.domains.wallet import manager as manager_mod

    # The singleton may already exist from another test — reset it so this
    # test's password kwarg actually constructs a fresh instance.
    saved = manager_mod._manager_instance
    manager_mod._manager_instance = None
    try:
        m = manager_mod.get_wallet_manager_v2(vault_path=str(tmp_path), password="secret")
    finally:
        pass
    assert m.encryption_password == "secret"
    assert m.VAULT_PATH.endswith("vault.json")  # vault_path kwarg honored, not the password
    manager_mod._manager_instance = saved
