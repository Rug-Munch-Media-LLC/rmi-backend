"""Tests for scanner number correctness — pump.fun LP lock, liquidity/holders
fallbacks, and fail-open behavior.

Pure unit tests: every fetcher (DexScreener, GMGN, Solana RPC, HoneypotDetector)
is mocked. No network, no database.

Regression: a Pump.fun token scan showed ``Liquidity: $0``, ``LP Locked: 0%`` +
``CRITICAL: LP only 0.0% locked - HIGH rug pull risk``, ``Holders: 0`` and a
$0.15 volume. Pump.fun burns/locks LP by design (bonding curve → Raydium
migration burns LP), and real liquidity/holders/volume live in the DEX pair /
GMGN — never surface $0 when a fallback has data.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.domains.scanner.modules._core import PUMPFUN_PROGRAM_ID, is_pumpfun_token
from app.domains.scanners.liquidity_verifier import LiquidityVerifier, LockStatus

# ── Fixtures / sample data ────────────────────────────────────────────────

PUMP_MINT = "7pYfqWpump"  # pump.fun vanity mint suffix
NONPUMP_MINT = "7pYfqWabcd"

PUMP_PAIR = {
    "dexId": "pumpfun",
    "pairAddress": "curvePair111",
    "liquidity": {"usd": 0},
    "volume": {"h24": 0.15},
    "pairCreatedAt": 1700000000000,
    "labels": ["pump.fun"],
}

RAYDIUM_PAIR = {
    "dexId": "raydium",
    "pairAddress": "realPool222",
    "liquidity": {"usd": 48000},
    "volume": {"h24": 250000},
    "pairCreatedAt": 1700000000000,
    "labels": [],
}

EMPTY_RPC = {"result": {"value": []}}


def _enter_lock_flow(
    stack: ExitStack,
    *,
    dexscreener: dict | None = None,
    gmgn: dict | None = None,
    burn: tuple | None = None,
) -> None:
    """Enter every patch used by _check_liquidity_lock_multi into an ExitStack."""
    stack.enter_context(patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False))
    stack.enter_context(
        patch("app.domains.scanner.modules.holders._fetch_dexscreener", AsyncMock(return_value=dexscreener))
    )
    stack.enter_context(patch("app.domains.scanner.modules.holders._gmgn_token_info", AsyncMock(return_value=gmgn)))
    stack.enter_context(
        patch("app.domains.scanner.modules.holders._solana_lp_burn_ratio", AsyncMock(return_value=burn))
    )
    stack.enter_context(
        patch(
            "app.domains.detectors.smart_contract_honeypot_detector.HoneypotDetector.analyze_token",
            side_effect=ImportError("no honeypot detector in test"),
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pump.fun detection
# ═══════════════════════════════════════════════════════════════════════════


class TestIsPumpfunToken:
    def test_mint_suffix_pump(self) -> None:
        assert is_pumpfun_token(PUMP_MINT, "solana") is True

    def test_mint_suffix_pump_case_insensitive(self) -> None:
        assert is_pumpfun_token("7pYfqWpUmP", "solana") is True

    def test_non_pump_mint(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana") is False

    def test_pair_dexid_pumpfun(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana", pair={"dexId": "pumpfun"}) is True

    def test_pair_labels_pump(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana", pair={"dexId": "raydium", "labels": ["pump.fun"]}) is True

    def test_pair_program_id_pumpfun(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana", pair={"programId": PUMPFUN_PROGRAM_ID}) is True

    def test_gmgn_launchpad_pump(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana", launchpad="pump") is True

    def test_non_pump_pair_and_launchpad(self) -> None:
        assert is_pumpfun_token(NONPUMP_MINT, "solana", pair=RAYDIUM_PAIR, launchpad="") is False

    def test_never_true_for_evm(self) -> None:
        # pump.fun is Solana-only — even a "pump"-suffixed EVM address is not
        # treated as pump.fun (prevents hardcoding 100% LP lock on EVM).
        assert is_pumpfun_token("0xpump", "ethereum") is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. Pump.fun LP lock — no false rug flag
# ═══════════════════════════════════════════════════════════════════════════


class TestPumpfunLpLock:
    @pytest.mark.asyncio
    async def test_verify_lock_pumpfun_mint_suffix(self) -> None:
        """A genuine pump.fun mint (ends 'pump') reports 100% LP burned — no
        CRITICAL flag, when the GMGN launchpad label corroborates the suffix.

        The mint suffix alone is a WEAK signal (fail-closed on copycats):
        GMGN launchpad='pump' corroborates it without needing the DEX pair.
        """
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="pump")),
        ):
            report = await verifier.verify_lock(PUMP_MINT, "solana")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert report.lp_burned is True
        assert report.risk_score == 0
        assert report.risk_level == "LOW"
        assert all("CRITICAL" not in w and "0.0% locked" not in w for w in report.warnings)
        assert any("pump.fun" in w.lower() for w in report.warnings)

    @pytest.mark.asyncio
    async def test_verify_lock_pumpfun_via_pair(self) -> None:
        """A pump.fun-created DEX pair (labels/dexId) also short-circuits."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=PUMP_PAIR)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0
        assert report.lp_burned is True
        assert report.risk_level == "LOW"

    @pytest.mark.asyncio
    async def test_verify_lock_pumpfun_via_gmgn_launchpad(self) -> None:
        """GMGN launchpad='pump' is enough when DexScreener has no pump pair."""
        verifier = LiquidityVerifier()
        with (
            patch.object(verifier, "_dexscreener_first_pair", AsyncMock(return_value=None)),
            patch.object(verifier, "_gmgn_launchpad", AsyncMock(return_value="pump")),
        ):
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.status == LockStatus.BURNED
        assert report.total_locked_percentage == 100.0

    @pytest.mark.asyncio
    async def test_liquidity_lock_multi_pumpfun_100pct_not_clobbered(self) -> None:
        """_check_liquidity_lock_multi reports 100% for a pump.fun pair even
        when the pair's liquidity is $0 (bonding curve) — the low-liquidity
        reset must not clobber the pump.fun LP-burn answer."""
        from app.domains.scanner.modules import _check_liquidity_lock_multi

        with ExitStack() as stack:
            _enter_lock_flow(stack, dexscreener={"pairs": [PUMP_PAIR]}, gmgn=None)
            result = await _check_liquidity_lock_multi(NONPUMP_MINT, "solana")
        assert result is not None
        assert result["liquidity_locked_pct"] == 100.0
        assert result["lp_burned"] is True
        assert result["is_pumpfun"] is True
        assert result["source"] == "pumpfun_lp_burn"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Non-pump tokens are NOT hardcoded
