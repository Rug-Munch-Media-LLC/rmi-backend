"""Launchpad LP-lock detection tests (pure, mocked fetchers).

Verifies the extended ``LiquidityVerifier`` launchpad short-circuit:

* genuine launchpad tokens (pump.fun, Four.meme, SunSwap/SunPump, Zora,
  Bags, Bonk) are reported as LP locked/burned at 100% — no false
  "LP 0% locked → rug risk" flag;
* FAKE pump tokens (mint ends in ``pump`` but the pool/creator is NOT the
  pump.fun program) are NOT auto-locked — they fall through to the real LP
  check so a copycat/honeypot still gets flagged;
* non-launchpad tokens behave unchanged.

No network, no database: every fetcher (``_dexscreener_first_pair``,
``_gmgn_launchpad``, ``_check_solana_lp_burn``, ``_get_lp_holders``) is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.modules._core import (
    PUMPFUN_AMM_PROGRAM_ID,
    PUMPFUN_PROGRAM_ID,
    is_pumpfun_token,
)
from app.domains.scanners.liquidity_verifier import LiquidityVerifier, LockStatus

# ── Fixtures / sample data ────────────────────────────────────────────────

PUMP_MINT = "7pYfqWpump"  # pump.fun vanity mint suffix
NONPUMP_MINT = "7pYfqWabcd"
FAKE_PUMP_MINT = "Honeypot123pump"  # copycat: suffix without pump.fun origin
# Real Raydium AMM program (a DEX, NOT a launchpad) — used as counter-evidence
# for a fake pump pool.
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

UNCX_LOCKER = "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2"


class TestLaunchpadLpLock:
    """Genuine launchpad-issued tokens → LP locked/burned 100% (no rug flag)."""

    @pytest.mark.asyncio
    async def test_pumpfun_via_pair_program_id(self) -> None:
        """pump.fun curve pair (program ID) short-circuits to 100% locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(
                verifier,
                "_dexscreener_first_pair",
                AsyncMock(return_value={"dexId": "pumpfun", "programId": PUMPFUN_PROGRAM_ID}),
            ),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert report.lp_burned is True
        assert report.risk_level == "LOW"
        assert any("pump.fun" in w.lower() for w in report.warnings)

    @pytest.mark.asyncio
    async def test_pumpfun_via_pair_labels(self) -> None:
        """A Raydium pair labeled 'pump.fun' is a migrated pump token → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(
                verifier,
                "_dexscreener_first_pair",
                AsyncMock(return_value={"dexId": "raydium", "labels": ["pump.fun"]}),
            ),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.total_locked_percentage == 100.0
        assert report.status == LockStatus.BURNED

    @pytest.mark.asyncio
    async def test_pumpfun_via_gmgn_label_with_suffix(self) -> None:
        """Mint suffix + GMGN launchpad='pump' → locked without a DEX pair."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="pump")),
        ):
            report = await verifier.verify_lock(PUMP_MINT, "solana")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert report.lp_burned is True

    @pytest.mark.asyncio
    async def test_four_meme_bsc_pair(self) -> None:
        """Four.meme (BSC) pair dexId → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(
                verifier,
                "_dexscreener_first_pair",
                AsyncMock(return_value={"dexId": "fourmeme"}),
            ),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock("0xFourMemeToken", "bsc")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert "four.meme" in report.lock_details[0].platform.lower()

    @pytest.mark.asyncio
    async def test_sunswap_tron_pair(self) -> None:
        """SunSwap/SunPump (Tron) pair dexId → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(
                verifier,
                "_dexscreener_first_pair",
                AsyncMock(return_value={"dexId": "sunpump"}),
            ),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock("TSunPumpToken", "tron")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert "sun" in report.lock_details[0].platform.lower()

    @pytest.mark.asyncio
    async def test_zora_base_pair(self) -> None:
        """Zora (Base) pair dexId → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value={"dexId": "zora"})),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock("0xZoraMeme", "base")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert "zora" in report.lock_details[0].platform.lower()

    @pytest.mark.asyncio
    async def test_bags_solana_gmgn(self) -> None:
        """Bags launchpad via GMGN label → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="bags")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.total_locked_percentage == 100.0
        assert "bags" in report.lock_details[0].platform.lower()

    @pytest.mark.asyncio
    async def test_bonk_solana_gmgn(self) -> None:
        """Bonk launchpad via GMGN label → locked."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="bonk")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.total_locked_percentage == 100.0
        assert "bonk" in report.lock_details[0].platform.lower()


class TestFakePumpGuard:
    """Mint looks like a launchpad token but is NOT — must not be auto-locked."""

    @pytest.mark.asyncio
    async def test_fake_pump_copycat_pair_not_locked(self) -> None:
        """Mint ends 'pump' but the pool program is NOT pump.fun → falls through
        to the real LP check (0% locked → still flagged)."""
        verifier = LiquidityVerifier()
        fake_pair = {"dexId": "raydium", "programId": RAYDIUM_AMM_PROGRAM, "labels": []}
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=fake_pair)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
            patch.object(verifier, "_check_solana_lp_burn", AsyncMock(return_value=False)),
        ):
            report = await verifier.verify_lock(FAKE_PUMP_MINT, "solana")
        assert report.status == LockStatus.UNLOCKED
        assert report.total_locked_percentage == 0.0
        assert report.lp_burned is False
        assert report.risk_level in ("HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_fake_pump_no_evidence_not_locked(self) -> None:
        """Unknown origin (no pair, no GMGN) → fail-closed: the suffix alone is
        NOT treated as a lock."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
            patch.object(verifier, "_check_solana_lp_burn", AsyncMock(return_value=False)),
        ):
            report = await verifier.verify_lock(FAKE_PUMP_MINT, "solana")
        assert report.status == LockStatus.UNLOCKED
        assert report.total_locked_percentage == 0.0
        assert report.lp_burned is False

    def test_is_pumpfun_token_pair_program_guard(self) -> None:
        """Name-level helper: a pump-suffixed mint on a non-pump pool program is
        not pump.fun (guard), while genuine signals still match."""
        assert is_pumpfun_token(FAKE_PUMP_MINT, "solana", pair={"programId": RAYDIUM_AMM_PROGRAM}) is False
        assert is_pumpfun_token(FAKE_PUMP_MINT, "solana", pair={"dexId": "raydium"}) is False
        assert is_pumpfun_token(FAKE_PUMP_MINT, "solana", pair={"programId": PUMPFUN_PROGRAM_ID}) is True
        assert is_pumpfun_token(FAKE_PUMP_MINT, "solana", pair={"programId": PUMPFUN_AMM_PROGRAM_ID}) is True
        # Backward-compat: bare suffix (no counter-evidence) still matches.
        assert is_pumpfun_token(FAKE_PUMP_MINT, "solana") is True


