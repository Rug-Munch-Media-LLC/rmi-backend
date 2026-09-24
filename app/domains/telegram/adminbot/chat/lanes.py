"""Model lanes the picker can offer, with their live endpoints."""

from __future__ import annotations

from typing import Final

LANES: Final[tuple[dict[str, str], ...]] = (
    {"id": "local-workhorse", "label": "Qwen3.6-35B (local :8009)", "url": "http://127.0.0.1:8009"},
    {"id": "local-fast", "label": "Qwythos-9B (local :8009 fast)", "url": "http://127.0.0.1:8009"},
    {"id": "zerogpu", "label": "ZeroGPU fleet (:20201)", "url": "http://127.0.0.1:20201"},
    {"id": "litellm", "label": "LiteLLM router (:4000)", "url": "http://127.0.0.1:4000"},
    {"id": "opencode", "label": "opencode", "url": ""},
    {"id": "kilocode", "label": "KiloCode", "url": ""},
    {"id": "openhands", "label": "OpenHands", "url": ""},
    {"id": "hermes", "label": "Hermes", "url": ""},
)


def lane_ids() -> tuple[str, ...]:
    """Return every selectable lane id."""
    return tuple(lane["id"] for lane in LANES)
