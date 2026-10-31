"""Purpose-based content routing — the ONLY place a kind meets a channel profile.

Two pure decisions:

``route_content(item_kind, channel_profiles)``
    which channels are chartered for an item kind. **Default-deny**: an unknown or
    empty kind returns ``()``; a channel must explicitly list the kind in its profile.

``filter_plan_by_profile(plan, profiles)``
    the additive filter the alert transport applies to the plan the tier router
    already produced. It never adds a channel, never reorders one and never
    re-implements severity logic — tier routing and purpose routing are AND-ed,
    so the dispatcher is not forked.

Why a filter and not a second router: the tier router (``publisher.route_alerts``)
answers "how important is this?", the profile answers "does this channel want this
at all?". A critical whale move is important AND belongs on @rmialpha, not on
@rmialerts. Either rule alone posts to the wrong channel.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from app.core.channel_profiles import ChannelProfile, kind_for_alert, load_profiles

#: Logical transport channel → profile role (``app.core.channels_registry``).
LOGICAL_PROFILE_CHANNELS: dict[str, str] = {
    "alerts_channel": "alerts",
    "rmialerts": "alerts",
    "rmialpha": "alpha",
    "rmicryptonews": "news",
    "main_channel": "main",
    "rmiscans": "scans",
    "rmiupdates": "updates",
}

#: Drop reasons emitted by the filter (also used in logs/metrics).
DENY_UNPROFILED = "channel_unprofiled"
DENY_KIND_UNMAPPED = "kind_unmapped"
DENY_KIND_NOT_IN_PROFILE = "kind_not_in_profile"


def _kinds_of(profile: object) -> frozenset[str]:
    """Kinds for a profile row — accepts a ChannelProfile or a raw YAML mapping."""
    if isinstance(profile, ChannelProfile):
        return profile.kinds
    if isinstance(profile, Mapping):
        raw = profile.get("kinds") or ()
        return frozenset(str(k).strip().lower() for k in raw)
    return frozenset(getattr(profile, "kinds", ()) or ())


def route_content(
    item_kind: str,
    channel_profiles: Mapping[str, object],
) -> tuple[str, ...]:
    """Return the channels whose profile lists *item_kind*, sorted, default-deny.

    Pure: no I/O, no globals, no ordering surprises. ``"SCANS"`` and ``" scans "``
    are the same kind. An empty/unknown kind returns ``()`` — a caller that cannot
    name its content kind has no route, which is the safe failure for a public channel.
    """
    kind = str(item_kind or "").strip().lower()
    if not kind:
        return ()
    channels = [
        str(name).strip().lower()
        for name, profile in channel_profiles.items()
        if profile is not None and kind in _kinds_of(profile)
    ]
    return tuple(sorted(channels))


def _deny(channel: str, category: str, kind: str | None, reason: str) -> dict[str, str]:
    return {"channel": channel, "category": category, "kind": kind or "", "reason": reason}


def filter_plan_by_profile(
    plan: Sequence[Mapping[str, object]],
    profiles: Mapping[str, object] | None = None,
    *,
    kind_resolver: Callable[[str, str], str | None] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """AND the purpose profiles into an existing ``route_alerts`` plan.

    Each plan entry is ``{"channel": ..., "alerts": [alert dicts]}``. An alert stays
    in an entry only when its kind is listed in that channel's profile. Channels with
    no profile mapping (``site`` — not a chat) pass through untouched.

    Returns ``(new_plan, denied)``; ``denied`` carries one row per suppressed alert
    with the reason, so an audit can always say why a post did not go out.
    """
    resolved = profiles if profiles is not None else load_profiles()
    resolve_kind = kind_resolver or kind_for_alert
    new_plan: list[dict[str, object]] = []
    denied: list[dict[str, str]] = []

    for entry in plan:
        channel = str(entry.get("channel") or "")
        profile_role = LOGICAL_PROFILE_CHANNELS.get(channel)
        alerts: Iterable[object] = entry.get("alerts") or ()  # type: ignore[assignment]
        if profile_role is None:
            new_plan.append(dict(entry))
            continue
        profile = resolved.get(profile_role)
        if profile is None:
            for alert in alerts:
                denied.append(_deny(channel, str(_field(alert, "category")), None, DENY_UNPROFILED))
            continue

        kept: list[object] = []
        for alert in alerts:
            category = str(_field(alert, "category"))
            severity = str(_field(alert, "severity"))
            kind = resolve_kind(category, severity)
            if kind is None:
                denied.append(_deny(channel, category, None, DENY_KIND_UNMAPPED))
                continue
            if kind not in _kinds_of(profile):
                denied.append(_deny(channel, category, kind, DENY_KIND_NOT_IN_PROFILE))
                continue
            kept.append(alert)
        if kept:
            new_plan.append({**entry, "alerts": kept})

    return new_plan, denied


def _field(alert: object, name: str) -> object:
    """Read *name* from a dict-shaped alert, a pydantic model or anything with attrs."""
    if isinstance(alert, Mapping):
        return alert.get(name, "")
    return getattr(alert, name, "")


__all__ = [
    "DENY_KIND_NOT_IN_PROFILE",
    "DENY_KIND_UNMAPPED",
    "DENY_UNPROFILED",
    "LOGICAL_PROFILE_CHANNELS",
    "filter_plan_by_profile",
    "route_content",
]
