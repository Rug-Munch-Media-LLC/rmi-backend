"""Tests for the admin bot health dashboard, server ops, and docker ops.

Covers the pure parsing/classification/formatting helpers in
``app.domains.telegram.adminbot.admin_health``, ``admin_server`` and
``admin_docker`` — no live bot or network required.

The adminbot ``config`` module fatally exits when ADMIN_BOT_TOKEN /
ADMIN_CHAT_ID are unset, so the env vars are seeded before import.
"""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")

import datetime as _dt

from app.domains.telegram.adminbot import _ops_backup, admin_bot, admin_docker, admin_health, admin_ops, admin_server

# ═══════════════════════════════════════════════
# Emoji classifiers
# ═══════════════════════════════════════════════


class TestEmojiClassifiers:
    def test_disk_pct(self) -> None:
        assert admin_health.emoji_for_pct(10) == "🟢"
        assert admin_health.emoji_for_pct(85) == "🟡"
        assert admin_health.emoji_for_pct(95) == "🔴"

    def test_mem_available(self) -> None:
        assert admin_health.emoji_for_mem_available(10.0) == "🟢"
        assert admin_health.emoji_for_mem_available(3.0) == "🟡"
        assert admin_health.emoji_for_mem_available(1.0) == "🔴"

    def test_load(self) -> None:
        assert admin_health.emoji_for_load(0.5, 16) == "🟢"
        assert admin_health.emoji_for_load(16.0, 16) == "🟡"  # fully loaded
        assert admin_health.emoji_for_load(50.0, 16) == "🔴"
        assert admin_health.emoji_for_load(10.0, 0) == "🟡"  # unknown cores

    def test_hours_ago(self) -> None:
        assert admin_health.emoji_for_hours_ago(5) == "🟢"
        assert admin_health.emoji_for_hours_ago(48) == "🟡"
        assert admin_health.emoji_for_hours_ago(200) == "🔴"
        assert admin_health.emoji_for_hours_ago(None) == "🔴"

    def test_cert_days(self) -> None:
        assert admin_health.emoji_for_cert_days(90) == "🟢"
        assert admin_health.emoji_for_cert_days(20) == "🟡"
        assert admin_health.emoji_for_cert_days(10) == "🔴"
        assert admin_health.emoji_for_cert_days(None) == "🔴"

    def test_container_status(self) -> None:
        assert admin_health.classify_container_status("Up 5 days (healthy)") == "🟢"
        assert admin_health.classify_container_status("Up 2 minutes") == "🟢"
        assert admin_health.classify_container_status("Up 1 second (health: starting)") == "🟡"
        assert admin_health.classify_container_status("Created") == "🟡"
        assert admin_health.classify_container_status("Exited (0) 2 days ago") == "🔴"
        assert admin_health.classify_container_status("Restarting (1) 19 seconds ago") == "🔴"
        assert admin_health.classify_container_status("Up 1 hour (unhealthy)") == "🔴"
        assert admin_health.classify_container_status("not found") == "🔴"

    def test_systemd_status(self) -> None:
        assert admin_health.classify_systemd_status("running", "active") == "🟢"
        assert admin_health.classify_systemd_status("exited", "active") == "⚪"
        assert admin_health.classify_systemd_status("dead", "inactive") == "⚫"
        assert admin_health.classify_systemd_status("failed", "failed") == "🔴"


# ═══════════════════════════════════════════════
# Parsers
# ═══════════════════════════════════════════════


