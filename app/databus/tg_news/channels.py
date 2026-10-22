"""Seed channel set for the public-preview ingester.

``kind_hint`` encodes what the probe of 2026-10-23 measured (see
``verified_channels``): a desk that republishes other channels is an
*aggregator*, a desk that links the primary source is *primary*, a security desk
is *security*. ``weight`` is the curated source weight for that handle; when set
it wins over the rubric score, because a measured squat or a dormant desk must
not out-rank a live one on rubric luck.

Seeds are merged with enabled ``news`` entries from the canonical registry
(``app/databus/tg_channels.json``); explicit ``--channels`` overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.databus import tg_channel_registry as registry
from app.databus.tg_news.verified_channels import IMPOSTOR, PREVIEW_OFF, ROLES, SWEPT, WEIGHTS

logger = get_logger("tg_news")


@dataclass(frozen=True, slots=True)
class SeedChannel:
    username: str
    kind_hint: str
    weight: float | None = None


SEED_CHANNELS: tuple[SeedChannel, ...] = (
    SeedChannel("news_crypto", "aggregator"),
    SeedChannel("Fin_Watch", "primary"),
    SeedChannel("ETF_FLOWS", "primary"),
    SeedChannel("BTC_Tick", "primary"),
    SeedChannel("Bitcoin_FGI", "primary"),
)

_KINDS = {seed.username.lower(): seed.kind_hint for seed in SEED_CHANNELS}
_KINDS.update(ROLES)


def channel_list(explicit: list[str] | None = None) -> list[SeedChannel]:
    """Explicit handles, else seeds + measured channels + registry news channels."""
    if explicit:
        names = [name.lstrip("@") for name in explicit]
        return [
            SeedChannel(name, _KINDS.get(name.lower(), "unknown"), WEIGHTS.get(name.lower()))
            for name in names
        ]
    result = list(SEED_CHANNELS) + [
        SeedChannel(channel.username, channel.role, channel.weight) for channel in SWEPT
    ]
    seen = {seed.username.lower() for seed in result}
    try:
        extra = [c for c in registry.enabled_channels() if str(c.get("kind")) == "news"]
    except Exception as exc:  # registry is optional; seeds alone are enough
        logger.debug("tg_channel registry unavailable: %s", exc)
        return result
    for entry in extra:
        handle = str(entry.get("username", "")).lstrip("@")
        lowered = handle.lower()
        if (
            handle
            and lowered not in seen
            and lowered not in PREVIEW_OFF
            and lowered not in IMPOSTOR
        ):
            result.append(SeedChannel(handle, "unknown", WEIGHTS.get(lowered)))
            seen.add(lowered)
    return result
