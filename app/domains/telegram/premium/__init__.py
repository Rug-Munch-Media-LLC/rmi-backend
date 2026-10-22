"""Premium scan cards: three tiers, one renderer, a thumbnail and a keyboard.

Public surface — everything else in this package is an implementation detail:

* :func:`render_card`      — ``free`` | ``pro`` | ``alert`` within 4,096 chars
* :func:`extract`          — scanner payload -> :class:`CardData`
* :func:`render_thumb`     — 960x320 PNG for the photo slot
* :func:`scan_keyboard`    — ``Full scan | Watch | Chart``
* :data:`TIERS`            — the tier contract, for docs and tests
"""

from __future__ import annotations

from app.domains.telegram.premium.delta import render_alert
from app.domains.telegram.premium.extract import extract
from app.domains.telegram.premium.keyboard import scan_keyboard
from app.domains.telegram.premium.model import CardData, Cohort, RenderedCard, ThumbSpec
from app.domains.telegram.premium.render import MAX_CHARS, render_card
from app.domains.telegram.premium.thumb import render_thumb
from app.domains.telegram.premium.tiers import TIERS, Tier

__all__ = [
    "MAX_CHARS",
    "TIERS",
    "CardData",
    "Cohort",
    "RenderedCard",
    "ThumbSpec",
    "Tier",
    "extract",
    "render_alert",
    "render_card",
    "render_thumb",
    "scan_keyboard",
]
