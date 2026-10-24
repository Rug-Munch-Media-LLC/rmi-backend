"""Pure tests for the scanner-domain split + the risk-policy seam.

Part 1 (``test_risk_policy_seam.py``) pins the *public* half of the seam:
the interface, the engine contract, the compat shim, and the absence of the
calibration from this repository.

Part 2 (here) keeps the original degodding guarantees for the
liquidity_verifier split: helpers importable from their sibling submodules
and re-exported from the original module with the SAME object identity. No
network, no DB.
"""

from __future__ import annotations

import app.domains.scanners._config as lv_config
import app.domains.scanners._models as lv_models
import app.domains.scanners.liquidity_verifier as lv

# ── liquidity_verifier: models + config ────────────────────────────────


def test_lv_models_submodule_importable() -> None:
    assert lv_models.LockStatus.BURNED.value == "burned"
    assert len(lv_models.SOLANA_BURN_ADDRESSES) == 2
    detail = lv_models.LockDetail(locker_address="0x0", platform="UNCX")
    assert detail.is_legit is True
    assert detail.percentage_locked == 0.0


def test_lv_models_reexported_from_original_same_object() -> None:
    for name in ("LockStatus", "LockDetail", "LockReport", "SOLANA_BURN_ADDRESSES"):
        assert getattr(lv, name) is getattr(lv_models, name), name


def test_lv_config_submodule_importable() -> None:
    assert "ethereum" in lv_config.ETHERSCAN_NETWORKS
    assert lv_config.HELIUS_RPC_URL.startswith("http")
    assert lv_config.MORALIS_DATA_URL.startswith("https")


def test_lv_config_reexported_from_original_same_object() -> None:
    for name in ("ETHERSCAN_NETWORKS", "MORALIS_DATA_URL", "HELIUS_RPC_URL", "HELIUS_API_KEY"):
        assert getattr(lv, name) is getattr(lv_config, name), name
