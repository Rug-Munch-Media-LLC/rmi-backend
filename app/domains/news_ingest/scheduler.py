"""Scheduling stub: when to run channel sweeps each day.

Stub for wiring the ingester into a scheduler/cron later. ``daily_sweep_times``
is the default clock; ``next_sweep_times`` is a pure, deterministic helper
that computes the upcoming sweep datetimes for a given timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Final
from zoneinfo import ZoneInfo

# Default sweep clock (UTC hours). A few times per day keeps Telegram rate
# limits low while still catching breaking news.
daily_sweep_times: Final[list[int]] = [9, 13, 17, 21]
DEFAULT_SWEEPS_PER_DAY: Final[int] = len(daily_sweep_times)
DEFAULT_TZ: Final[tzinfo] = ZoneInfo("UTC")


def sweep_schedule(n_per_day: int) -> list[time]:
    """Evenly spaced times of day for *n_per_day* sweeps (0-23h, no tz).

    Examples: ``n=4`` → [00:00, 06:00, 12:00, 18:00]; ``n=1`` → [00:00].
    Clamped to at most 24 entries; non-positive input → empty list.
    """
    if n_per_day <= 0:
        return []
    n = min(n_per_day, 24)
    spacing = 24 / n
    return [
        time(
            hour=int(i * spacing),
            minute=int((i * spacing % 1) * 60),
        )
        for i in range(n)
    ]


def next_sweep_times(n_per_day: int, tz: tzinfo, *, now: datetime | None = None) -> list[datetime]:
    """Next *n_per_day* sweep datetimes (aware, in *tz*) strictly after *now*.

    Deterministic: identical inputs produce identical output. When *now* is
    omitted the current UTC time is used. Naive *now* values are assumed UTC.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(tz)

    schedule = sweep_schedule(n_per_day)
    if not schedule:
        return []

    results: list[datetime] = []
    day = now.date()
    while len(results) < len(schedule):
        for scheduled in schedule:
            candidate = datetime.combine(day, scheduled, tzinfo=tz)
            if candidate > now:
                results.append(candidate)
                if len(results) == len(schedule):
                    break
        day += timedelta(days=1)
    return results
