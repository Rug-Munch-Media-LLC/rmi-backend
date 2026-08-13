"""Pure tests for referral trade buttons wired into every scan output.

Covers ``app/domains/telegram/rugmunchbot/formatters/_base.py``:

* ``dex_buttons`` / ``dex_button_rows`` — the full ``DEX_REF_LINKS`` set
  rendered chain-filtered, with the scan's token address embedded in every
  token-page URL and the owner ref code attached (GMGN ``?ref=rugmunch``,
  OdinBot ``start=x5pyi0``, Axiom ``?ref=crmuncher``, Padre ``?ref=crm``)
* row grouping within the per-row budget (≤ per_row), highest-value
  platforms (GMGN / Jupiter) first
* DexScreener + DexTools chart buttons kept on known chains
* empty/unknown chain → safe minimal set (chain-agnostic bot links only,
  never a token-page URL built for the wrong chain)

All tests are pure — no network, no DB, no Telegram API.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.formatters import _base as fb

SOL_TOK = "So11111111111111111111111111111111111111112"
EVM_TOK = "0x" + "a" * 38


def _by_url(buttons: list[fb.InlineKeyboardButton]) -> dict[str, str]:
    """Map button label → URL (skipping buttons without a URL)."""
    return {b.text: (b.url or "") for b in buttons}


class TestDexButtonsChainFiltered:
    def test_solana_renders_full_solana_set(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        for platform in ("GMGN.ai", "Jupiter", "Photon", "OdinBot", "Banana Gun", "Axiom", "Padre", "Birdeye"):
            assert any(platform in label for label in by), f"missing {platform} on solana"
        # EVM-only platforms must not leak onto Solana.
        assert not any("1inch" in label for label in by)
        assert not any("PancakeSwap" in label for label in by)

    def test_ethereum_renders_evm_platforms(self) -> None:
        by = _by_url(fb.dex_buttons(EVM_TOK, "ethereum"))
        for platform in ("GMGN.ai", "Photon", "Banana Gun", "Padre", "Birdeye", "1inch", "PancakeSwap"):
            assert any(platform in label for label in by), f"missing {platform} on ethereum"
        # Solana-only platforms excluded on EVM.
        assert not any("Jupiter" in label for label in by)
        assert not any("OdinBot" in label for label in by)
        assert not any("Axiom" in label for label in by)

    def test_token_embedded_in_every_token_page_url(self) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        assert buttons
        assert all(b.url for b in buttons)
        # Token-page platforms carry the exact scan address in their URL.
        for host in ("jup.ag", "gmgn.ai", "photon-sol", "birdeye.so"):
            assert any(host in b.url and SOL_TOK in b.url for b in buttons), f"{host} URL lacks token"

    @pytest.mark.parametrize(
        ("host", "ref_frag"),
        [
            ("gmgn.ai", "?ref=rugmunch"),
            ("t.me/OdinBot", "start=x5pyi0"),
            ("axiom.trade", "?ref=crmuncher"),
            ("padre.trade", "?ref=crm"),
        ],
    )
    def test_ref_codes_embedded(self, host: str, ref_frag: str) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        matching = [b for b in buttons if host in (b.url or "")]
        assert matching, f"no solana button for {host}"
        assert any(ref_frag in (b.url or "") for b in matching), f"{host} missing ref {ref_frag}"

    def test_chart_buttons_kept_on_known_chain(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        assert any("dexscreener.com/solana/" in u for u in by.values())
        assert any("dextools.io/app/en/sol/pair-explorer/" in u for u in by.values())


class TestDexButtonOrdering:
    def test_gmgn_and_jupiter_lead(self) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        assert "GMGN" in buttons[0].text
        assert "Jupiter" in buttons[1].text

    def test_referral_platforms_precede_chart_buttons(self) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        labels = [b.text for b in buttons]
        assert labels.index("🦅 Birdeye") < labels.index("🦆 DexScreener")


class TestDexButtonRowsGrouping:
    def test_rows_never_exceed_per_row_budget(self) -> None:
        rows = fb.dex_button_rows(SOL_TOK, "solana", per_row=3)
        assert rows
        assert all(len(row) <= 3 for row in rows)
        assert max(len(row) for row in rows) <= 3

    def test_default_rows_at_most_two(self) -> None:
        rows = fb.dex_button_rows(SOL_TOK, "solana")
        assert all(len(row) <= fb.DEX_BUTTONS_PER_ROW for row in rows)

    def test_flat_matches_rows(self) -> None:
        flat = fb.dex_buttons(SOL_TOK, "solana")
        assert flat == [b for row in fb.dex_button_rows(SOL_TOK, "solana") for b in row]

    def test_rows_keep_chart_buttons(self) -> None:
        flat = [b for row in fb.dex_button_rows(SOL_TOK, "solana", per_row=3) for b in row]
        urls = " ".join(b.url or "" for b in flat)
        assert "dexscreener.com/solana/" in urls
        assert "dextools.io/app/en/sol/pair-explorer/" in urls


class TestUnknownChainMinimalSet:
    @pytest.mark.parametrize("chain", ["", "unknown", "UNKNOWN"])
    def test_unknown_chain_returns_safe_minimal_bots(self, chain: str) -> None:
        buttons = fb.dex_buttons(SOL_TOK, chain)
        urls = " ".join(b.url or "" for b in buttons)
        # Only chain-agnostic Telegram bot links carry our refs — never a
        # token-page URL that could be built against the wrong chain.
        assert "t.me/OdinBot?start=x5pyi0" in urls
        assert "t.me/BananaGunBot?start=rugmunch" in urls
        assert "gmgn.ai" not in urls
        assert "jup.ag" not in urls
        assert "dexscreener.com" not in urls
        assert "dextools.io" not in urls

    def test_unknown_chain_rows_within_budget(self) -> None:
        rows = fb.dex_button_rows(SOL_TOK, "", per_row=3)
        assert rows
        assert all(len(row) <= 3 for row in rows)
