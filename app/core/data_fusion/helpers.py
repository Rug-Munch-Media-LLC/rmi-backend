"""
Data Fusion — Extraction and Helper Functions
"""

from __future__ import annotations

from typing import Any

from app.core.data_fusion.config import IQR_MULTIPLIER, MAX_OUTLIER_PERCENTAGE


def _remove_outliers(values: list[float]) -> list[float]:
    """Remove statistical outliers using IQR method."""
    if len(values) < 4:
        return values
    sorted_v = sorted(values)
    q1 = sorted_v[len(sorted_v) // 4]
    q3 = sorted_v[(3 * len(sorted_v)) // 4]
    iqr = q3 - q1
    if iqr == 0:
        return values
    lower = q1 - IQR_MULTIPLIER * iqr
    upper = q3 + IQR_MULTIPLIER * iqr
    filtered = [v for v in values if lower <= v <= upper]
    if len(filtered) < len(values) * (1 - MAX_OUTLIER_PERCENTAGE):
        return values
    return filtered


def _get_freshness(result: dict[str, Any], source_key: str, now: float) -> float:
    """Calculate freshness score 0-1 for a result based on its source TTL."""
    from app.core.data_fusion.config import SOURCE_TTL

    ttl = SOURCE_TTL.get(source_key, 300)
    ts = result.get("_fetched_at", now)
    age = now - ts
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - (age / ttl))


def _extract_numeric(data: dict[str, Any], *keys: str) -> float | None:
    """Extract numeric value from nested dict following key path."""
    current: Any = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    if current is not None:
        try:
            return float(current)
        except (ValueError, TypeError):
            return None
    return None


def _extract_boolean(data: dict[str, Any], *keys: str) -> bool | None:
    """Extract boolean value following key path."""
    val = _extract_numeric(data, *keys)
    if val is not None:
        return bool(val)
    for key in keys:
        raw = data.get(key) if isinstance(data, dict) else None
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() in ("true", "yes", "1")
    return None


def _extract_list(data: dict[str, Any], *keys: str) -> list[Any]:
    """Extract list value following key path."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, [])
        else:
            return []
    return current if isinstance(current, list) else []


def _extract_price(data: dict[str, Any]) -> float | None:
    """Extract price from various response formats."""
    for key in ("priceUsd", "price", "usdPrice", "usd"):
        val = data.get(key) if isinstance(data, dict) else None
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    return None


def _merge_holder_lists(holder_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge holder lists from multiple sources, deduplicating by address."""
    seen = set()
    merged = []
    for holders in holder_sets:
        for h in holders:
            addr = h.get("address", "")
            if addr and addr not in seen:
                seen.add(addr)
                merged.append(h)
    return merged


def _deduplicate_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove redundant findings (similar descriptions)."""
    seen = set()
    unique = []
    for f in findings:
        key = f.get("finding", "")[:80].lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _risk_level(score: float) -> str:
    """Map numeric score to risk level."""
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "minimal"


def _one_liner(score: float, findings: list[dict[str, Any]]) -> str:
    """Generate a single-line risk summary."""
    level = _risk_level(score)
    top_finding = findings[0].get("finding", "")[:60] if findings else ""
    if top_finding:
        return f"{level.upper()}: {top_finding}"
    return f"{level.upper()}: No specific findings"


def _top_findings(findings: list[dict[str, Any]], max_count: int = 5) -> list[dict[str, Any]]:
    """Return top N findings sorted by severity."""
    sorted_f = sorted(findings, key=lambda f: f.get("severity", 0), reverse=True)
    return sorted_f[:max_count]


def _empty_result(reason: str) -> dict[str, Any]:
    """Return a standard empty fusion result."""
    return {
        "success": False,
        "reason": reason,
        "confidence": 0,
        "fused_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