# ═══════════════════════════════════════════════════════════════════════════


class TestNonPumpNoHardcode:
    @pytest.mark.asyncio
    async def test_liquidity_lock_multi_non_pump_stays_0(self) -> None:
        """A non-pump Solana token with no burn evidence keeps 0% locked —
        we never hardcode 100% for non-pump tokens."""
        from app.domains.scanner.modules import _check_liquidity_lock_multi

        with ExitStack() as stack:
            _enter_lock_flow(
                stack,
                dexscreener={"pairs": [dict(RAYDIUM_PAIR, liquidity={"usd": 0})]},
                gmgn=None,
                burn=None,
            )
            result = await _check_liquidity_lock_multi(NONPUMP_MINT, "solana")
        assert result is not None
        assert result["liquidity_locked_pct"] == 0.0
        assert result["lp_burned"] is False
        assert result.get("is_pumpfun") is not True


# ═══════════════════════════════════════════════════════════════════════════
# 4. Liquidity / holders / volume fallbacks (DexScreener pair + GMGN)
# ═══════════════════════════════════════════════════════════════════════════


class TestNumberFallbacks:
    @pytest.mark.asyncio
    async def test_holder_fallback_gmgn_when_rpc_empty(self) -> None:
        """Solana RPC returns no accounts → GMGN holder_count fills the gap."""
        from app.domains.scanner.modules import _get_holder_data

        with (
            patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False),
            patch("app.domains.scanner.modules.holders._solana_rpc_call", AsyncMock(return_value=EMPTY_RPC)),
            patch("app.domains.scanner.modules.holders._fetch_dexscreener", AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.modules.holders._gmgn_token_info",
                AsyncMock(return_value={"holders": 847, "source": "gmgn"}),
            ),
        ):
            result = await _get_holder_data(NONPUMP_MINT, "solana")
        assert result["holder_count"] == 847
        assert result["source"] == "gmgn"

    @pytest.mark.asyncio
    async def test_holder_fallback_dexscreener_pair(self) -> None:
        """The DEX pair's holder count fills the gap when RPC is empty."""
        from app.domains.scanner.modules import _get_holder_data

        pair = dict(RAYDIUM_PAIR, holders=1234)
        with (
            patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False),
            patch("app.domains.scanner.modules.holders._solana_rpc_call", AsyncMock(return_value=EMPTY_RPC)),
            patch(
                "app.domains.scanner.modules.holders._fetch_dexscreener",
                AsyncMock(return_value={"pairs": [pair]}),
            ),
            patch("app.domains.scanner.modules.holders._gmgn_token_info", AsyncMock()) as mock_gmgn,
        ):
            result = await _get_holder_data(NONPUMP_MINT, "solana")
        mock_gmgn.assert_not_awaited()
        assert result["holder_count"] == 1234
        assert result["source"] == "dexscreener"

    @pytest.mark.asyncio
    async def test_liquidity_fallback_gmgn_when_pair_zero(self) -> None:
        """Pair liquidity is $0 → GMGN liquidity fills the gap (never $0)."""
        from app.domains.scanner.modules import _check_liquidity_lock_multi

        zero_liq_pair = dict(RAYDIUM_PAIR, liquidity={"usd": 0})
        with ExitStack() as stack:
            _enter_lock_flow(
                stack,
                dexscreener={"pairs": [zero_liq_pair]},
                gmgn={"liquidity": 42000.0, "holders": 300, "source": "gmgn", "launchpad": ""},
                burn=None,
            )
            result = await _check_liquidity_lock_multi(NONPUMP_MINT, "solana")
        assert result["liquidity_usd"] == 42000.0
        assert result["source"] == "gmgn"

    @pytest.mark.asyncio
    async def test_price_uses_highest_liquidity_pair(self) -> None:
        """Pump.fun tokens can expose both the curve pair ($0 liquidity, $0.15
        volume) and the migrated Raydium pool — volume/liquidity must come from
        the REAL pool, not pairs[0]."""
        from app.domains.scanner.modules import _get_price_consensus

        curve = {
            "dexId": "pumpfun",
            "pairAddress": "curvePair111",
            "priceUsd": "0.5",
            "volume": {"h24": 0.15},
            "liquidity": {"usd": 0},
            "priceChange": {"h24": 0},
            "fdv": 0,
        }
        migrated = {
            "dexId": "raydium",
            "pairAddress": "realPool222",
            "priceUsd": "0.55",
            "volume": {"h24": 250000},
            "liquidity": {"usd": 48000},
            "priceChange": {"h24": 12},
            "fdv": 1000000,
        }
        with (
            patch("app.domains.scanner.modules.market.price.PREFER_OWN_DATA", False),
            patch("app.domains.scanner.modules._core.PREFER_OWN_DATA", False),
            patch(
                "app.domains.scanner.modules.market.price._fetch_dexscreener",
                AsyncMock(return_value={"pairs": [curve, migrated]}),
            ),
        ):
            result = await _get_price_consensus(NONPUMP_MINT, "solana")
        assert result["volume_24h"] == 250000
        assert result["liquidity_usd"] == 48000
        assert result["dex"] == "raydium"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Fail-open on API errors
