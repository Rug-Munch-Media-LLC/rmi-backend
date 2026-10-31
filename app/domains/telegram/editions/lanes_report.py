"""Value-free reporting for the delivery lanes (docs, logs, dry run).

Nothing here returns a chat id: rows carry the *source* of a resolution
(``env``/``gopass``/``fallback``/``none``), never the id itself, so the table is
safe to print in CI output, an incident note or a release artifact.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.core.channels_registry import CHANNEL_ROLES, resolve_channel_id
from app.domains.telegram.editions.lanes import (
    LANE_BINDINGS,
    LANES,
    WHALES_ENV_VAR,
    lane_live,
    parse_chat_id,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def lane_source(
    lane: str,
    *,
    env: Mapping[str, str] | None = None,
    use_gopass: bool = False,
) -> str:
    """Where this lane would resolve from: env, gopass, fallback or none.

    Value-free by construction — it reports the *source*, never the id — so a
    dry run can show the wiring without printing channel identifiers.
    """
    binding = LANE_BINDINGS.get(lane)
    if binding is None or not binding.telegram or binding.role is None:
        return "none"
    source = os.environ if env is None else env
    if lane == "whales" and parse_chat_id(source.get(WHALES_ENV_VAR)) is not None:
        return "env"
    definition = CHANNEL_ROLES.get(binding.role)
    if definition is None:
        return "none"
    for key in (definition.env_var, *definition.legacy_env_vars):
        if source.get(key):
            return "env"
    if use_gopass and resolve_channel_id(
        binding.role, env=source, use_gopass=True, include_fallback=False
    ):
        return "gopass"
    return "fallback" if definition.fallback else "none"


def lane_inventory(env: Mapping[str, str] | None = None) -> tuple[dict[str, object], ...]:
    """Value-free lane table for docs, logs and the dry-run preview.

    Never returns a chat id — only names, handles and booleans — so it is safe
    to print in CI output and in a report.
    """
    source = os.environ if env is None else env
    rows: list[dict[str, object]] = []
    for lane in LANES:
        binding = LANE_BINDINGS[lane]
        definition = CHANNEL_ROLES.get(binding.role) if binding.role else None
        rows.append(
            {
                "lane": lane,
                "env_var": binding.env_var,
                "role": binding.role,
                "public_handle": binding.public_handle,
                "telegram": binding.telegram,
                "fallback": "documented" if definition and definition.fallback else "none",
                "source": lane_source(lane, env=source, use_gopass=False),
                "live": lane_live(lane, source),
                "note": binding.note,
            }
        )
    return tuple(rows)


__all__ = ["lane_inventory", "lane_source"]
