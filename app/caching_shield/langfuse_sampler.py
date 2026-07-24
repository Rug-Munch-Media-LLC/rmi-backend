#!/usr/bin/env python3
"""
Langfuse Smart Sampling - Never exceed free tier, keep local as fallback.

Strategy:
  - Sample 20% of normal traces → cloud
  - 100% of errors (score < 0.5) → cloud
  - 100% of user-facing requests → cloud
  - Everything → local ClickHouse (full archive)
  - Target: 800-1,200 observations/day (free tier: 1,600/day)

Free tier: 50,000 observations/month
Our target: 30,000/month (60% headroom)
"""
from __future__ import annotations

import os
import random

from app.core.logging import get_logger

logger = get_logger("langfuse_sampler")

SAMPLING_RATE = float(os.getenv("LANGFUSE_SAMPLING_RATE", "0.20"))  # 20% normal
ERROR_RATE = 1.0  # 100% of errors
USER_RATE = 1.0  # 100% of user-facing

_counters = {
    "total": 0,
    "sampled": 0,
    "errors_captured": 0,
    "local_only": 0,
}

def should_send_to_cloud(
    is_error: bool = False,
    is_user_facing: bool = False,
    force: bool = False,
) -> bool:
    """Decide whether to send this trace to Langfuse cloud."""
    _counters["total"] += 1

    if force:
        _counters["sampled"] += 1
        return True

    # Always capture errors (failed calls, hallucinations, etc.)
    if is_error:
        _counters["errors_captured"] += 1
        _counters["sampled"] += 1
        return True

    # Always capture user-facing interactions
    if is_user_facing:
        _counters["sampled"] += 1
        return True

    # Sample normal background traces
    if random.random() < SAMPLING_RATE:
        _counters["sampled"] += 1
        return True

    _counters["local_only"] += 1
    return False

def stats() -> dict:
    total = max(_counters["total"], 1)
    return {
        **_counters,
        "sampling_rate_pct": round(_counters["sampled"] / total * 100, 1),
        "estimated_daily": _counters["sampled"],
    }
