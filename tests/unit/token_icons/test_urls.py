"""URL acceptance gate — only https raster images pass."""

from __future__ import annotations

import pytest

from app.token_icons.urls import acceptable_icon_url


@pytest.mark.parametrize(
    "url",
    [
        "https://dd.dexscreener.com/ds-data/tokens/solana/abc.png",
        "https://assets.coingecko.com/coins/images/1/large/bitcoin.png?1696501400",
        "https://gmgn.ai/external-res/xyz.webp",
    ],
)
def test_accepts_https_images(url: str) -> None:
    assert acceptable_icon_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://example.com/logo.png",
        "data:image/png;base64,AAAA",
        "javascript:alert(1)",
        "https://example.com/logo.svg",
        "https://example.com/logo.SVG",
        "https://localhost/logo.png",
        "https://127.0.0.1/logo.png",
        "https://example.com/lo go.png",
        'https://example.com/x.png"><script>',
        "/relative/logo.png",
    ],
)
def test_rejects_unsafe_or_relative(url) -> None:
    assert acceptable_icon_url(url) is False
