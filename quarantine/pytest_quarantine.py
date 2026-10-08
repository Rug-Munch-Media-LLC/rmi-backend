"""Quarantine mechanism for flaky tests.

Loads the flaky registry and dynamically applies pytest.mark.flaky markers.
This keeps the quarantine honest: quarantined tests still RUN, failures are tracked,
and we get a CI report showing which tests were rerun/quarantined.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

# Path to the flaky registry
QUARANTINE_REGISTRY = Path(__file__).parent / "flaky_registry.yml"


def load_flaky_registry() -> dict[str, Any]:
    """Load the flaky test registry from YAML."""
    if not QUARANTINE_REGISTRY.exists():
        return {"entries": [], "meta": {}}
    with QUARANTINE_REGISTRY.open() as f:
        return yaml.safe_load(f)


def pytest_configure(config):
    """Register the quarantine marker and add custom reporting."""
    config.addinivalue_line(
        "markers",
        "quarantined: marks a test as known-flaky with rerun configuration from quarantine/flaky_registry.yml",
    )
    config.addinivalue_line("markers", "flaky: marks a test as flaky (pytest-rerunfailures marker)")


def pytest_collection_modifyitems(config, items):
    """Apply flaky markers from registry to collected test items."""
    registry = load_flaky_registry()
    flaky_map = {entry["test_id"]: entry for entry in registry.get("entries", [])}

    for item in items:
        test_id = item.nodeid
        if test_id in flaky_map:
            entry = flaky_map[test_id]
            rerun_config = entry.get("rerun_config", {})
            # Apply the pytest-rerunfailures marker dynamically
            marker = pytest.mark.flaky(**rerun_config)
            item.add_marker(marker)
            # Also add our custom quarantine marker for reporting
            item.add_marker(
                pytest.mark.quarantined(
                    owner=entry.get("owner", "unknown"),
                    reason=entry.get("reason", "no reason provided"),
                    review_date=entry.get("review_date", "unknown"),
                )
            )


def pytest_runtest_makereport(item, call):
    """Track flaky test outcomes for CI reporting."""
    # This hook runs after each test phase (setup, call, teardown)
    if hasattr(item, "_test_failed_statuses"):
        failed_statuses = item._test_failed_statuses
    else:
        failed_statuses = {"setup": False, "call": False, "teardown": False}

    if call.when == "call":
        if call.excinfo is not None:
            failed_statuses["call"] = True
    elif call.when == "setup":
        if call.excinfo is not None:
            failed_statuses["setup"] = True
    elif call.when == "teardown":
        if call.excinfo is not None:
            failed_statuses["teardown"] = True

    item._test_failed_statuses = failed_statuses


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Add quarantine summary to terminal output."""
    quarantined_tests = []
    rerun_tests = []

    # Collect quarantined test results
    for test_result in terminalreporter.stats.get("passed", []) + terminalreporter.stats.get("failed", []):
        for marker in test_result.keywords:
            if marker == "quarantined":
                quarantined_tests.append(
                    {
                        "nodeid": test_result.nodeid,
                        "outcome": test_result.outcome,
                        "owner": getattr(test_result, "quarantined_owner", "unknown"),
                        "reason": getattr(test_result, "quarantined_reason", "unknown"),
                    }
                )
            elif marker == "flaky":
                # Check if this was a rerun
                if hasattr(test_result, "_test_failed_statuses"):
                    rerun_tests.append(
                        {
                            "nodeid": test_result.nodeid,
                            "outcome": test_result.outcome,
                        }
                    )

    if quarantined_tests or rerun_tests:
        terminalreporter.write_sep("=", "QUARANTINE SUMMARY")
        if quarantined_tests:
            terminalreporter.write_line(f"Quarantined tests run: {len(quarantined_tests)}")
            for qt in quarantined_tests:
                terminalreporter.write_line(
                    f"  {qt['nodeid']} -> {qt['outcome']} (owner: {qt['owner']}, reason: {qt['reason']})"
                )
        if rerun_tests:
            terminalreporter.write_line(f"Rerun tests: {len(rerun_tests)}")
            for rt in rerun_tests:
                terminalreporter.write_line(f"  {rt['nodeid']} -> {rt['outcome']}")
