"""Composite scoring + high-level determination for the risk_report package.

``_composite_and_determination`` computes the weighted-average composite over
KNOWN factors and maps it to a scam / rug / risky / ok / unknown verdict.
``_summary_text`` renders the one-line human-readable summary.
"""

from __future__ import annotations

from app.domains.scanner.unified.risk_report._model import (
    _MIN_KNOWN_FOR_DETERMINATION,
    _RISKY_MIN,
    _RUG_MIN,
    _SCAM_MIN,
    FACTOR_WEIGHTS,
    RiskFactor,
)


def _composite_and_determination(
    factors: list[RiskFactor],
    honeypot: dict[str, Any],
    deployer_section: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[int | None, str, str]:
    """Weighted-average composite over KNOWN factors + high-level verdict."""
    known = [f for f in factors if f.risk_score is not None]
    if not known:
        # A confirmed honeypot is a decisive scam vector and must be reported
        # even when no other factor managed to score (thin scan). Otherwise the
        # scan is genuinely indeterminate.
        if bool(honeypot.get("is_honeypot")):
            return None, "scam", _summary_text("scam", None, 0, len(factors))
        return None, "unknown", "Insufficient data to score this token."

    total_w = 0.0
    total = 0.0
    for f in known:
        if f.risk_score is None:
            continue
        w = FACTOR_WEIGHTS.get(f.key, 1.0)
        total += float(f.risk_score) * w
        total_w += w
    composite = int(round(total / total_w)) if total_w > 0 else 50

    determination: str
    if bool(honeypot.get("is_honeypot")) or composite >= _SCAM_MIN:
        determination = "scam"
    elif composite >= _RUG_MIN or (
        len(known) >= _MIN_KNOWN_FOR_DETERMINATION
        and bool(deployer_section.get("previously_rugged"))
        and bool(bundle.get("is_bundled"))
    ):
        determination = "rug"
    elif composite >= _RISKY_MIN:
        determination = "risky"
    elif composite < _RISKY_MIN and len(known) >= _MIN_KNOWN_FOR_DETERMINATION:
        determination = "ok"
    else:
        # Too few known factors to declare a clean "ok" verdict — a low
        # composite built from e.g. 1/30 known factors is not evidence of a
        # safe token (the reader must not mistake "0/100 OK" for a clean bill).
        determination = "unknown"

    summary = _summary_text(determination, composite, len(known), len(factors))
    return composite, determination, summary


def _summary_text(determination: str, composite: int | None, known: int, total: int) -> str:
    """Human-readable one-line summary of the verdict.

    When a determination cannot be made (too few known factors), the summary
    must NOT present a composite score — a "Composite risk 0/100 … OK · 1/30
    factors known" headline is self-contradictory and misleads readers.
    """
    if determination == "unknown":
        return (
            f"Insufficient data — {known}/{total} factors known, a verdict needs "
            f"at least {_MIN_KNOWN_FOR_DETERMINATION}. Scan pending; re-run or use a deeper tier."
        )
    if composite is not None:
        base = f"Composite risk {composite}/100 from {known}/{total} known factors."
    else:
        base = f"Decisive vector detected from {known}/{total} known factors."
    if determination == "scam":
        return f"{base} DETERMINATION: SCAM — decisive scam vectors detected; do not trade."
    if determination == "rug":
        return f"{base} DETERMINATION: RUG — strong rug-pull signals; extreme caution."
    if determination == "risky":
        return f"{base} DETERMINATION: RISKY — elevated risk; only trade with full awareness."
    return f"{base} DETERMINATION: OK — no dominant scam/rug signal detected."
