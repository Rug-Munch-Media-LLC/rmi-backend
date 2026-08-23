"""Tests for adminbot _ops_control formatters (server-control expansion).

Formatters are pure dict->str; behavior locked against live-probe output
2026-08-23. The bullet-strip regression in format_digest was caught by a
live probe: systemctl --no-legend prefixes failed-unit lines with '●'.
"""

from __future__ import annotations

import os

# adminbot/config.py validates env at import time (sys.exit on missing) and
# the package __init__ eagerly imports bot->config. Satisfy it BEFORE any
# app.domains.telegram import so pytest can collect this module.
os.environ.setdefault("ADMIN_BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")
os.environ.setdefault("TELEGRAM_ALLOWED_USERS", "7075336557")

from typing import Any

from app.domains.telegram.adminbot._ops_control import (
    format_cleanup,
    format_digest,
    format_logins,
    format_pressure,
)


def test_format_pressure_flags_oom_and_killed() -> None:
    result = {
        "rows": [
            {
                "service": "llama-qwen38",
                "mem_current": 18_874_368_000,
                "oom": 6248,
                "killed": 1,
                "mem_avg10": "12.5",
                "cpu_avg10": "800.0",
            },
            {
                "service": "rmiadminbot",
                "mem_current": 16_000_000,
                "oom": 0,
                "killed": 0,
                "mem_avg10": "-",
                "cpu_avg10": "-",
            },
        ],
        "root_mem_psi": ["some avg10=0.06 avg60=1.33 avg300=1.95 total=316445850323"],
    }
    out = format_pressure(result)
    assert "Cgroup Pressure" in out
    assert "OOM x6248" in out
    assert "killed x1" in out  # thrash incident signature
    assert "17.6G" in out or "18G" in out  # humanized bytes
    assert "host memory PSI" in out


def test_format_pressure_empty_rows_renders_header_only() -> None:
    out = format_pressure({"rows": [], "root_mem_psi": []})
    assert "Cgroup Pressure" in out
    assert "OOM" not in out


def test_format_digest_strips_systemctl_bullet() -> None:
    # Regression: unit name rendered as '●' before the fix.
    result = {
        "failed": {"ok": True, "output": "\u25cf clickhouse-snapshot.service loaded failed failed\n"},
        "disk": {"ok": True, "output": "Filesystem Size Used Avail Use% Mounted\n/dev/sda1 2.0T 1.3T 700G 62% /"},
        "mem": {"ok": True, "output": "Mem: 64295 42074 10000 0 12000 21000"},
        "load": {"ok": True, "output": "32.54 32.88 39.03 1/1234 5678"},
        "docker_count": 105,
        "oom_total": 0,
    }
    out = format_digest(result)
    assert "`clickhouse-snapshot.service`" in out
    assert "\u25cf" not in out
    assert "RAM: 42074M / 64295M (65%)" in out
    assert "Containers: 105 running" in out
    assert "OOM/kill events: 0" in out


def test_format_digest_no_failed_units_green() -> None:
    result = {
        "failed": {"ok": True, "output": ""},
        "disk": {"ok": False},
        "mem": {"ok": False},
        "load": {"ok": False},
        "docker_count": 0,
        "oom_total": 3,
    }
    out = format_digest(result)
    assert "No failed systemd units" in out
    assert "⚠️" in out
    assert "OOM/kill events: 3" in out


def test_format_logins_extracts_jail_list_from_tree() -> None:
    result = {
        "sessions": {"ok": True, "output": "root pts/0 192.0.2.10\n"},
        "logins": {"ok": True, "output": "root pts/1 Fri08 still logged in\nwtmp begins Mon\n"},
        "fail2ban": {
            "ok": True,
            "output": "Status\n|- Number of jail:\t5\n`- Jail list:\tdovecot, sshd, postfix-sasl\n",
        },
    }
    out = format_logins(result)
    assert "Active sessions:" in out
    assert "Recent logins:" in out
    assert "fail2ban jails (3)" in out
    assert "dovecot, sshd, postfix-sasl" in out
    assert "|-" not in out
    assert "`-" not in out  # tree glyphs stripped


def test_format_logins_fail2ban_without_jail_list_line() -> None:
    result = {
        "sessions": {"ok": False},
        "logins": {"ok": False},
        "fail2ban": {"ok": True, "output": "Status\n|- Number of jail:\t2\n"},
    }
    out = format_logins(result)
    assert "No active user sessions." in out
    assert "fail2ban: active" in out


def test_format_cleanup_dry_run_never_promises_deletion() -> None:
    result = {
        "journal": {"ok": True, "output": "Archived and active journals take up 184.7M in the file system."},
        "apt": {"ok": True, "output": "224M\t/var/cache/apt"},
        "pip": {"ok": True, "output": "31M\t/root/.cache/pip"},
        "docker": {"ok": True, "output": "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 100 85 56.75GB 54.19GB (95%)"},
    }
    out = format_cleanup(result)
    assert "dry-run" in out
    assert "nothing deleted" in out
    assert "184.7M" in out
    assert "--vacuum-size" in out
    assert "apt-get clean" in out
    assert "pip cache purge" in out
    assert "/dprune" in out  # docker reclamation delegated to existing command


def _digest_with_failed(raw: str) -> dict[str, Any]:
    return {
        "failed": {"ok": True, "output": raw},
        "disk": {"ok": False},
        "mem": {"ok": False},
        "load": {"ok": False},
        "docker_count": 0,
        "oom_total": 0,
    }


def test_format_digest_multiple_failed_units_capped_at_eight() -> None:
    raw = "".join(f"\u25cf svc{i}.service loaded failed failed\n" for i in range(10))
    out = format_digest(_digest_with_failed(raw))
    assert "Failed units (10):" in out
    assert out.count("svc") == 8  # cap
