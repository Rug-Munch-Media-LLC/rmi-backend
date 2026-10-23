"""Focused tests for fail-open except paths in launch detection.

Debt fix: every security check degrades to its fail-closed value when the
underlying scanner/network call raises — the enrichment batch never raises.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

from app.databus.token_launch_detector import (
    _security_check_bundle,
    _security_check_honeypot,
    _security_check_lp_locked,
    enrich_launches_security,
)


def _launch(address: str = "Launcher11111111111111111111111111111111") -> dict:
    return {
        "address": address,
        "chain": "solana",
        "symbol": "PEPE",
        "pair_address": "PairAddr1111111111111111111111111111111",
    }


async def test_honeypot_check_fail_open_when_detector_raises() -> None:
    """GoPlus misses + HoneypotDetector.detect raises -> None, never propagates."""
    with (
        mock.patch("app.databus.token_launch_detector._goplus_token_security", return_value=None),
        mock.patch("app.domains.scanners.honeypot_detector.HoneypotDetector") as detector_cls,
    ):
        detector = detector_cls.return_value
        detector.detect = mock.AsyncMock(side_effect=RuntimeError("gonzo"))
        detector.close = mock.AsyncMock()
        assert await _security_check_honeypot(_launch()) is None


async def test_lp_lock_check_fail_open_when_verifier_raises() -> None:
    """LiquidityVerifier.verify_lock raising -> None, never propagates."""
    with mock.patch("app.domains.scanners.liquidity_verifier.LiquidityVerifier") as verifier_cls:
        verifier = verifier_cls.return_value
        verifier.verify_lock = mock.AsyncMock(side_effect=ConnectionError("down"))
        verifier.client.aclose = mock.AsyncMock()
        assert await _security_check_lp_locked(_launch()) is None


async def test_bundle_check_fail_open_when_detector_raises() -> None:
    """BundleDetector.detect_bundles raising -> None, never propagates."""
    with mock.patch("app.domains.scanners.bundle_detector.BundleDetector") as bundle_cls:
        detector = bundle_cls.return_value
        detector.detect_bundles = mock.AsyncMock(side_effect=TimeoutError("slow"))
        detector.close = mock.AsyncMock()
        assert await _security_check_bundle(_launch()) is None


_CHECK_FNS = (
    "_security_check_honeypot",
    "_security_check_mintable",
    "_security_check_lp_locked",
    "_security_check_creator",
    "_security_check_bundle",
)


async def test_enrich_batch_never_raises_when_a_check_raises() -> None:
    """Raising per-field checks leave fail-closed defaults; the batch returns."""
    launches = [
        _launch(),
        _launch(address="Two22222222222222222222222222222222"),
    ]
    with ExitStack() as stack:
        for fn in _CHECK_FNS:
            stack.enter_context(
                mock.patch(
                    f"app.databus.token_launch_detector.{fn}",
                    side_effect=RuntimeError("boom"),
                )
            )
        enriched = await enrich_launches_security(launches)
    assert len(enriched) == 2
    for record in enriched:
        sec = record["security"]
        assert sec["honeypot"] is False
        assert sec["mintable"] is False
        assert sec["lp_locked"] is None
        assert sec["creator_address"] is None
        assert sec["bundle_suspect"] is False
