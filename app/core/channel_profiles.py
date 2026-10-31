"""Channel profile registry — what each Telegram channel is FOR.

Loads ``channel_profiles.yaml`` (the only copy of the data) and exposes it as
frozen :class:`ChannelProfile` rows. Purposes, kinds, tone, length, cadence and
dedupe scope all come from what each channel says about itself — the YAML keeps
the live ``getChat`` description quoted verbatim next to the operator summary,
so a purpose can never drift from the channel's own words without a visible diff.

This module is data + translation only. The routing DECISION lives in
``app.core.channel_routing`` (``route_content``), so the loader can be reused by
docs, tooling and tests without dragging a policy in.

Kind vocabulary (closed set, see the YAML):
    news            breaking crypto news from the wire
    scans           scanner findings / suspicious-token cards
    alerts          security incidents: exploits, hacks, rug pulls, critical warnings
    whales          large wallet movements
    alpha           smart-money signals, launches worth front-running
    market_recaps   market commentary / daily wrap
    intel           shill campaigns, breaches, sanctions, wash-trading intel
    updates         product releases and changelogs
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

PROFILES_PATH = Path(__file__).with_name("channel_profiles.yaml")

#: Item kinds. Closed vocabulary — a kind outside this set can never route.
KINDS: frozenset[str] = frozenset(
    {"news", "scans", "alerts", "whales", "alpha", "market_recaps", "intel", "updates"}
)

#: Severities at which a ``scanner`` alert is a critical token warning (an alert)
#: rather than a teaching card (a scan). Encoded in the YAML as ``scanner: alerts``.
SCANNER_ALERT_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})


@dataclass(frozen=True, slots=True)
class ChannelProfile:
    """One channel's purpose contract, derived from its own description."""

    channel: str
    handle: str
    title: str
    telegram_description: str
    purpose: str
    kinds: frozenset[str]
    tone: str
    max_chars: int
    cadence: str
    dedupe_scope: str
    member_count: int | None = None
    bot_status: str | None = None

    def accepts(self, item_kind: str) -> bool:
        """True only when this channel's profile lists *item_kind* (default-deny)."""
        return _norm(item_kind) in self.kinds


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def _load_yaml(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"channel profiles must be a mapping: {path}")
    return data


@functools.lru_cache(maxsize=4)
def _cached(path_str: str) -> Mapping[str, object]:
    return _load_yaml(Path(path_str))


def _raw_data(path: Path | None = None) -> Mapping[str, object]:
    return _cached(str(path or PROFILES_PATH))


def load_profiles(path: Path | None = None) -> dict[str, ChannelProfile]:
    """Return ``{channel_role: ChannelProfile}`` from the YAML registry.

    Unknown kinds listed in the file are dropped (the closed vocabulary wins);
    a channel with no kinds is kept so the registry stays honest about it.
    """
    data = _raw_data(path)
    rows = data.get("channels") or {}
    if not isinstance(rows, dict):
        raise ValueError("channel_profiles.yaml: 'channels' must be a mapping")
    profiles: dict[str, ChannelProfile] = {}
    for role, row in rows.items():
        if not isinstance(row, dict):
            raise ValueError(f"channel_profiles.yaml: row {role!r} must be a mapping")
        raw_kinds = row.get("kinds") or []
        kinds = frozenset(k for k in (_norm(v) for v in raw_kinds) if k in KINDS)
        profiles[_norm(role)] = ChannelProfile(
            channel=_norm(role),
            handle=str(row.get("handle") or ""),
            title=str(row.get("title") or ""),
            telegram_description=str(row.get("telegram_description") or ""),
            purpose=" ".join(str(row.get("purpose") or "").split()),
            kinds=kinds,
            tone=" ".join(str(row.get("tone") or "").split()),
            max_chars=int(row.get("max_chars") or 0),
            cadence=" ".join(str(row.get("cadence") or "").split()),
            dedupe_scope=" ".join(str(row.get("dedupe_scope") or "").split()),
            member_count=row.get("member_count"),
            bot_status=row.get("bot_status"),
        )
    return profiles


def kind_for_source(source: str, path: Path | None = None) -> str | None:
    """Map a content producer (``scanner.finding``, ``rugbrain.exploit``, …) to a kind."""
    table = _raw_data(path).get("sources") or {}
    kind = _norm(table.get(_norm(source)))
    return kind if kind in KINDS else None


def kind_for_alert(category: str, severity: str, path: Path | None = None) -> str | None:
    """Map a legacy ``PlatformAlert`` (category, severity) to an item kind.

    ``scanner`` is severity-split: high/critical findings are critical token
    warnings (``alerts``), everything lower is a teaching card (``scans``).
    An unmapped category resolves to ``None`` — callers must deny, not guess.
    """
    cat = _norm(category)
    table = _raw_data(path).get("alert_category_kinds") or {}
    kind = _norm(table.get(cat))
    if kind not in KINDS:
        return None
    if cat == "scanner" and _norm(severity) not in SCANNER_ALERT_SEVERITIES:
        return "scans"
    return kind


__all__ = [
    "KINDS",
    "PROFILES_PATH",
    "SCANNER_ALERT_SEVERITIES",
    "ChannelProfile",
    "kind_for_alert",
    "kind_for_source",
    "load_profiles",
]