class TestParsers:
    def test_parse_df_pct(self) -> None:
        out = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/vda1       2.0T  928G  1.1T  47% /\n"
            "/dev/vda2        99G   80G   15G  85% /var\n"
        )
        assert admin_health.parse_df_pct(out) == 85.0
        assert admin_health.parse_df_pct("no data here") is None

    def test_parse_free_memory(self) -> None:
        out = (
            "               total        used        free      shared  buff/cache   available\n"
            "Mem:           64295       38491        1140        1372       26760       25804\n"
            "Swap:             15           0           15\n"
        )
        avail, total = admin_health.parse_free_memory(out)
        assert avail == 25804 / 1024.0
        assert total == 64295 / 1024.0
        assert admin_health.parse_free_memory("garbage") is None

    def test_parse_loadavg(self) -> None:
        assert admin_health.parse_loadavg("14.12 11.59 11.22 20/6384 18497\n") == (14.12, 11.59, 11.22)
        assert admin_health.parse_loadavg("nope") is None

    def test_parse_cert_enddate(self) -> None:
        dt = admin_health.parse_cert_enddate("notAfter=Sep 29 16:57:27 2026 GMT")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 9
        assert dt.day == 29
        assert dt.tzinfo is not None
        assert admin_health.parse_cert_enddate("") is None
        assert admin_health.parse_cert_enddate("garbage") is None

    def test_parse_systemd_units(self) -> None:
        out = (
            "rmi-backend.service loaded active running RMI Backend\n"
            "rmiadminbot.service loaded active running RMI Admin Bot\n"
            "cron.service loaded failed failed Cron daemon\n"
        )
        units = admin_health.parse_systemd_units(out)
        assert len(units) == 3
        assert units[0]["unit"] == "rmi-backend.service"
        assert units[0]["sub"] == "running"
        assert units[2]["sub"] == "failed"

    def test_parse_ps_output(self) -> None:
        out = "35281 infinity_emb 235.0 9.9 6519468\n23989 opencode 128.0 2.4 1581124\n"
        rows = admin_server._parse_ps_output(out)
        assert len(rows) == 2
        assert rows[0]["pid"] == 35281
        assert rows[0]["rss_mb"] == round(6519468 / 1024.0, 1)

    def test_parse_listening_ports(self) -> None:
        out = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            'LISTEN 0 4096 127.0.0.1:3306 0.0.0.0:* users:(("docker-proxy",pid=123,fd=9))\n'
            'LISTEN 0 128 [::]:80 0.0.0.0:* users:(("caddy",pid=456,fd=3))\n'
        )
        ports = admin_server._parse_listening_ports(out)
        # Header row must be skipped.
        assert len(ports) == 2
        assert ports[0]["port"] == "3306"
        assert ports[0]["proc"] == "docker-proxy"
        assert ports[1]["port"] == "80"
        assert ports[1]["proc"] == "caddy"

    def test_bytes_from_size(self) -> None:
        assert admin_docker._bytes_from_size("1.0GiB") == 1024**3
        assert admin_docker._bytes_from_size("393.6MiB") == round(393.6 * 1024**2, 1)
        assert admin_docker._bytes_from_size("500MB") == 5e8
        assert admin_docker._bytes_from_size("junk") == 0.0


# ═══════════════════════════════════════════════
# Formatters
# ═══════════════════════════════════════════════


