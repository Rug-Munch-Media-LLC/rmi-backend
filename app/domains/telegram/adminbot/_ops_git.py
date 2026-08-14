"""
_ops_git — known RugMunch repos + git status + push-all.

Extracted from ``admin_ops`` (degodding 2026-08-14).
"""

from __future__ import annotations

import os
from typing import Any

from app.domains.telegram.adminbot._ops_core import _err, _ok, _run_subprocess, _validate_name

# Known RugMunch repos for git/push operations
RMI_REPOS: list[str] = [
    "rmi-backend",
    "rmi-frontend",
    "fleet-infra",
    "pryscraper",
    "walletpress",
    "rugmunchbot",
    "standards",
    "rmi-rag",
    "rmi-mcp-x402",
    "rmi-docs",
]


def git_status(repo_name: str) -> dict[str, Any]:
    """Get git status for a known repo."""
    if not _validate_name(repo_name):
        return _err(f"Invalid repo name: '{repo_name}'")

    base_path = f"/srv/work/repos/{repo_name}"
    if not os.path.isdir(base_path):
        # Try alternative locations
        alt = f"/root/{repo_name}"
        if os.path.isdir(alt):
            base_path = alt
        else:
            return _err(f"Repo not found at /srv/work/repos/{repo_name}")

    # Get branch, status, and recent commits
    branch_r = _run_subprocess(["git", "branch", "--show-current"], timeout=5, cwd=base_path)
    status_r = _run_subprocess(["git", "status", "--short"], timeout=5, cwd=base_path)
    log_r = _run_subprocess(["git", "log", "--oneline", "-5"], timeout=5, cwd=base_path)

    output = f"📁 *{repo_name}*\n"
    output += f"🌿 Branch: `{branch_r.get('output', '').strip() or 'unknown'}`\n"

    dirty = status_r.get("output", "").strip()
    if dirty:
        lines = dirty.splitlines()
        output += f"📝 *{len(lines)} uncommitted file(s):*\n"
        for line in lines[:10]:
            output += f"  `{line[:60]}`\n"
    else:
        output += "✅ Clean working tree\n"

    if log_r["ok"] and log_r["output"].strip():
        output += f"\n📜 *Recent commits:*\n```\n{log_r['output'].strip()}\n```"

    return _ok(output)


def push_all_repos() -> dict[str, Any]:
    """Push all repos using the push-all-repos script."""
    script_path = "/root/scripts/push-all-repos.sh"
    if not os.path.exists(script_path):
        return _err(f"Push script not found: {script_path}")
    return _run_subprocess(["bash", script_path], timeout=300)