# ═══════════════════════════════════════════════════════════════════════════


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_holder_fail_open_on_api_error(self) -> None:
        """API errors never crash holder counting — current value is kept."""
        import app.databus.gmgn_provider as gmgn_mod
        from app.domains.scanner.modules import _get_holder_data

        with (
            patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False),
            patch(
                "app.domains.scanner.modules.holders._solana_rpc_call",
                AsyncMock(side_effect=httpx.HTTPError("rpc down")),
            ),
            patch(
                "app.domains.scanner.modules.holders._fetch_dexscreener",
                AsyncMock(side_effect=httpx.HTTPError("dex down")),
            ),
            patch.object(gmgn_mod, "get_gmgn_provider", side_effect=RuntimeError("gmgn down")),
        ):
            result = await _get_holder_data(NONPUMP_MINT, "solana")
        assert isinstance(result, dict)
        assert result["check"] == "holders"
        assert result["holder_count"] == 0  # fail-open: keep current value

    @pytest.mark.asyncio
    async def test_liquidity_fail_open_on_api_error(self) -> None:
        """Liquidity verifier never crashes when the DEX/GMGN fetchers fail."""
        import app.databus.gmgn_provider as gmgn_mod
        from app.domains.scanner.modules import _check_liquidity_lock_multi

        with (
            patch("app.domains.scanner.modules.holders.PREFER_OWN_DATA", False),
            patch(
                "app.domains.scanner.modules.holders._fetch_dexscreener",
                AsyncMock(side_effect=httpx.HTTPError("dex down")),
            ),
            patch.object(gmgn_mod, "get_gmgn_provider", side_effect=RuntimeError("gmgn down")),
            patch(
                "app.domains.detectors.smart_contract_honeypot_detector.HoneypotDetector.analyze_token",
                side_effect=ImportError("no honeypot detector in test"),
            ),
        ):
            result = await _check_liquidity_lock_multi(NONPUMP_MINT, "solana")
        assert isinstance(result, dict)
        assert result["check"] == "liquidity"
        assert result["liquidity_usd"] == 0.0  # fail-open: current value

    @pytest.mark.asyncio
    async def test_verify_lock_pumpfun_detection_fail_open(self) -> None:
        """Pump.fun detection failures fall through to the normal path without
        raising (non-pump result, current behavior preserved)."""
        verifier = LiquidityVerifier()
        with (
            patch.object(
                verifier,
                "_detect_pumpfun",
                AsyncMock(side_effect=RuntimeError("detector down")),
            ),
            patch.object(verifier, "_check_solana_lp_burn", AsyncMock(return_value=False)),
        ):
            # verify_lock must not raise — _detect_pumpfun is awaited bare in
            # the short-circuit; the detector itself is fail-open internally,
            # so this guards the contract.
            report = await verifier.verify_lock(NONPUMP_MINT, "solana")
        assert report.chain == "solana"
        assert isinstance(report.total_locked_percentage, float)