class TestNonLaunchpadUnchanged:
    """Tokens with no launchpad origin keep the existing behavior."""

    @pytest.mark.asyncio
    async def test_normal_solana_token_unchanged(self) -> None:
        """A plain Solana token (no launchpad, no burn) still yields 0% locked
        and a rug flag — nothing is hardcoded for non-launchpad tokens."""
        verifier = LiquidityVerifier()
        normal_pair = {"dexId": "raydium", "programId": RAYDIUM_AMM_PROGRAM, "labels": []}
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=normal_pair)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
            patch.object(verifier, "_check_solana_lp_burn", AsyncMock(return_value=False)),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.status == LockStatus.UNLOCKED
        assert report.total_locked_percentage == 0.0
        assert report.risk_level in ("HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_evm_locker_path_unchanged(self) -> None:
        """EVM tokens bypass the launchpad detector entirely; the locker flow
        (UNCX holder) still reports LOCKED."""
        verifier = LiquidityVerifier()
        with patch.object(
            verifier,
            "_get_lp_holders",
            AsyncMock(return_value=[{"address": UNCX_LOCKER, "percentage": 95.0, "label": ""}]),
        ):
            report = await verifier.verify_lock("0xtoken", "ethereum")
        assert report.status == LockStatus.LOCKED
        assert report.total_locked_percentage >= 95
        assert report.lock_details[0].platform == "UNCX Network"

    @pytest.mark.asyncio
    async def test_evm_pump_suffix_not_launchpad(self) -> None:
        """A pump-suffixed EVM mint is NOT treated as a launchpad token (the
        detector only runs on launchpad chains)."""
        verifier = LiquidityVerifier()
        with patch.object(verifier, "_get_lp_holders", AsyncMock(return_value=[])):
            report = await verifier.verify_lock("0xPumpSuffixpump", "ethereum")
        assert report.status == LockStatus.UNLOCKED
        assert report.total_locked_percentage == 0.0


if __name__ == "__main__":
    pytest.main([__file__])
