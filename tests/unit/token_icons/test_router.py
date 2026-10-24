"""Router + fallback PNG contract (no app import required)."""

from __future__ import annotations

import re

import pytest

from app.token_icons.fallback import fallback_icon_url
from app.token_icons.palette import brand_rgb
from app.token_icons.png import solid_png
from app.token_icons.router import fallback_png


def test_fallback_png_is_valid_png() -> None:
    response = fallback_png("solana", "So11111111111111111111111111111111111111112")
    assert response.media_type == "image/png"
    assert response.body.startswith(b"\x89PNG\r\n\x1a\n")
    assert response.headers["cache-control"].startswith("public, max-age=")


def test_fallback_colour_is_deterministic() -> None:
    assert brand_rgb("base", "0xabc") == brand_rgb("base", "0xabc")
    assert brand_rgb("base", "0xabc") in brand_rgb.__globals__["BRAND_PALETTE"]


def test_fallback_url_shape() -> None:
    url = fallback_icon_url("Base", "0xABC")
    assert url == "/api/v1/tokens/icon-fallback/base/0xABC.png"
    assert fallback_icon_url("", "") == "/api/v1/tokens/icon-fallback/unknown/unknown.png"


@pytest.mark.parametrize("size", [1, 16, 64])
def test_solid_png_size_param(size: int) -> None:
    data = solid_png((255, 0, 0), size=size)
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert (width, height) == (size, size)
    assert re.match(rb"^\x89PNG", data)
