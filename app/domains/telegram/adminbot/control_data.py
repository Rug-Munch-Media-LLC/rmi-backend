"""Read-only data-plane probes for /dataops.

Qdrant collection counts, ClickHouse largest tables, and backup freshness.
Nothing here mutates state or shells out through a shell — triggers stay in
``control_actions``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import escape

from app.domains.telegram.adminbot.admin_ops import _run_subprocess

QDRANT_URL = "http://127.0.0.1:6334"
CH_QUERY = (
    "SELECT database || '.' || name, total_rows FROM system.tables "
    "WHERE database NOT IN ('system','INFORMATION_SCHEMA','information_schema') "
    "AND total_rows > 0 AND name NOT LIKE '.inner%' ORDER BY total_rows DESC LIMIT 6 FORMAT TSV"
)


def _qdrant() -> tuple[int, int, list[tuple[str, int]]]:
    """(collections, total points, top 6) — local HTTP fan-out, never raises."""
    try:
        with urllib.request.urlopen(QDRANT_URL + "/collections", timeout=6) as resp:  # noqa: S310
            names = [c["name"] for c in json.load(resp)["result"]["collections"]]
    except Exception:
        return 0, 0, []

    def count(name: str) -> tuple[str, int]:
        try:
            with urllib.request.urlopen(f"{QDRANT_URL}/collections/{name}", timeout=4) as resp:  # noqa: S310
                return name, int(json.load(resp)["result"].get("points_count") or 0)
        except Exception:
            return name, -1

    with ThreadPoolExecutor(max_workers=10) as pool:
        live = [(n, c) for n, c in pool.map(count, names) if c >= 0]
    return len(names), sum(c for _n, c in live), sorted(live, key=lambda x: -x[1])[:6]


def _newest(root: str) -> tuple[float, str]:
    """Newest file (mtime, path) within two levels of ``root``; (0, '') if none."""
    best: tuple[float, str] = (0.0, "")
    for base, _dirs, files in os.walk(root):
        if base.count(os.sep) - root.count(os.sep) >= 2:
            continue
        for name in files:
            path = os.path.join(base, name)
            try:
                best = max(best, (os.path.getmtime(path), path))
            except OSError:
                continue
    return best


def _backup_dirs(root: str) -> list[tuple[str, float]]:
    """(artifact, age hours) per top-level backup dir, freshest first."""
    rows: list[tuple[str, float]] = []
    try:
        entries = [e for e in os.scandir(root) if e.is_dir() and not e.name.startswith(".")]
    except OSError:
        return rows
    for entry in entries:
        mtime, _path = _newest(entry.path)
        if mtime:
            rows.append((f"{entry.name}", (time.time() - mtime) / 3600))
    return sorted(rows, key=lambda r: r[1])


def render_dataops() -> str:
    """One data-plane screen: Qdrant, ClickHouse, backup freshness."""
    collections, points, top = _qdrant()
    out = [f"🧠 <b>Qdrant</b> {collections} collections · {points:,} points"]
    out += [f"   {escape(n)}  {c:,}" for n, c in top]
    ch = [
        ln.split("\t")
        for ln in (
            _run_subprocess(["docker", "exec", "rmi-clickhouse", "clickhouse-client", "-q", CH_QUERY], 25).get("output")
            or ""
        ).splitlines()
        if "\t" in ln
    ]
    out.append(f"\n📊 <b>ClickHouse</b> {len(ch)} largest tables")
    out += [f"   {escape(t)}  {int(r):,}" if r.isdigit() else f"   {escape(t)}" for t, r in ch]
    out.append("\n💾 <b>Backups</b>")
    for root in ("/backups", "/srv/backups"):
        entries = _backup_dirs(root)
        if not entries:
            out.append(f"   ⚠️ {escape(root)}: empty/absent")
        for name, age in entries[:4]:
            out.append(f"   {escape(root + '/' + name)}: {age:.0f}h ago{' ⚠️ stale >48h' if age > 48 else ''}")
    return "\n".join(out)


def latest_backup_age_hours(root: str) -> float:
    """Age of the newest artifact under ``root`` in hours; inf when absent."""
    mtime, _path = _newest(root)
    return (time.time() - mtime) / 3600 if mtime else float("inf")
