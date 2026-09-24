"""Report which model lanes are actually answering."""

from __future__ import annotations

import urllib.error
import urllib.request

from .lanes import LANES

TIMEOUT = 4


def discover() -> list[dict[str, object]]:
    """Return each lane with an `up` flag for the menu.

    A lane that is paused, down, or needs auth still counts as selectable for
    the CLI engines (opencode, kilo, openhands, hermes) and shows as offline for
    the HTTP ones, so the menu reports state instead of hiding options.
    """

    def up(url: str) -> bool:
        if not url:
            return True
        try:
            with urllib.request.urlopen(f"{url}/v1/models", timeout=TIMEOUT) as response:  # noqa: S310 - fixed http loopback lanes
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    return [{"id": lane["id"], "label": lane["label"], "up": up(lane["url"])} for lane in LANES]
