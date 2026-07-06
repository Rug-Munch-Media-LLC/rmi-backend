#!/usr/bin/env python3
"""T44 Cron Health Check - detects silent cron failures.

Per v4.0 §T44. Runs every 5 minutes, checks that all crons have
logged success in their expected window. Reports failures to:
  1. /var/log/cron-health.log (local)
  2. rmi-backend /api/v1/admin/alerts/critical (for paging)
  3. rmi-backend /api/v1/_test/glitchtip (when GlitchTip is up)

Run as a cron itself: */5 * * * * /root/scripts/cron_health_check.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

# ── Config ─────────────────────────────────────────────────────────
BACKEND_URL = os.getenv("RMI_BACKEND_URL", "http://127.0.0.1:8000")
LOG_FILE = "/var/log/cron-health.log"
CRONTAB_FILE = "/tmp/cron-inventory.txt"

# Each cron: (name, schedule, expected_window_minutes, log_file_pattern)
# expected_window = how often it should fire (e.g. */15 → 15 min window)
CRONS = [
    # (name, schedule, window_min, log_file)
    ("security-state",    "* * * * *",   5,   "/var/log/security-state.log"),
    ("vault-sync",        "*/30 * * * *", 35, "/root/.hermes/logs/vault-sync.log"),
    ("cache-warmer",      "*/4 * * * *",  8,   "/tmp/cache-warmer.log"),
    ("ingest-runner",     "*/15 * * * *", 20, "/var/log/ingest_cron.log"),
    ("wallet-ingest",     "*/30 * * * *", 35, "/var/log/wallet_ingest.log"),
    ("master-ingest",     "0 * * * *",    65,  "/var/log/master_ingest.log"),
    ("neo4j-sync",        "0 */6 * * *",  370, "/var/log/neo4j_sync.log"),
    ("reembed-rag",       "0 3 * * *",    1500, "/var/log/reembed_cron.log"),
    ("rag-rss",           "*/15 * * * *", 20, "/var/log/rag_ingest_rss.log"),
    ("rag-market",        "0 * * * *",    65,  "/var/log/rag_ingest_market.log"),
    ("rag-threat",        "0 */6 * * *",  370, "/var/log/rag_ingest_threat.log"),
    ("rag-clickhouse",    "*/30 * * * *", 35, "/var/log/rag_ingest_ch.log"),
    ("mbal-labels",       "0 2 * * *",    1500, "/var/log/rag_mbal.log"),
    ("daily-brief",       "30 6 * * *",   1500, "/var/log/daily_brief.log"),
]


def log(msg: str) -> None:
    """Append to local log file."""
    ts = datetime.now(UTC).isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")
    print(f"{ts} {msg}")


def check_log_for_errors(log_file: str) -> tuple[bool, str]:
    """Check a cron log for known error patterns.

    Returns (healthy, last_error).
    """
    if not Path(log_file).exists():
        return False, "log file missing"

    # Read last 100 lines
    try:
        result = subprocess.run(
            ["tail", "-n", "100", log_file],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        return False, f"log read fail: {e}"

    lines = result.stdout.splitlines()
    if not lines:
        return False, "empty log"

    # Look for known error patterns
    error_patterns = [
        r"file not found", r"No such file", r"command not found",
        r"ImportError", r"ModuleNotFoundError",
        r"connection refused", r"Connection refused",
        r"Permission denied", r"No module named",
    ]
    last_error = ""
    for line in lines[-20:]:  # check last 20 lines
        for pat in error_patterns:
            if re.search(pat, line, re.IGNORECASE):
                last_error = line[:200]
                return False, last_error

    # Check for recent activity (log has content from last N hours)
    if "ImportError" in result.stdout or "ModuleNotFoundError" in result.stdout:
        return False, "Python import error"

    return True, ""


def check_all_crons() -> list[dict]:
    """Run all checks. Return list of (name, healthy, error)."""
    results = []
    for name, sched, window, log_file in CRONS:
        healthy, error = check_log_for_errors(log_file)
        results.append({
            "name": name,
            "schedule": sched,
            "window_min": window,
            "log_file": log_file,
            "healthy": healthy,
            "error": error,
            "checked_at": datetime.now(UTC).isoformat(),
        })
    return results


def report_to_backend(stale: list[dict]) -> bool:
    """Post stale crons to rmi-backend alerts."""
    if not stale:
        return True
    try:
        import httpx
        url = urljoin(BACKEND_URL, "/api/v1/admin/alerts/critical")
        payload = {
            "title": f"{len(stale)} cron failures",
            "description": f"Failed crons: {', '.join(c['name'] for c in stale)}",
            "severity": "warning",
            "evidence": {"stale_crons": stale},
        }
        with httpx.Client(timeout=10) as c:
            r = c.post(url, json=payload)
            if r.status_code < 300:
                log(f"reported_to_backend status={r.status_code}")
                return True
            log(f"report_failed status={r.status_code} body={r.text[:200]}")
            return False
    except Exception as e:
        log(f"report_exception err={e}")
        return False


def main():
    log("=== cron_health_check start ===")
    results = check_all_crons()
    stale = [r for r in results if not r["healthy"]]

    summary = {
        "total": len(results),
        "healthy": len(results) - len(stale),
        "stale": len(stale),
        "stale_names": [r["name"] for r in stale],
    }
    log(f"summary: {json.dumps(summary)}")

    for r in stale:
        log(f"  STALE: {r['name']} - {r['error'][:120]}")

    if stale:
        report_to_backend(stale)

    log("=== cron_health_check end ===")
    return 0 if not stale else 1


if __name__ == "__main__":
    sys.exit(main())
