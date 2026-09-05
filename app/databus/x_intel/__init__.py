"""
RugCharts X/CT Intelligence Pipeline
====================================
"CT Rundown" - top 20 stories daily from Crypto Twitter.
Multi-method access: xurl (OAuth), cookie scraping, Groq AI analysis.

Algorithm: engagement-weighted, diversity-scored, entity-resolved.

Split into sub-modules:
  - fetchers.py  — X/Twitter data fetching (xurl, cookie scrape, web scrape)
  - analyzers.py — scoring, summarization, rundown pipeline
  - cache.py     — influencer-registry cache loader
  - helpers.py   — curated CT account data + shared constants
"""

from __future__ import annotations

from app.databus.x_intel.analyzers import (
    fetch_ct_rundown,
    score_ct_post,
    track_ct_accounts,
)
from app.databus.x_intel.cache import load_influential_accounts
from app.databus.x_intel.helpers import (
    ALL_CT,
    CT_ACCOUNTS,
    CT_HANDLES,
    GROQ_KEY,
    INFLUENTIAL_ACCOUNTS,
    TRACKED_ACCOUNTS,
)

__all__ = [
    "ALL_CT",
    "CT_ACCOUNTS",
    "CT_HANDLES",
    "GROQ_KEY",
    "INFLUENTIAL_ACCOUNTS",
    "TRACKED_ACCOUNTS",
    "fetch_ct_rundown",
    "load_influential_accounts",
    "score_ct_post",
    "track_ct_accounts",
]