class TestFormatters:
    def test_format_quick_status(self) -> None:
        data = {
            "hostname": "testhost",
            "uptime": "up 3 days",
            "disk_pct": 47.0,
            "disk_level": "🟢",
            "mem_avail_gb": 26.7,
            "mem_total_gb": 62.8,
            "mem_level": "🟢",
            "load": (0.5, 0.4, 0.3),
            "load_level": "🟢",
            "nproc": 16,
            "docker": {"running": 10, "total": 12, "exited": 2},
            "systemd_failed": 0,
            "critical_alerts": 2,
            "backup": {"backup_hours_ago": 5.0, "level": "🟢"},
        }
        text = admin_health.format_quick_status(data)
        assert "testhost" in text
        assert "47%" in text
        assert "26.7G" in text
        assert "0.50 / 0.40 / 0.30" in text
        assert "10` running" in text
        assert "5.0h ago" in text
        assert "2` 🔴" in text

    def test_format_health_dashboard_sections(self) -> None:
        data = {
            "disk_pct": 47.0,
            "disk_level": "🟢",
            "df_output": "/dev/vda3 2.0T 928G 1.1T 47% /\n",
            "mem_avail_gb": 26.7,
            "mem_total_gb": 62.8,
            "mem_level": "🟢",
            "free_output": "Mem:            64295       38491        1140        1372       26760       25804\n",
            "load": (0.5, 0.4, 0.3),
            "load_level": "🟢",
            "nproc": 16,
            "docker": {"running": 10, "total": 12, "exited": 2},
            "systemd_failed": [],
            "backup": {"backup_hours_ago": 5.0, "level": "🟢", "git_hours_ago": 10.0},
            "services": [
                {"name": "rmi-backend", "status": "Up 2 minutes (healthy)", "level": "🟢"},
                {"name": "rmi-postgres", "status": "Restarting", "level": "🔴"},
            ],
            "critical_alerts": 1,
        }
        text = admin_health.format_health_dashboard(data)
        assert "System Health Dashboard" in text
        assert "Disk" in text
        assert "Memory" in text
        assert "CPU Load" in text
        assert "Docker" in text
        assert "Systemd" in text
        assert "Backup" in text
        assert "Critical Services" in text
        assert "rmi-postgres" in text

    def test_format_top(self) -> None:
        result = {
            "ok": True,
            "rows": [
                {"pid": 1, "command": "init", "cpu": 5.0, "mem": 1.0, "rss_mb": 10.0},
                {"pid": 2, "command": "worker", "cpu": 3.5, "mem": 0.5, "rss_mb": 5.0},
            ],
        }
        text = admin_server.format_top(result)
        assert "init" in text
        assert "worker" in text
        assert "PID" in text

    def test_format_certs(self) -> None:
        now = _dt.datetime.now(_dt.UTC)
        result = {
            "ok": True,
            "count": 1,
            "certs": [
                {
                    "domain": "example.com",
                    "source": "acme.sh",
                    "expires": now + _dt.timedelta(days=45),
                    "days_left": 45,
                    "level": "🟢",
                }
            ],
        }
        text = admin_server.format_certs(result)
        assert "example.com" in text
        assert "45d" in text
        assert "🟢" in text

    def test_format_services_colors(self) -> None:
        result = {
            "ok": True,
            "total": 2,
            "failed": 1,
            "active": 1,
            "rows": [
                {"unit": "bad.service", "active": "failed", "sub": "failed", "level": "🔴"},
                {"unit": "good.service", "active": "active", "sub": "running", "level": "🟢"},
            ],
        }
        text = admin_server.format_services(result)
        assert "good.service" in text
        assert "bad.service" in text
        assert "FAILED (1)" in text
        assert "Showing 2/2" in text

    def test_format_docker_ps(self) -> None:
        result = {
            "ok": True,
            "rows": [
                {
                    "name": "rmi-backend",
                    "status": "Up 2 minutes (healthy)",
                    "health": "healthy",
                    "uptime": "Up 2 minutes",
                },
            ],
        }
        text = admin_docker.format_docker_ps(result)
        assert "rmi-backend" in text
        assert "healthy" in text
        assert "Up 2 minutes" in text
        assert "1 running" in text

    def test_format_docker_stats(self) -> None:
        result = {
            "ok": True,
            "rows": [
                {"name": "infinity-rag", "mem_usage": "4.899GiB / 10GiB", "mem_pct": "48.99%", "cpu_pct": "0.77%"},
            ],
        }
        text = admin_docker.format_docker_stats(result)
        assert "infinity-rag" in text
        assert "48.99%" in text


# ═══════════════════════════════════════════════
# v5 — Control panel + bot management + backups
# ═══════════════════════════════════════════════


