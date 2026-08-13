"""
Pump.fun metadata resolver tests — MetadataFingerprinter.

Pure unit tests: the Pump.fun + DexScreener + Birdeye fetchers are all mocked
(no network, no DB). Covers:
  1. Pump.fun mint detection (Solana mint ending in "pump")
  2. Resolver maps description / socials / creator from a mocked Pump.fun response
  3. Fail-open on API error -> None (no crash), at resolver and scan level
  4. Deployer populated from the Pump.fun ``creator`` field
  5. Non-Pump.fun tokens keep the DexScreener fallback (Pump.fun never called)
"""

from __future__ import annotations

import os

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-key-thats-32-chars-long!")
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from unittest.mock import AsyncMock, MagicMock

import httpx

from app.domains.scanners.metadata_fingerprint import (
    PUMPFUN_PROGRAM_ID,
    MetadataFingerprinter,
    PumpFunMetadata,
    is_pumpfun_mint,
)

# A realistic-looking Pump.fun mint (address ending in "pump")
PUMP_MINT = "CJPZiyGfsNQdjrDGf233uW9BoVdgn6KpoVAshwPkpump"
NON_PUMP_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
CREATOR = "DeployerWalletAddrXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
PROGRAM_ID = PUMPFUN_PROGRAM_ID

PUMPFUN_PAYLOAD = {
    "mint": PUMP_MINT,
    "name": "Test Pump Token",
    "symbol": "TPT",
    "description": "A real pump.fun description with some meat to it.",
    "image_uri": "https://example.com/img.png",
    "twitter": "https://x.com/testpump",
    "telegram": "https://t.me/testpump",
    "website": "https://testpump.example",
    "creator": CREATOR,
    "created_timestamp": "2026-08-01T00:00:00Z",
}


def _resp(data: dict, status: int = 200) -> MagicMock:
    """Sync mock httpx response: status_code + .json() + empty .text."""
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.text = ""
    return m


