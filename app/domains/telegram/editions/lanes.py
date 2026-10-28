"""Telegram delivery lanes — every chat id comes from config, never code.

Six logical lanes can be addressed by a routing decision:

=========  ==================  ===============================  =====================
Lane       Public handle       Env var                          Notes
=========  ==================  ===============================  =====================
main       @cryptorugmuncher   RMI_MAIN_CHANNEL_ID              curated + escalations
alerts     @rmialerts          RMI_ALERTS_CHANNEL_ID            high+ threats
news       @rmicryptonews      RMI_NEWS_CHANNEL_ID              news + digests
alpha      @rmialpha           RMI_ALPHA_CHANNEL_ID             early signal
whales     @rmialpha           RMI_WHALES_CHANNEL_ID            opt-in; falls back to alpha
site       —                   (none)                           no Telegram surface
=========  ==================  ===============================  =====================

Resolution is delegated to :mod:`app.core.channels_registry` (gopass → env →
documented fallback), so no numeric chat id is duplicated in this package.
``RMI_WHALES_CHANNEL_ID`` is the single lane-local override: the registry has no
dedicated whales role and its ``alpha`` role is documented as "Whale & Alpha
Signals", so an operator can split whales out without a registry change.

Every lane is additionally DRY unless its own gate says otherwise
(:mod:`app.core.channel_gate`: ``RMI_CHANNEL_DRY_RUN_<ROLE>=0``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.channel_gate import is_live
from app.core.channels_registry import CHANNEL_ROLES, resolve_channel_id

if TYPE_CHECKING:
    from collections.abc import Mapping

LANES: tuple[str, ...] = ("main", "alerts", "news", "alpha", "whales", "site")

WHALES_ENV_VAR = "RMI_WHALES_CHANNEL_ID"

_INT_RE = re.compile(r"^-?\d+$")


@dataclass(frozen=True, slots=True)
class LaneBinding:
    """How one logical lane resolves, logs and gates. Contains no chat id."""

    lane: str
    role: str | None
    env_var: str | None
    public_handle: str | None
    telegram: bool
    note: str = ""

    @property
    def gate_role(self) -> str:
        """Gate variable for this lane (``RMI_CHANNEL_DRY_RUN_<ROLE>``)."""
        return self.lane


def _binding(lane: str, role: str | None, *, env_var: str | None = None, telegram: bool = True, note: str = "") -> LaneBinding:
    definition = CHANNEL_ROLES.get(role) if role else None
    return LaneBinding(
        lane=lane,
        role=role,
        env_var=env_var or (definition.env_var if definition else None),
        public_handle=definition.public_handle if definition else None,
        telegram=telegram,
        note=note,
    )


LANE_BINDINGS: dict[str, LaneBinding] = {
    "main": _binding("main", "main", note="top curated stories + high-importance escalations"),
    "alerts": _binding("alerts", "alerts", note="rug pulls, exploits, high-severity threats"),
    "news": _binding("news", "news", note="news-worthy items + digests"),
    "alpha": _binding("alpha", "alpha", note="highest-signal early data (launch/syndicate/whale)"),
    "whales": _binding(
        "whales",
        "alpha",
        env_var=WHALES_ENV_VAR,
        note="dedicated whales channel when RMI_WHALES_CHANNEL_ID is set, else the alpha lane",
    ),
    "site": _binding("site", None, telegram=False, note="no Telegram surface — link lives in the post"),
}


def parse_chat_id(raw: str | None) -> int | str | None:
    """Parse an env value into a chat id: int when numeric, else a @-handle."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if _INT_RE.fullmatch(value):
        try:
            return int(value)
        except ValueError:
            return None
    return value


def lane_chat_id(
    lane: str,
    *,
    env: Mapping[str, str] | None = None,
    use_gopass: bool = True,
) -> int | str | None:
    """Resolve one lane to a Telegram chat id (None = not deliverable).

    site never resolves: it has no Telegram surface, the canonical link is
    part of every post. whales prefers RMI_WHALES_CHANNEL_ID and falls
    back to the alpha role, so the lane is usable before a split channel exists.
    """
    binding = LANE_BINDINGS.get(lane)
    if binding is None or not binding.telegram:
        return None
    source = os.environ if env is None else env
    if lane == "whales":
        override = parse_chat_id(source.get(WHALES_ENV_VAR))
        if override is not None:
            return override
    if binding.role is None:
        return None
    resolved = resolve_channel_id(
        binding.role, env=source, use_gopass=use_gopass, include_fallback=True
    )
    return parse_chat_id(resolved)


def lane_live(lane: str, env: Mapping[str, str] | None = None) -> bool:
    """True only when this lane is explicitly enabled and not force-dried."""
    binding = LANE_BINDINGS.get(lane)
    if binding is None or not binding.telegram:
        return False
    return is_live(binding.gate_role, env)


__all__ = [
    "LANES",
    "LANE_BINDINGS",
    "WHALES_ENV_VAR",
    "LaneBinding",
    "lane_chat_id",
    "lane_live",
    "parse_chat_id",
]
