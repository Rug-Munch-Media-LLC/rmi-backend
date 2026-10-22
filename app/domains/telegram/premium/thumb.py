"""Per-card thumbnail: risk field, ticker, three metrics — no clip art.

The thumbnail is the part of the card that survives a notification drawer, so it
carries the same three facts as the text card (liquidity, top-10, safety) and the
same severity colour as the header bar. Rendered with Pillow at 960x320, the
size Telegram crops least on mobile.

Nothing here is decorative: the block bar repeats the severity rank, the accent
rule is the only colour, and the metrics are the card's own ``—``-aware
formatters so the image can never disagree with the text.
"""

from __future__ import annotations

import os

from app.domains.telegram.premium.model import CardData, ThumbSpec
from app.domains.telegram.premium.numbers import money, pct, short

WIDTH, HEIGHT = 960, 320
BG = (10, 13, 11)
INK = (236, 240, 236)
MUTED = (138, 148, 140)
ACCENT = {
    "CRITICAL": (214, 69, 43),
    "HIGH": (224, 138, 30),
    "MEDIUM": (150, 160, 178),
    "LOW": (46, 158, 107),
    "UNKNOWN": (107, 114, 128),
}
BAR_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
MONO = os.path.join(FONT_DIR, "DejaVuSansMono.ttf")
MONO_BOLD = os.path.join(FONT_DIR, "DejaVuSansMono-Bold.ttf")


def _font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def _right(draw, text: str, x: int, y: int, font, fill) -> None:
    """Right-align to ``x`` — metric values line up or the card looks broken."""
    draw.text((x - draw.textlength(text, font=font), y), text, font=font, fill=fill)


def _bar(draw, severity: str, accent, y: int = 42) -> None:
    """Four blocks: filled = severity rank. Readable at 40px wide in a preview."""
    for index in range(4):
        x = 44 + index * 34
        fill = accent if index < BAR_RANK.get(severity, 0) else (38, 44, 40)
        draw.rectangle((x, y, x + 26, y + 22), fill=fill)


def render_thumb(data: CardData, out_dir: str = ".") -> ThumbSpec:
    """Write the PNG and return where it landed plus its size."""
    from PIL import Image, ImageDraw

    accent = ACCENT.get(data.severity, ACCENT["UNKNOWN"])
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 6), fill=accent)
    draw.line((600, 44, 600, HEIGHT - 44), fill=(30, 36, 32), width=2)

    _bar(draw, data.severity, accent)
    draw.text((44, 84), f"${data.symbol}"[:12], font=_font(BOLD, 76), fill=INK)
    draw.text((44, 176), f"{data.chain} · {short(data.address)}", font=_font(MONO, 22), fill=MUTED)
    draw.text((44, 214), data.severity, font=_font(MONO_BOLD, 30), fill=accent)
    draw.text(
        (44, 258),
        f"SAFETY {data.safety:.0f}/100" if data.safety is not None else "SAFETY unknown",
        font=_font(MONO, 22),
        fill=MUTED,
    )

    metrics = (
        ("LIQUIDITY", money(data.liquidity_usd)),
        ("TOP-10", pct(data.top10_pct)),
        ("24H VOL", money(data.volume_24h)),
    )
    for index, (label, value) in enumerate(metrics):
        y = 52 + index * 88
        _right(draw, label, 920, y, _font(MONO, 20), MUTED)
        _right(draw, value, 920, y + 24, _font(MONO_BOLD, 40), INK)

    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(ch for ch in data.symbol.lower() if ch.isalnum() or ch in "-_") or "token"
    path = os.path.join(out_dir, f"thumb_{safe}_{data.severity.lower()}.png")
    image.save(path, "PNG", optimize=True)
    return ThumbSpec(
        path=path,
        width=WIDTH,
        height=HEIGHT,
        bytes_len=os.path.getsize(path),
        severity=data.severity,
        symbol=data.symbol,
    )
