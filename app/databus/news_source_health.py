"""Source-health module + pruning utility for the unified news source list.

Classifies every source in ``unified_news_sources.json`` using the per-source
status map written by ``scripts/test_news_sources.py`` (``news_sources_status.json``)
and produces a safe pruning plan.

Design rules:

- **Pure helpers** (``classify_sources``, ``prune_plan``) never touch disk.
- **Fail-open**: anything that cannot be classified (missing from the status map,
  unreadable status file) is treated as *keep*. We never drop a source we don't
  have evidence about.
- **Floor**: pruning never removes so many sources that fewer than a floor remain.
  The floor is 20 by default and can be raised with ``min_verified``.
- ``apply_prune`` is the only writer; it rewrites ``unified_news_sources.json``
  only when ``dry_run=False``, and reconciles ``categories`` + ``total_sources``
  + ``updated`` so no metadata references pruned ids.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

SOURCES_FILE = Path(__file__).resolve().parent / "unified_news_sources.json"
STATUS_FILE = Path(__file__).resolve().parent / "news_sources_status.json"

DEFAULT_FLOOR = 20

# Status strings produced by scripts/test_news_sources.py
VERIFIED = "VERIFIED"
STALE = "STALE"
DEAD = "DEAD"

_BUCKET_BY_STATUS = {
    VERIFIED: "active",
    STALE: "stale",
    DEAD: "dead",
}


def load_status(path: str | Path | None = None) -> dict[str, Any]:
    """Load the per-source status map, failing open to ``{}``.

    ``path`` defaults to ``news_sources_status.json`` next to this module.
    Any read/decode error returns ``{}`` so callers can keep all sources.
    """
    status_path = Path(path) if path is not None else STATUS_FILE
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    sources = payload.get("sources")
    return sources if isinstance(sources, dict) else {}


def classify_sources(sources: list[dict[str, Any]], status: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Bucket every source into ``active`` / ``stale`` / ``dead`` / ``unknown``.

    Buckets are lists of the original source dicts, preserving input order.
    ``unknown`` holds sources with no entry in the status map (fail-open keep).
    """
    buckets: dict[str, list[dict[str, Any]]] = {"active": [], "stale": [], "dead": [], "unknown": []}
    for source in sources:
        entry = status.get(source.get("id"))
        bucket = _BUCKET_BY_STATUS.get(entry.get("status")) if isinstance(entry, dict) else None
        buckets[bucket or "unknown"].append(source)
    return buckets


def prune_plan(
    sources: list[dict[str, Any]],
    status: dict[str, Any],
    *,
    remove_dead: bool = True,
    keep_stale: bool = True,
    min_verified: int | None = None,
) -> dict[str, Any]:
    """Build a ``{keep, remove, counts}`` pruning plan.

    - ``remove_dead=True`` drops every DEAD source.
    - ``keep_stale=True`` (default) retains STALE sources; ``False`` drops them.
    - Unknown sources are always kept (fail-open).
    - ``min_verified`` raises the keep-floor above the default of 20 sources:
      if removing sources would leave fewer than ``floor = max(20, min_verified)``
      sources, earlier-removed sources are restored (in original order) until the
      floor is reached. Never removes below the floor, and never removes more than
      ``len(sources) - floor``.
    """
    buckets = classify_sources(sources, status)

    keep: list[dict[str, Any]] = []
    keep.extend(buckets["active"])
    if keep_stale:
        keep.extend(buckets["stale"])
    keep.extend(buckets["unknown"])
    if not remove_dead:
        keep.extend(buckets["dead"])

    remove: list[dict[str, Any]] = []
    if remove_dead:
        remove.extend(buckets["dead"])
    if not keep_stale:
        remove.extend(buckets["stale"])

    floor = max(DEFAULT_FLOOR, min_verified) if min_verified is not None else DEFAULT_FLOOR
    if len(keep) < floor and remove:
        need = min(floor - len(keep), len(remove))
        keep.extend(remove[:need])
        remove = remove[need:]

    return {
        "keep": keep,
        "remove": remove,
        "counts": {
            "total": len(sources),
            "active": len(buckets["active"]),
            "stale": len(buckets["stale"]),
            "dead": len(buckets["dead"]),
            "unknown": len(buckets["unknown"]),
            "kept": len(keep),
            "removed": len(remove),
        },
    }