def _client(resp: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.get.return_value = resp
    return client


def _fingerprinter(
    dexscreener_data: dict | None = None,
    pumpfun_data: dict | None = None,
    pumpfun_error: Exception | None = None,
) -> tuple[MetadataFingerprinter, AsyncMock]:
    """Build a MetadataFingerprinter with mocked clients.

    Returns (fingerprinter, pumpfun_client) so tests can assert call behavior.
    """
    dex = _client(_resp(dexscreener_data if dexscreener_data is not None else {"pairs": []}))
    birdeye = _client(_resp({"data": {}}))
    pumpfun = AsyncMock()
    if pumpfun_error is not None:
        pumpfun.get.side_effect = pumpfun_error
    else:
        pumpfun.get.return_value = _resp(pumpfun_data if pumpfun_data is not None else {})
    fp = MetadataFingerprinter(dexscreener_client=dex, pumpfun_client=pumpfun)
    fp.birdeye_client = birdeye
    return fp, pumpfun


# ═══════════════════════════════════════════════════════════════════════
# 1. Pump.fun mint detection
# ═══════════════════════════════════════════════════════════════════════


class TestIsPumpfunMint:
    def test_solana_mint_ending_in_pump(self) -> None:
        assert is_pumpfun_mint(PUMP_MINT, "solana") is True

    def test_case_insensitive(self) -> None:
        assert is_pumpfun_mint(PUMP_MINT.upper(), "solana") is True

    def test_non_pump_solana_mint(self) -> None:
        assert is_pumpfun_mint(NON_PUMP_SOL, "solana") is False

    def test_evm_chain_is_never_pumpfun(self) -> None:
        assert is_pumpfun_mint("0x1234", "ethereum") is False

    def test_empty_or_missing_chain(self) -> None:
        assert is_pumpfun_mint("", "solana") is False
        assert is_pumpfun_mint(PUMP_MINT, "") is False


# ═══════════════════════════════════════════════════════════════════════
# 2. Resolver maps description / socials / creator
# ═══════════════════════════════════════════════════════════════════════


class TestFetchPumpfunMetadata:
    async def test_maps_fields_from_response(self) -> None:
        fp, _ = _fingerprinter(pumpfun_data=PUMPFUN_PAYLOAD)
        meta = await fp.fetch_pumpfun_metadata(PUMP_MINT)
        assert isinstance(meta, PumpFunMetadata)
        assert meta.mint == PUMP_MINT
        assert meta.name == "Test Pump Token"
        assert meta.symbol == "TPT"
        assert meta.description == "A real pump.fun description with some meat to it."
        assert meta.twitter == "https://x.com/testpump"
        assert meta.telegram == "https://t.me/testpump"
        assert meta.website == "https://testpump.example"
        assert meta.creator == CREATOR

    async def test_builds_social_links_from_handles(self) -> None:
        meta = PumpFunMetadata(
            mint=PUMP_MINT,
            twitter="@pump_handle",
            telegram="pump_channel",
            website="pump.example",
        )
        links = MetadataFingerprinter._pumpfun_social_links(meta)
        assert "https://x.com/pump_handle" in links
        assert "https://t.me/pump_channel" in links
        assert "https://pump.example" in links

    async def test_fail_open_on_http_error(self) -> None:
        fp, _ = _fingerprinter(pumpfun_error=httpx.ConnectError("boom"))
        assert await fp.fetch_pumpfun_metadata(PUMP_MINT) is None

    async def test_fail_open_on_timeout(self) -> None:
        fp, _ = _fingerprinter(pumpfun_error=httpx.TimeoutException("slow"))
        assert await fp.fetch_pumpfun_metadata(PUMP_MINT) is None

    async def test_fail_open_on_non_200(self) -> None:
        fp, _ = _fingerprinter(pumpfun_data={})
        fp.pumpfun_client.get.return_value = _resp({}, status=404)
        assert await fp.fetch_pumpfun_metadata(PUMP_MINT) is None

    async def test_fail_open_on_malformed_payload(self) -> None:
        fp, _ = _fingerprinter(pumpfun_data={"nope": True})
        assert await fp.fetch_pumpfun_metadata(PUMP_MINT) is None

    async def test_fail_open_on_empty_mint(self) -> None:
        fp, _ = _fingerprinter(pumpfun_data=PUMPFUN_PAYLOAD)
        assert await fp.fetch_pumpfun_metadata("") is None


# ═══════════════════════════════════════════════════════════════════════
# 3. End-to-end: scan metadata path populates description/socials/deployer
# ═══════════════════════════════════════════════════════════════════════


class TestPumpfunInScanMetadata:
    async def test_pumpfun_token_populates_metadata_and_deployer(self) -> None:
        fp, pumpfun = _fingerprinter(dexscreener_data={"pairs": []}, pumpfun_data=PUMPFUN_PAYLOAD)
        report = await fp.analyze_token_metadata(PUMP_MINT, "solana")

        # Description + socials populate (kills NO_DESCRIPTION / NO_SOCIALS)
        assert report.description_length > 0
        assert report.social_link_count >= 3
        assert "NO_DESCRIPTION" not in report.warnings
        assert "NO_SOCIALS" not in report.warnings
        assert "https://x.com/testpump" in report.social_domains or "x.com" in report.social_domains

        # Deployer populated from the Pump.fun creator
        assert report.creator == CREATOR
        pumpfun.get.assert_awaited_once()

    async def test_pumpfun_creator_program_id_is_not_reported_as_deployer(self) -> None:
        payload = dict(PUMPFUN_PAYLOAD, creator=PROGRAM_ID)
        fp, _ = _fingerprinter(dexscreener_data={"pairs": []}, pumpfun_data=payload)
        report = await fp.analyze_token_metadata(PUMP_MINT, "solana")
        assert report.creator == ""

    async def test_pumpfun_fail_open_does_not_crash_scan(self) -> None:
        fp, _ = _fingerprinter(dexscreener_data={"pairs": []}, pumpfun_error=httpx.ConnectError("boom"))
        report = await fp.analyze_token_metadata(PUMP_MINT, "solana")
        # No crash; falls back to previous (empty) metadata + warnings
        assert report.creator == ""
        assert report.description_length == 0
        assert any(w.startswith("NO_DESCRIPTION") for w in report.warnings)
        assert any(w.startswith("NO_SOCIALS") for w in report.warnings)

    async def test_non_pump_token_uses_dexscreener_and_skips_pumpfun(self) -> None:
        dexscreener_payload = {
            "pairs": [
                {
                    "baseToken": {"name": "USD Coin", "symbol": "USDC"},
                    "info": {
                        "description": "DexScreener description",
                        "socials": [{"type": "twitter", "url": "https://x.com/usdcoin"}],
                        "websites": [{"url": "https://example.com"}],
                    },
                }
            ]
        }
        fp, pumpfun = _fingerprinter(dexscreener_data=dexscreener_payload, pumpfun_data=PUMPFUN_PAYLOAD)
        report = await fp.analyze_token_metadata(NON_PUMP_SOL, "solana")

        # DexScreener fallback is wired for non-pump tokens (task 3)
        assert report.name == "USD Coin"
        assert report.description_length > 0
        assert report.social_link_count >= 1
        assert "NO_DESCRIPTION" not in report.warnings
        assert "NO_SOCIALS" not in report.warnings
        # Pump.fun resolver must not be invoked for non-pump mints
        pumpfun.get.assert_not_awaited()
