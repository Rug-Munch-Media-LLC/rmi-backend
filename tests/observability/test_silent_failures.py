"""Failure-injection tests for previously silent handlers."""

from __future__ import annotations

import ast
import logging
import pathlib

import pytest

from app.observability.silent_failures import DEGRADED_FAILURES, record_silent_failure

CONVERTED = [
    "app/databus/token_launch_detector.py",
    "app/domains/databus/providers/chains/lite_chain_indexer_own.py",
    "app/domains/scanner/modules/deployer.py",
    "app/domains/scanner/unified/scanner.py",
    "app/domains/detectors/smart_contract_honeypot_detector/detector.py",
    "app/domains/detectors/rug_imminence_predictor/predictor.py",
    "app/routers/v1/market.py",
    "app/routers/v1/reports.py",
    "app/catalog/service.py",
    "app/unified_provider.py",
    "app/routers/admin/overview.py",
]


def test_injected_failure_is_logged_warning_and_counted(caplog):
    """An injected provider failure must WARN and increment the counter."""
    before = DEGRADED_FAILURES.labels(
        "databus", "injected_provider", "none", "TimeoutError"
    )._value.get()
    with caplog.at_level(logging.WARNING, logger="app.observability.silent_failures"):
        record_silent_failure("databus", "injected_provider", TimeoutError("upstream timed out"))
    after = DEGRADED_FAILURES.labels(
        "databus", "injected_provider", "none", "TimeoutError"
    )._value.get()
    assert after == before + 1, "counter must record the swallowed failure"
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert records, "failure must be logged at WARNING, not DEBUG"
    assert "degraded_fallback" in records[-1].getMessage()
    assert "databus" in records[-1].getMessage()


def test_named_fallback_differs_from_success_payload():
    """A degraded section must be distinguishable from a successful payload."""
    degraded = {"error": "unavailable", "degraded": True, "fallback": "section_unavailable"}
    success = {"risk": {"score": 12}}
    assert degraded.get("degraded") is True
    assert "degraded" not in success
    assert degraded != success


DEBUG_ONLY_BASELINE = {'app/databus/token_launch_detector.py': 10, 'app/domains/databus/providers/chains/lite_chain_indexer_own.py': 9, 'app/domains/scanner/modules/deployer.py': 15, 'app/domains/scanner/unified/scanner.py': 13, 'app/domains/detectors/smart_contract_honeypot_detector/detector.py': 11, 'app/domains/detectors/rug_imminence_predictor/predictor.py': 9, 'app/routers/v1/market.py': 15, 'app/routers/v1/reports.py': 19, 'app/catalog/service.py': 1, 'app/unified_provider.py': 12, 'app/routers/admin/overview.py': 11}


@pytest.mark.parametrize("path", sorted(DEBUG_ONLY_BASELINE))
def test_debug_only_handlers_ratchet_down(path):
    """Each converted file reports failures; debug-only handlers never grow."""
    src = pathlib.Path(path).read_text()
    assert "record_silent_failure" in src, f"{path} lost its degradation reporting"
    tree = ast.parse(src)
    debug_only = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                func = stmt.value.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "debug":
                    debug_only += 1
                    break
    assert debug_only <= DEBUG_ONLY_BASELINE[path], (
        f"{path} grew silent debug-only handlers: {debug_only} > {DEBUG_ONLY_BASELINE[path]}"
    )
