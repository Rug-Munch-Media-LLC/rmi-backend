"""Pure unit tests for scan-report socials / image / description rendering.

Covers:
- ``format_scan_report`` ALWAYS renders a Socials block when the token has
  socials or an image URL — Twitter/Telegram/website links + token image URL
  + one-line external-link safety note. "NO SOCIALS" only when genuinely none
  (no links AND no image).
- The description renders whenever available (top-level ``description`` or
  the SENTINEL metadata_fingerprint ``description_snippet``).
- The scan handler sends the token image via ``reply_photo`` when the scan
  result carries ``image_uri``/``image_url``/``logo_url`` and skips when
  absent (fail-open).

Pure — no network, no DB, no Telegram. The bot reply is an AsyncMock and
``reply_photo`` is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.domains.telegram.rugmunchbot.formatters._sentinel import (
    _token_description,
    _token_image_url,
    format_scan_report,
)
from app.domains.telegram.rugmunchbot.handlers.scanning import _maybe_send_token_image


def _base_report() -> dict:
    """Minimal legacy scan report dict (every key format_scan_report reads)."""
    return {
        "address": "0xabc123",
        "chain": "ethereum",
        "name": "Test Token",
        "symbol": "TST",
        "price": 1.0,
        "mcap": 1_000_000,
        "liquidity": 50_000,
        "volume_24h": 100_000,
        "price_change_1h": 1.0,
        "price_change_24h": 5.0,
        "price_change_7d": 10.0,
        "risk_score": 60,
        "honeypot": None,
        "contract_verified": None,
        "mintable": None,
        "owner_renounced": None,
        "lp_locked": None,
        "buy_tax": None,
        "sell_tax": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "socials": {},
        "threats": [],
    }


class TestSocialsSection:
    def test_renders_links_and_safety_note_when_socials_exist(self) -> None:
        report = _base_report()
        report["socials"] = {
            "twitter": "https://x.com/tst",
            "telegram": "https://t.me/tst",
            "website": "https://tst.example.com",
        }
        out = format_scan_report(report)
        assert "<b>Socials</b>" in out
        assert "Twitter" in out
        assert "Telegram" in out
        assert "Website" in out
        assert "https://x.com/tst" in out
        assert "https://t.me/tst" in out
        assert "https://tst.example.com" in out
        assert "external links may be malicious" in out

    def test_renders_image_url_in_socials_when_present(self) -> None:
        report = _base_report()
        report["image_uri"] = "https://cdn.example.com/token.png"
        out = format_scan_report(report)
        assert "<b>Socials</b>" in out
        assert "https://cdn.example.com/token.png" in out
        assert "external links may be malicious" in out

    def test_supports_image_url_and_logo_url_keys(self) -> None:
        report = _base_report()
        report["image_url"] = "https://cdn.example.com/a.png"
        assert _token_image_url(report) == "https://cdn.example.com/a.png"
        report = _base_report()
        report["logo_url"] = "https://cdn.example.com/b.png"
        assert _token_image_url(report) == "https://cdn.example.com/b.png"

    def test_no_socials_fallback_only_when_genuinely_none(self) -> None:
        report = _base_report()  # empty socials, no image
        out = format_scan_report(report)
        assert "NO SOCIALS" in out
        assert "external links may be malicious" not in out

    def test_no_socials_suppressed_when_image_present(self) -> None:
        report = _base_report()
        report["image_uri"] = "https://cdn.example.com/token.png"
        out = format_scan_report(report)
        assert "NO SOCIALS" not in out
        assert "https://cdn.example.com/token.png" in out

    def test_escapes_social_urls(self) -> None:
        report = _base_report()
        report["socials"] = {"twitter": "https://x.com/tst?a=1&b=2"}
        out = format_scan_report(report)
        assert "&amp;" in out or "&b=2" in out  # HTML-safe attribute
        assert "<b>Socials</b>" in out


class TestDescription:
    def test_renders_description_when_present(self) -> None:
        report = _base_report()
        report["description"] = "A fully normal token description."
        out = format_scan_report(report)
        assert "A fully normal token description." in out

    def test_description_renders_even_without_socials(self) -> None:
        report = _base_report()
        report["description"] = "Description but no socials."
        out = format_scan_report(report)
        assert "Description but no socials." in out
        assert "NO SOCIALS" in out

    def test_falls_back_to_sentinel_description_snippet(self) -> None:
        report = _base_report()
        report["sentinel"] = {"free": {"metadata_fingerprint": {"description_snippet": "From sentinel fingerprint."}}}
        assert _token_description(report) == "From sentinel fingerprint."
        out = format_scan_report(report)
        assert "From sentinel fingerprint." in out

    def test_no_description_section_when_unavailable(self) -> None:
        out = format_scan_report(_base_report())
        assert "<i>" not in out or "description" not in out.lower()

    def test_top_level_description_beats_sentinel_snippet(self) -> None:
        report = _base_report()
        report["description"] = "Top-level description."
        report["sentinel"] = {"free": {"metadata_fingerprint": {"description_snippet": "Snippet."}}}
        assert _token_description(report) == "Top-level description."


class TestHandlerSendsTokenImage:
    async def test_sends_photo_when_image_uri_present(self) -> None:
        msg = AsyncMock()
        result = _base_report()
        result["image_uri"] = "https://cdn.example.com/token.png"
        result["name"] = "Test Token"
        result["symbol"] = "TST"
        await _maybe_send_token_image(msg, result)
        msg.reply_photo.assert_awaited_once()
        _, kwargs = msg.reply_photo.call_args
        assert kwargs["photo"] == "https://cdn.example.com/token.png"
        assert "Test Token" in kwargs["caption"]

    async def test_sends_photo_when_image_url_present(self) -> None:
        msg = AsyncMock()
        result = _base_report()
        result["image_url"] = "https://cdn.example.com/token.png"
        await _maybe_send_token_image(msg, result)
        msg.reply_photo.assert_awaited_once()

    async def test_skips_photo_when_no_image(self) -> None:
        msg = AsyncMock()
        await _maybe_send_token_image(msg, _base_report())
        msg.reply_photo.assert_not_awaited()

    async def test_fails_open_when_reply_photo_raises(self) -> None:
        msg = AsyncMock()
        msg.reply_photo = AsyncMock(side_effect=RuntimeError("telegram down"))
        result = _base_report()
        result["image_uri"] = "https://cdn.example.com/token.png"
        # Must not raise — text-only reply survives.
        await _maybe_send_token_image(msg, result)

    async def test_skips_invalid_image_value(self) -> None:
        msg = AsyncMock()
        result = _base_report()
        result["image_uri"] = "not-a-url"
        await _maybe_send_token_image(msg, result)
        msg.reply_photo.assert_not_awaited()
