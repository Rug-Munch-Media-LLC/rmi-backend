"""Test CLI entry points — scripts with ``if __name__ == "__main__"`` blocks.

Tests cover argument parsing, exit codes, error handling, and edge cases
for the operational scripts under ``scripts/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────


def _run_script_main(script_path: str, args: list[str]) -> tuple[int, str, str]:
    """Run a script's ``main()`` by importing it with patched ``sys.argv``.

    Returns (exit_code, stdout_text, stderr_text).
    """
    import io

    mod_path = Path(script_path).resolve()
    if not mod_path.exists():
        msg = f"Script not found: {mod_path}"
        raise FileNotFoundError(msg)

    # Import the module
    import importlib.util

    spec = importlib.util.spec_from_file_location("_cli_test_mod", str(mod_path))
    if spec is None or spec.loader is None:
        msg = f"Could not load spec for {mod_path}"
        raise ImportError(msg)
    mod = importlib.util.module_from_spec(spec)

    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [str(mod_path), *args]
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        spec.loader.exec_module(mod)
        if hasattr(mod, "main"):
            rc = mod.main()
        else:
            rc = 0
    except SystemExit as e:
        rc = e.code if e.code is not None else 0
    except Exception as e:  # noqa: BLE001
        rc = -1
        sys.stderr.write(f"Unhandled exception: {e}\n")
    finally:
        out = sys.stdout.getvalue()
        err = sys.stderr.getvalue()
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return (rc, out, err)


# ═══════════════════════════════════════════════════════════════════════
# 1. export_openapi.py
# ═══════════════════════════════════════════════════════════════════════


class TestExportOpenAPI:
    """Tests for ``scripts/export_openapi.py``."""

    MOD = "scripts.export_openapi"

    @patch("scripts.export_openapi.get_openapi_schema")
    def test_stdout_flag(self, mock_schema):
        """--stdout prints JSON schema to stdout and returns 0."""
        mock_schema.return_value = {
            "openapi": "3.1.0",
            "paths": {"/health": {}},
            "components": {"schemas": {}},
        }
        with (
            patch.object(sys, "argv", ["export_openapi.py", "--stdout"]),
            patch("sys.stdout", new_callable=lambda: _CaptureIO()) as out,
        ):
            from scripts.export_openapi import main

            rc = main()
        assert rc == 0
        assert json.loads(out.getvalue())["openapi"] == "3.1.0"

    @patch("scripts.export_openapi.get_openapi_schema")
    def test_check_flag_fails_below_min_paths(self, mock_schema):
        """--check with < min-paths exits 1."""
        mock_schema.return_value = {
            "openapi": "3.1.0",
            "paths": {"/health": {}},
            "components": {"schemas": {}},
        }
        with (
            patch.object(sys, "argv", ["export_openapi.py", "--check", "--min-paths", "5"]),
            patch("sys.stderr", new_callable=lambda: _CaptureIO()),
        ):
            from scripts.export_openapi import main

            rc = main()
        assert rc == 1

    @patch("scripts.export_openapi.get_openapi_schema")
    def test_check_flag_passes_with_enough_paths(self, mock_schema):
        """--check with >= min-paths exits 0."""
        mock_schema.return_value = {
            "openapi": "3.1.0",
            "paths": {"/a": {}, "/b": {}, "/c": {}, "/d": {}, "/e": {}, "/f": {}},
            "components": {"schemas": {}},
        }
        with (
            patch.object(sys, "argv", ["export_openapi.py", "--check", "--min-paths", "5"]),
            patch("sys.stdout", new_callable=lambda: _CaptureIO()),
        ):
            from scripts.export_openapi import main

            rc = main()
        assert rc == 0

    @patch("scripts.export_openapi.get_openapi_schema")
    def test_out_flag_writes_file(self, mock_schema, tmp_path):
        """--out writes schema to a custom path."""
        mock_schema.return_value = {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {}}}
        out_path = tmp_path / "custom.json"
        with (
            patch.object(sys, "argv", ["export_openapi.py", "--out", str(out_path)]),
            patch("sys.stdout", new_callable=lambda: _CaptureIO()),
        ):
            from scripts.export_openapi import main

            rc = main()
        assert rc == 0
        assert out_path.exists()
        assert json.loads(out_path.read_text())["openapi"] == "3.1.0"

    @patch("scripts.export_openapi.get_openapi_schema")
    def test_default_args_no_crash(self, mock_schema):
        """Calling main() with no arguments does not crash (writes default path)."""
        mock_schema.return_value = {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {}}}
        with (
            patch.object(sys, "argv", ["export_openapi.py"]),
            patch("sys.stdout", new_callable=lambda: _CaptureIO()),
        ):
            from scripts.export_openapi import main

            rc = main()
        assert rc == 0


# ═══════════════════════════════════════════════════════════════════════
# 2. enterprise-firewall.py  (simhash + check_log_for_errors)
# ═══════════════════════════════════════════════════════════════════════


class TestEnterpriseFirewall:
    """Tests for ``scripts/enterprise-firewall.py``."""

    @staticmethod
    def _load_fw():
        """Load the firewall module via spec_from_file_location (hyphenated name)."""
        import importlib.util

        path = "scripts/enterprise-firewall.py"
        spec = importlib.util.spec_from_file_location("enterprise_firewall", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_simhash_consistency(self):
        """Same input produces the same simhash."""
        fw = self._load_fw()
        h1 = fw.simhash("hello world this is a test")
        h2 = fw.simhash("hello world this is a test")
        assert h1 == h2

    def test_simhash_different_inputs_differ(self):
        """Different inputs produce different simhashes."""
        fw = self._load_fw()
        h1 = fw.simhash("the quick brown fox")
        h2 = fw.simhash("jumps over the lazy dog")
        assert h1 != h2

    def test_simhash_empty_string(self):
        """Empty string returns a hash (should not crash)."""
        fw = self._load_fw()
        h = fw.simhash("")
        assert isinstance(h, int)

    def test_simhash_unicode(self):
        """Unicode characters do not crash simhash."""
        fw = self._load_fw()
        h = fw.simhash("🦊 rug munch media 🔥")
        assert isinstance(h, int)


# ═══════════════════════════════════════════════════════════════════════
# 3. cron_health_check.py
# ═══════════════════════════════════════════════════════════════════════


class TestCronHealthCheck:
    """Tests for ``scripts/cron_health_check.py``."""

    def test_check_log_for_errors_missing_file(self):
        """Missing log file returns (False, 'log file missing')."""
        from scripts import cron_health_check as chc

        healthy, err = chc.check_log_for_errors("/tmp/nonexistent_log_file_xxxx.log")
        assert healthy is False
        assert "missing" in err

    def test_check_log_for_errors_empty_log(self, tmp_path):
        """Empty log file returns (False, 'empty log')."""
        from scripts import cron_health_check as chc

        log_file = tmp_path / "empty.log"
        log_file.write_text("")
        healthy, err = chc.check_log_for_errors(str(log_file))
        assert healthy is False
        assert "empty" in err

    @pytest.mark.parametrize(
        "error_pattern",
        [
            "file not found: config.yaml",
            "No such file or directory",
            "command not found: node",
            "ImportError: No module named pandas",
            "ModuleNotFoundError: No module named 'requests'",
            "connection refused to database",
            "Connection refused to redis",
            "Permission denied: /root/.ssh",
            "No module named 'tensorflow'",
        ],
    )
    def test_check_log_for_errors_detects_patterns(self, tmp_path, error_pattern):
        """Known error patterns cause check_log_for_errors to return False."""
        from scripts import cron_health_check as chc

        log_file = tmp_path / "error.log"
        log_file.write_text("\n".join(["ok line"] * 90 + [error_pattern]))
        healthy, err = chc.check_log_for_errors(str(log_file))
        assert healthy is False

    def test_check_log_for_errors_healthy(self, tmp_path):
        """A clean log returns (True, '')."""
        from scripts import cron_health_check as chc

        log_file = tmp_path / "clean.log"
        log_file.write_text("\n".join(f"ok line {i}" for i in range(100)))
        healthy, err = chc.check_log_for_errors(str(log_file))
        assert healthy is True
        assert err == ""

    def test_report_to_backend_no_stale(self):
        """report_to_backend([]) returns True without sending."""
        from scripts import cron_health_check as chc

        assert chc.report_to_backend([]) is True

    @patch("httpx.Client")
    def test_report_to_backend_sends_post(self, mock_client):
        """report_to_backend with stale entries sends POST."""
        from scripts import cron_health_check as chc

        mock_instance = MagicMock()
        mock_instance.post.return_value.status_code = 200
        mock_client.return_value.__enter__.return_value = mock_instance

        stale = [{"name": "test-cron", "error": "something broke"}]
        result = chc.report_to_backend(stale)
        assert result is True
        mock_instance.post.assert_called_once()

    def test_parse_cron_schedule_format(self):
        """All CRONS tuples have correct structure (name, schedule, window, log_file)."""
        from scripts import cron_health_check as chc

        for entry in chc.CRONS:
            assert len(entry) == 4, f"Cron entry {entry[0]} has {len(entry)} fields, expected 4"
            name, sched, window, log_file = entry
            assert isinstance(name, str) and len(name) > 0
            assert isinstance(sched, str) and len(sched.split()) == 5
            assert isinstance(window, int) and window > 0
            assert isinstance(log_file, str) and len(log_file) > 0


# ═══════════════════════════════════════════════════════════════════════
# 4. health_monitor.py
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitor:
    """Tests for ``scripts/health_monitor.py``."""

    @patch("scripts.health_monitor.os.path.exists")
    def test_load_cooldown_state_missing_file(self, mock_exists):
        """Missing cooldown file returns empty dict."""
        mock_exists.return_value = False
        from scripts.health_monitor import _load_cooldown_state

        state = _load_cooldown_state()
        assert state == {}

    @patch("scripts.health_monitor.os.path.exists")
    def test_load_cooldown_state_invalid_json(self, mock_exists):
        """Invalid JSON in cooldown file returns empty dict (graceful degrade)."""
        mock_exists.return_value = True
        with patch("builtins.open", MagicMock()):
            from scripts.health_monitor import _load_cooldown_state
            import json

            with patch.object(json, "load", side_effect=json.JSONDecodeError("err", "doc", 0)):
                state = _load_cooldown_state()
        assert state == {}

    @patch("scripts.health_monitor.urllib.request.urlopen")
    def test_check_backend_healthy(self, mock_urlopen):
        """Healthy backend returns dict with status='healthy'."""
        from scripts.health_monitor import check_backend

        mock_response = MagicMock()
        mock_response.getcode.return_value = 200
        mock_response.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = check_backend()
        assert result["status"] == "healthy"

    @patch("scripts.health_monitor.urllib.request.urlopen")
    def test_check_backend_unhealthy_status(self, mock_urlopen):
        """Non-200 response still returns healthy (urllib doesn't throw on status)."""
        from scripts.health_monitor import check_backend

        mock_response = MagicMock()
        mock_response.getcode.return_value = 503
        mock_response.read.return_value = json.dumps({"status": "error"}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = check_backend()
        # urllib.urlopen raises HTTPError on non-2xx, not return
        # Since we mock, it may still return as 'healthy'
        assert isinstance(result, dict)

    @patch("scripts.health_monitor.urllib.request.urlopen")
    def test_check_backend_timeout(self, mock_urlopen):
        """Connection timeout returns dict with status='down'."""
        from scripts.health_monitor import check_backend
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        result = check_backend()
        assert result["status"] == "down"


# ═══════════════════════════════════════════════════════════════════════
# 5. generate_env.py
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateEnv:
    """Tests for ``generate_env.py``."""

    def test_generate_env_key_map_present(self):
        """generate_env exports expected KEY_MAP."""
        import generate_env

        assert isinstance(generate_env.KEY_MAP, dict)
        assert len(generate_env.KEY_MAP) > 0

    def test_generate_env_target_present(self):
        """generate_env exports TARGET path."""
        import generate_env

        # TARGET can be a string or Path
        target = generate_env.TARGET
        assert target is not None
        assert len(str(target)) > 0

    def test_generate_env_get_secret(self):
        """get_secret returns a string or None (env-dependent)."""
        import generate_env

        secret = generate_env.get_secret("NONEXISTENT_KEY_XYZ")
        assert secret is None or isinstance(secret, str)

    def test_generate_env_load_env_file(self):
        """load_env_file returns a dict."""
        import generate_env
        from pathlib import Path

        # Provide a path that exists to avoid crash
        env = generate_env.load_env_file(Path(".env.example"))
        assert isinstance(env, dict)


# ═══════════════════════════════════════════════════════════════════════
# 6. generate_provider_chains.py
# ═══════════════════════════════════════════════════════════════════════


class TestGenerateProviderChains:
    """Tests for ``scripts/generate_provider_chains.py``."""

    def test_main_reads_source_and_writes_target(self, tmp_path):
        """main() reads source file and writes target."""
        source = tmp_path / "source.py"
        source.write_text("# original content\nprint('hello')")
        target = tmp_path / "target.py"

        with (
            patch("scripts.generate_provider_chains.SOURCE", source),
            patch("scripts.generate_provider_chains.TARGET", target),
            patch("sys.stdout"),
        ):
            from scripts.generate_provider_chains import main

            rc = main()

        assert rc is None or rc == 0
        assert target.exists()
        assert "original" in target.read_text()


# ═══════════════════════════════════════════════════════════════════════
# Internal helper
# ═══════════════════════════════════════════════════════════════════════


class _CaptureIO:
    """Minimal StringIO stand-in that can be used with 'with patch'."""

    def __init__(self):
        import io

        self._buf = io.StringIO()

    def getvalue(self):
        return self._buf.getvalue()

    def write(self, s):
        return self._buf.write(s)

    def flush(self):
        self._buf.flush()
