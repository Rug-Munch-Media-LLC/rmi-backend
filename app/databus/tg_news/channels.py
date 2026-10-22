"""Seed channel set for the public-preview ingester.

``kind_hint`` encodes what we verified by hand on 2026-10-23: ``news_crypto``
republishes other channels' posts (forward-heavy, no primary links) so it is an
*aggregator*; the upstream handles are treated as *primary* until measured.

These seeds are merged with enabled ``news`` entries from the canonical
registry (``app/databus/tg_channels.json``); explicit ``--channels`` overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.databus import tg_channel_registry as registry

logger = get_logger("tg_news")


@dataclass(frozen=True, slots=True)
class SeedChannel:
    username: str
    kind_hint: str


SEED_CHANNELS: tuple[SeedChannel, ...] = (
    SeedChannel("news_crypto", "aggregator"),
    SeedChannel("Fin_Watch", "primary"),
    SeedChannel("ETF_FLOWS", "primary"),
    SeedChannel("BTC_Tick", "primary"),
    SeedChannel("Bitcoin_FGI", "primary"),
)

_KINDS = {seed.username.lower(): seed.kind_hint for seed in SEED_CHANNELS}


def channel_list(explicit: list[str] | None = None) -> list[SeedChannel]:
    """Explicit handles, else seeds + enabled registry news channels (deduped)."""
    if explicit:
        return [SeedChannel(name.lstrip("@"), _KINDS.get(name.lstrip("@").lower(), "unknown")) for name in explicit]
    result = list(SEED_CHANNELS)
    seen = {seed.username.lower() for seed in SEED_CHANNELS}
    try:
        extra = [c for c in registry.enabled_channels() if str(c.get("kind")) == "news"]
    except Exception as exc:  # registry is optional; seeds alone are enough
        logger.debug("tg_channel registry unavailable: %s", exc)
        return result
    for entry in extra:
        handle = str(entry.get("username", "")).lstrip("@")
        if handle and handle.lower() not in seen:
            result.append(SeedChannel(handle, "unknown"))
            seen.add(handle.lower())
    return result
