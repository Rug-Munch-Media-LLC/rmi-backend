"""Pure tests for referral + trading links in the RugMunch bot footer.

Covers the helpers in ``app/domains/telegram/rugmunchbot/formatters/_base.py``:

* ``gmgn_url`` / ``dextools_url`` — chain alias mapping, ref-code omission
  when empty, and ref-code inclusion when set (dict or env override)
* ``footer_links`` — GMGN → DexScreener → DexTools → RugCharts → RugMaps
  → /help link order, HTML safety, and the no-address (bare) variant
* ``dex_buttons`` — inline keyboard includes DexScreener + DexTools

All tests are pure (no network, no DB).
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from app.domains.telegram.rugmunchbot.formatters import _base as fb

ADDR = "0x" + "a" * 38


def _clear_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset every ref source so tests start with no codes set."""
    for key in ("gmgn", "dextools"):
        monkeypatch.delenv(f"RMI_REF_{key.upper()}", raising=False)
        monkeypatch.setitem(fb.REF_CODES, key, "")


# --- gmgn_url -------------------------------------------------------------


class TestGmgnUrl:
    @pytest.mark.parametrize(
        ("chain", "slug"),
        [
            ("solana", "sol"),
            ("ethereum", "eth"),
            ("base", "base"),
            ("bsc", "bsc"),
            ("arbitrum", "arbitrum"),
            ("SOLANA", "sol"),  # case-insensitive
            ("weirdchain", "weirdchain"),  # unknown → passthrough lowercase
        ],
    )
    def test_chain_alias_mapping(self, monkeypatch: pytest.MonkeyPatch, chain: str, slug: str) -> None:
        _clear_refs(monkeypatch)
        assert fb.gmgn_url(ADDR, chain) == f"https://gmgn.ai/{slug}/token/{ADDR}"

    def test_omits_ref_when_code_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        url = fb.gmgn_url(ADDR, "solana")
        assert url == f"https://gmgn.ai/sol/token/{ADDR}"
        assert "?" not in url

    def test_includes_ref_when_code_set_in_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        monkeypatch.setitem(fb.REF_CODES, "gmgn", "muncher")
        url = fb.gmgn_url(ADDR, "solana")
        assert url == f"https://gmgn.ai/sol/token/{ADDR}?ref=muncher"

    def test_env_override_beats_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(fb.REF_CODES, "gmgn", "dictcode")
        monkeypatch.setenv("RMI_REF_GMGN", "envcode")
        assert fb.gmgn_url(ADDR, "solana").endswith("?ref=envcode")

    def test_empty_env_override_omits_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(fb.REF_CODES, "gmgn", "dictcode")
        monkeypatch.setenv("RMI_REF_GMGN", "")
        assert "?ref=" not in fb.gmgn_url(ADDR, "solana")


# --- dextools_url ---------------------------------------------------------


class TestDextoolsUrl:
    @pytest.mark.parametrize(
        ("chain", "slug"),
        [
            ("solana", "sol"),
            ("ethereum", "ether"),
            ("base", "base"),
            ("bsc", "bnb"),
            ("arbitrum", "arbitrum"),
            ("polygon", "polygon"),
            ("avalanche", "avalanche"),
            ("optimism", "optimism"),
        ],
    )
    def test_chain_alias_mapping(self, monkeypatch: pytest.MonkeyPatch, chain: str, slug: str) -> None:
        _clear_refs(monkeypatch)
        assert fb.dextools_url(ADDR, chain) == f"https://www.dextools.io/app/en/{slug}/pair-explorer/{ADDR}"

    def test_omits_ref_when_code_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        assert fb.dextools_url(ADDR, "solana") == f"https://www.dextools.io/app/en/sol/pair-explorer/{ADDR}"

    def test_includes_ref_when_code_set_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        monkeypatch.setenv("RMI_REF_DEXTOOLS", "dtcode")
        assert fb.dextools_url(ADDR, "solana").endswith("?ref=dtcode")


# --- footer_links ---------------------------------------------------------


class TestFooterLinks:
    def test_link_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        out = fb.footer_links(ADDR, "ethereum")
        order = ["GMGN", "DexScreener", "DexTools", "RugCharts", "RugMaps", "/help"]
        positions = [out.index(name) for name in order]
        assert positions == sorted(positions), f"link order violated: {dict(zip(order, positions, strict=True))}"

    def test_contains_all_trade_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        out = fb.footer_links(ADDR, "ethereum")
        assert 'href="https://gmgn.ai/eth/token/' in out
        assert 'href="https://dexscreener.com/ethereum/' in out
        assert 'href="https://www.dextools.io/app/en/ether/pair-explorer/' in out
        assert 'href="https://rugmunch.io/rugcharts?token=' in out
        assert 'href="https://rugmunch.io/rugmaps?token=' in out

    def test_gmgn_ref_appears_in_footer_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        monkeypatch.setenv("RMI_REF_GMGN", "muncher")
        out = fb.footer_links(ADDR, "solana")
        assert "https://gmgn.ai/sol/token/" in out
        assert "?ref=muncher" in out

    def test_ref_param_omitted_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        out = fb.footer_links(ADDR, "solana")
        assert "?ref=" not in out

    def test_bare_footer_has_no_trade_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        out = fb.footer_links()
        assert "/help" in out
        assert "Rug Munch Bot" in out
        assert "GMGN" not in out
        assert "RugCharts" not in out

    def test_html_safe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        out = fb.footer_links(ADDR, "ethereum")
        # No raw markdown, no raw `<` outside tags, tags balanced.
        assert "**" not in out
        assert out.count("<a") == out.count("</a>")
        stripped = re.sub(r"<[^>]+>", "", unescape(out))
        assert "<" not in stripped
        assert ">" not in stripped


# --- dex_buttons ----------------------------------------------------------


class TestDexButtons:
    def test_includes_dexscreener_and_dextools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        buttons = fb.dex_buttons(ADDR, "solana")
        urls = [b.url for b in buttons if b.url]
        assert any("dexscreener.com/solana/" in u for u in urls)
        assert any("dextools.io/app/en/sol/pair-explorer/" in u for u in urls)
        # GMGN still present via DEX_REF_LINKS (no duplicate added by us).
        assert any("gmgn.ai" in u for u in urls)

    def test_empty_chain_adds_no_chart_buttons(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_refs(monkeypatch)
        buttons = fb.dex_buttons(ADDR, "")
        assert not any(u and ("dexscreener.com" in u or "dextools.io" in u) for u in (b.url for b in buttons))
