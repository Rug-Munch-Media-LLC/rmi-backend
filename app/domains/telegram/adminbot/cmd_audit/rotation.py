"""Weekly rotation for the command audit log (keep the last 4 weeks)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

KEEP_WEEKS = 4

if TYPE_CHECKING:
    from pathlib import Path


def week_key(ts: float) -> str:
    """ISO week label for a timestamp, e.g. ``2026-W43``."""
    return time.strftime("%G-W%V", time.localtime(ts))


def prune(parent: Path, prefix: str, suffix: str) -> None:
    """Delete archives beyond the newest ``KEEP_WEEKS``."""
    archives = sorted(p for p in parent.glob(f"{prefix}*{suffix}") if p.is_file())
    for stale in archives[:-KEEP_WEEKS]:
        try:
            stale.unlink()
        except OSError:
            continue


def rotate_if_needed(path: Path, now: float | None = None) -> None:
    """Archive last week's file under its ISO week and prune old archives."""
    now_ts = time.time() if now is None else now
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    key = week_key(mtime)
    if key == week_key(now_ts):
        return
    archived = path.with_name(f"{path.stem}-{key}{path.suffix}")
    try:
        os.replace(path, archived)
    except OSError:
        return
    prune(path.parent, f"{path.stem}-", path.suffix)
