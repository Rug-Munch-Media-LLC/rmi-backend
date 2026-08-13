"""Pure tests: every referral/trade link lands on the ACTUAL token's chart page.

Locks in the audit of ``config.DEX_REF_LINKS`` + ``formatters/_base.py``:

* every chart-platform URL template embeds ``{token}``, and the scan address
  appears in the rendered ``dex_buttons`` / ``footer_links`` output
* GMGN / DexScreener / DexTools / Birdeye resolve to token/chart pages
  (``/token/`` or ``/pair-explorer/`` path segments + the exact address)
* Jupiter / 1inch / PancakeSwap stay on swap pages with the token embedded
  (users land on the exact token pair — acceptable, kept)
* OdinBot / Banana Gun are Telegram signup links with no per-token URL
  (documented — the ref rides in the ``start=`` payload)
* Axiom / Padre stay on home pages — no verifiable per-token deep link
  (documented + [REVIEW] in config.py)

All tests are pure: no network, no DB, no Telegram API.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.config import DEX_REF_LINKS
from app.domains.telegram.rugmunchbot.formatters import _base as fb

SOL_TOK = "So11111111111111111111111111111111111111112"
EVM_TOK = "0x" + "a" * 38

# Platforms whose templates embed the token address. Swap pages (Jupiter /
# 1inch / PancakeSwap) are intentional: the token is embedded so the user
# lands on the exact token pair, never a generic home page.
TOKEN_EMBEDDING_PLATFORMS = ("jupiter", "photon", "gmgn", "birdeye", "oneinch", "pancakeswap")

# Telegram signup bots + home-only terminals: no per-token URL to embed.
SIGNUP_OR_HOME_PLATFORMS = ("odinbot", "banana", "axiom", "padre")


def _by_url(buttons: list[fb.InlineKeyboardButton]) -> dict[str, str]:
    """Map button label → URL (skipping buttons without a URL)."""
    return {b.text: (b.url or "") for b in buttons}


def _token_for(chain: str) -> str:
    return SOL_TOK if chain == "solana" else EVM_TOK


class TestTemplatesEmbedToken:
    @pytest.mark.parametrize("platform", TOKEN_EMBEDDING_PLATFORMS)
    def test_chart_or_swap_template_embeds_token(self, platform: str) -> None:
        assert "{token}" in DEX_REF_LINKS[platform]["url"], f"{platform} template drops the token"

    @pytest.mark.parametrize("platform", SIGNUP_OR_HOME_PLATFORMS)
    def test_signup_or_home_template_has_no_token_placeholder(self, platform: str) -> None:
        # Signup/home links must not pretend to carry a token they can't use.
        assert "{token}" not in DEX_REF_LINKS[platform]["url"], (
            f"{platform} is a signup/home link and must not carry a token placeholder"
        )


class TestAddressAppearsInButtons:
    @pytest.mark.parametrize(
        ("platform", "chain", "host_frag"),
        [
            ("jupiter", "solana", "jup.ag"),
            ("photon", "solana", "photon-sol"),
            ("gmgn", "solana", "gmgn.ai"),
            ("birdeye", "solana", "birdeye.so"),
            ("oneinch", "ethereum", "1inch.io"),
            ("pancakeswap", "ethereum", "pancakeswap.finance"),
        ],
    )
    def test_scan_address_embedded_per_platform(self, platform: str, chain: str, host_frag: str) -> None:
        tok = _token_for(chain)
        by = _by_url(fb.dex_buttons(tok, chain))
        matching = [u for u in by.values() if host_frag in u]
        assert matching, f"no {platform} button rendered for {chain}"
        assert any(tok in u for u in matching), f"{platform} button lost the scan address"

    def test_no_unfilled_placeholder_survives_rendering(self) -> None:
        buttons = fb.dex_buttons(SOL_TOK, "solana")
        assert buttons
        for b in buttons:
            if b.url:
                assert "{" not in b.url
                assert "}" not in b.url


class TestChartPages:
    def test_gmgn_button_is_token_chart_page(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        gmgn = next(u for u in by.values() if "gmgn.ai" in u)
        assert f"/sol/token/{SOL_TOK}" in gmgn  # token chart page, not home

    def test_dexscreener_button_is_chain_token_page(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        ds = next(u for u in by.values() if "dexscreener.com" in u)
        assert f"/solana/{SOL_TOK}" in ds

    def test_dextools_button_is_pair_explorer_page(self) -> None:
        by = _by_url(fb.dex_buttons(EVM_TOK, "ethereum"))
        dt = next(u for u in by.values() if "dextools.io" in u)
        assert f"/en/ether/pair-explorer/{EVM_TOK}" in dt

    def test_birdeye_button_is_token_page(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        be = next(u for u in by.values() if "birdeye.so" in u)
        assert f"/token/{SOL_TOK}" in be

    def test_jupiter_button_is_token_swap_page(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        jup = next(u for u in by.values() if "jup.ag" in u)
        assert f"/swap/SOL-{SOL_TOK}" in jup  # swap with the exact token pair


class TestFooterLinksEmbedToken:
    def test_footer_chart_links_embed_address(self) -> None:
        out = fb.footer_links(SOL_TOK, "solana")
        assert f"https://gmgn.ai/sol/token/{SOL_TOK}" in out
        assert f"https://dexscreener.com/solana/{SOL_TOK}" in out
        assert f"https://www.dextools.io/app/en/sol/pair-explorer/{SOL_TOK}" in out
        assert f"https://rugmunch.io/rugcharts?token={SOL_TOK}" in out
        assert f"https://rugmunch.io/rugmaps?token={SOL_TOK}" in out

    def test_footer_evm_links_embed_address(self) -> None:
        out = fb.footer_links(EVM_TOK, "ethereum")
        assert f"https://gmgn.ai/eth/token/{EVM_TOK}" in out
        assert f"https://dexscreener.com/ethereum/{EVM_TOK}" in out
        assert f"https://www.dextools.io/app/en/ether/pair-explorer/{EVM_TOK}" in out


class TestRefCodes:
    @pytest.mark.parametrize(
        ("platform", "chain", "host_frag", "ref_frag"),
        [
            ("gmgn", "solana", "gmgn.ai", "?ref=rugmunch"),
            ("jupiter", "solana", "jup.ag", "?ref=rugmunch"),
            ("photon", "solana", "photon-sol", "?handle=rugmunch"),
            ("oneinch", "ethereum", "1inch.io", "?ref=rugmunch"),
            ("pancakeswap", "ethereum", "pancakeswap.finance", "?ref=rugmunch"),
            ("odinbot", "solana", "t.me/OdinBot", "start=x5pyi0"),
            ("banana", "solana", "t.me/BananaGunBot", "start=rugmunch"),
            ("axiom", "solana", "axiom.trade", "?ref=crmuncher"),
            ("padre", "solana", "padre.trade", "?ref=crm"),
        ],
    )
    def test_ref_code_embedded(self, platform: str, chain: str, host_frag: str, ref_frag: str) -> None:
        tok = _token_for(chain)
        by = _by_url(fb.dex_buttons(tok, chain))
        matching = [u for u in by.values() if host_frag in u]
        assert matching, f"no {platform} button rendered for {chain}"
        # Accept either `?ref=` (first query param) or `&ref=` (later param,
        # e.g. PancakeSwap's swap URL already carries ?inputCurrency=...).
        ref_token = ref_frag.lstrip("?&")
        assert any(f"?{ref_token}" in u or f"&{ref_token}" in u for u in matching), f"{platform} missing ref {ref_frag}"

    def test_birdeye_is_ref_free_by_design(self) -> None:
        by = _by_url(fb.dex_buttons(SOL_TOK, "solana"))
        be = next(u for u in by.values() if "birdeye.so" in u)
        # Birdeye has no referral program — no ref/handle param expected.
        assert "?ref=" not in be
        assert "?handle=" not in be


class TestSignupAndHomeLinks:
    @pytest.mark.parametrize("platform", ["odinbot", "banana"])
    def test_telegram_signup_links_kept(self, platform: str) -> None:
        # Signup links: no per-token URL exists; the ref is the start payload.
        assert "t.me/" in DEX_REF_LINKS[platform]["url"]
        assert "start=" in DEX_REF_LINKS[platform]["url"]

    @pytest.mark.parametrize("platform", ["axiom", "padre"])
    def test_home_links_kept_with_ref(self, platform: str) -> None:
        # No verifiable per-token deep link — kept as home pages with the ref
        # embedded. [REVIEW] switch to a token route when the platform adds one.
        assert "?ref=" in DEX_REF_LINKS[platform]["url"]
        assert "{token}" not in DEX_REF_LINKS[platform]["url"]