def _validate_plan(plan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(keep, remove)`` or raise if the plan is malformed."""
    keep = plan.get("keep")
    remove = plan.get("remove")
    if not isinstance(keep, list) or not isinstance(remove, list):
        raise ValueError("plan must contain 'keep' and 'remove' lists")
    return keep, remove


def apply_prune(plan: dict[str, Any], path: str | Path | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    """Apply a ``prune_plan`` to ``unified_news_sources.json``.

    Only writes when ``dry_run=False`` (atomic replace). Keeps all top-level
    metadata, replaces ``sources`` with ``plan["keep"]``, reconciles ``categories``
    so no pruned id is referenced, and updates ``total_sources`` + ``updated``.

    Returns ``{"removed", "kept", "wrote"}`` where ``removed``/``kept`` are counts.
    """
    keep, remove = _validate_plan(plan)
    result = {"removed": len(remove), "kept": len(keep), "wrote": False}
    if dry_run:
        return result

    sources_path = Path(path) if path is not None else SOURCES_FILE
    try:
        data = json.loads(sources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read source list at {sources_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"source list at {sources_path} is not a JSON object")

    kept_ids = {source.get("id") for source in keep if isinstance(source, dict) and source.get("id")}
    categories = data.get("categories")
    if isinstance(categories, dict):
        data["categories"] = {
            category: [source_id for source_id in ids if source_id in kept_ids]
            for category, ids in categories.items()
            if isinstance(ids, list)
        }

    data["sources"] = keep
    data["total_sources"] = len(keep)
    data["updated"] = datetime.now().date().isoformat()

    fd, tmp_name = tempfile.mkstemp(dir=str(sources_path.parent), prefix=".news_sources.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_name, sources_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    result["wrote"] = True
    return result


def health_report(status: dict[str, Any]) -> str:
    """Human-readable summary of the status map: counts + worst offenders.

    Accepts either the full status payload (has a ``sources`` mapping) or the
    id -> entry map returned by :func:`load_status`.
    """
    sources = status.get("sources") if isinstance(status, dict) else None
    if not isinstance(sources, dict):
        sources = status if isinstance(status, dict) else None
    if not isinstance(sources, dict) or not sources:
        return "No source status available (status map missing or empty)."

    by_status: dict[str, list[dict[str, Any]]] = {}
    for source_id, entry in sources.items():
        if not isinstance(entry, dict):
            continue
        by_status.setdefault(entry.get("status", "UNKNOWN"), []).append(
            {"id": source_id, **{k: v for k, v in entry.items() if k != "status"}}
        )

    order = ("VERIFIED", "STALE", "DEAD")
    lines = [
        "News source health report",
        "========================",
        f"Sources tracked: {len(sources)}",
    ]
    for status_name in order:
        bucket = by_status.get(status_name, [])
        lines.append(f"- {status_name}: {len(bucket)}")

    other = [k for k in by_status if k not in order]
    if other:
        lines.append(f"- other: {sum(len(by_status[k]) for k in other)}")

    stale = sorted(by_status.get(STALE, []), key=lambda s: s.get("last_good") or "")
    if stale:
        lines.append("")
        lines.append("Oldest STALE sources:")
        for entry in stale[:5]:
            lines.append(
                f"- {entry['id']}: last good {entry.get('last_good') or 'never'}, {entry.get('item_count', 0)} items"
            )

    dead = by_status.get(DEAD, [])
    if dead:
        lines.append("")
        lines.append(f"Worst DEAD offenders (top {min(10, len(dead))} by error):")
        for entry in sorted(dead, key=lambda s: s.get("error") or "")[:10]:
            lines.append(f"- {entry['id']}: {entry.get('error') or 'no error recorded'}")

    return "\n".join(lines)