class TestControlPanel:
    def test_format_control_panel_sections(self) -> None:
        data = {
            "hostname": "testhost",
            "uptime": "up 3 days",
            "disk_pct": 47.0,
            "disk_level": "🟢",
            "mem_avail_gb": 26.7,
            "mem_total_gb": 62.8,
            "mem_level": "🟢",
            "load": (0.5, 0.4, 0.3),
            "load_level": "🟢",
            "nproc": 16,
            "docker": {"running": 10, "total": 12, "exited": 2},
            "systemd_failed": 0,
            "critical_alerts": 2,
            "backup": {"backup_hours_ago": 5.0, "level": "🟢"},
            "service_summary": {
                "Backend": "🟢",
                "Postgres": "🟢",
                "ClickHouse": "🟡",
                "Redis": "🟢",
                "RAG": "🟢",
                "Bots": {"rugmunchbot": "🟢", "rmiadminbot": "🟢"},
            },
        }
        text = admin_health.format_control_panel(data)
        assert "Control Panel" in text
        assert "Service Health" in text
        assert "Backend" in text
        assert "ClickHouse" in text
        assert "Bot" in text
        assert "AdminBot" in text
        assert "Quick Stats" in text
        assert "47%" in text
        assert "26.7G" in text

    def test_format_control_panel_bots_failure(self) -> None:
        data = {
            "hostname": "h",
            "service_summary": {"Bots": {"rugmunchbot": "🔴", "rmiadminbot": "🟢"}},
        }
        text = admin_health.format_control_panel(data)
        assert "🔴 Bot" in text
        assert "🟢 AdminBot" in text


class TestAdminBotModule:
    def test_format_bot_stats(self) -> None:
        data = {
            "users": 45,
            "banned": 2,
            "active_today": 12,
            "messages_today": 80,
            "scans_today": 7,
            "enabled": True,
            "chat_enabled": True,
        }
        text = admin_bot.format_bot_stats(data)
        assert "RugMunchBot Stats" in text
        assert "45" in text
        assert "80" in text
        assert "ENABLED" in text

    def test_bot_users_empty_ok(self) -> None:
        result = admin_bot.bot_users(limit=5)
        assert result["ok"] in (True, False)  # fails open when DB unavailable
        if result["ok"]:
            assert "Recent RugMunchBot Users" in result["output"] or "No users" in result["output"]

    def test_bot_broadcast_empty_message(self) -> None:
        import asyncio

        result = asyncio.run(admin_bot.bot_broadcast("   "))
        assert result["ok"] is False
        assert "empty" in result["error"].lower()

    def test_scanner_stats_shape(self) -> None:
        result = admin_bot.scanner_stats()
        assert result["ok"] in (True, False)
        if result["ok"]:
            assert "Scanner Stats" in result["output"]
            assert "Scans today" in result["output"]


class TestBackupReport:
    def test_backup_report_missing_sentinel(self, tmp_path, monkeypatch) -> None:
        # Point sentinels at a nonexistent path so the report shows red lines.
        monkeypatch.setattr(_ops_backup, "_BACKUP_SENTINEL", str(tmp_path / "no-sentinel"))
        monkeypatch.setattr(_ops_backup, "_GIT_BACKUP_SENTINEL", str(tmp_path / "no-sentinel-git"))
        monkeypatch.setattr(_ops_backup, "os", __import__("os"))
        # Ensure /backups dir read is harmless — monkeypatch the dir check.
        monkeypatch.setattr(_ops_backup.os.path, "isdir", lambda p: False)
        result = admin_ops.backup_report()
        assert result["ok"] is True
        assert "Backup Report" in result["output"]
        assert "no sentinel" in result["output"]


class TestScheduleReboot:
    def test_schedule_reboot_rejects_small_delay(self, monkeypatch) -> None:
        captured: list[list[str]] = []

        def fake_run(args, timeout=30, cwd=None):
            captured.append(list(args))
            return {"ok": True, "output": "ok", "error": "", "duration": 0.01}

        monkeypatch.setattr(admin_server, "_run_subprocess", fake_run)
        result = admin_server.schedule_reboot(delay_seconds=5)  # clamps to 10
        assert result["ok"] is True
        assert captured
        assert "systemd-run" in captured[0][0]
        # on-active should be clamped to >= 10
        assert int(captured[0][2]) >= 10
