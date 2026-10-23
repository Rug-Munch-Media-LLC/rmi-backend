"""Focused tests for fail-open except paths in scan enrichment.

Debt fix: signal-renderer exceptions, enrich-scan budget timeouts, and
rug-risk verdict exceptions never block or fail a scan.
"""

from __future__ import annotations

from unittest import mock

from app.domains.scanner.scan_enrich import _render, enrich_scan


def _scan_result() -> dict:
    return {"address": "0xabc", "chain": "ethereum"}


def test_render_fail_open_when_renderer_raises() -> None:
    def boom(result):
        raise ValueError("render broken")

    assert _render(boom, {"foo": 1}) is None


def test_render_fail_open_when_renderer_returns_non_string() -> None:
    assert _render(lambda result: 42, {"foo": 1}) == "42"
    assert _render(lambda result: None, {"foo": 1}) is None
    assert _render(lambda result: "", {"foo": 1}) is None


async def test_enrich_scan_fail_open_on_timeout() -> None:
    """asyncio.wait_for budget exceeded -> scan result returned unchanged."""
    scan_result = _scan_result()
    with mock.patch(
        "app.domains.scanner.scan_enrich.asyncio.wait_for",
        side_effect=TimeoutError("budget exceeded"),
    ):
        out = await enrich_scan(dict(scan_result), "0xabc", "ethereum")
    assert out == scan_result
    assert "risk_signals" not in out


async def test_enrich_scan_fail_open_when_verdict_raises() -> None:
    """rug_risk_score succeeds but risk_verdict_text raises -> score kept,
    verdict omitted, scan still returns."""
    scan_result = _scan_result()
    with (
        mock.patch(
            "app.domains.scanner.scan_enrich.asyncio.wait_for",
            new=mock.AsyncMock(return_value=(None,) * 6),
        ),
        mock.patch(
            "app.domains.scanner.scan_enrich.rug_risk_score",
            return_value={"score": 7},
        ),
        mock.patch(
            "app.domains.scanner.scan_enrich.risk_verdict_text",
            side_effect=RuntimeError("verdict broken"),
        ),
    ):
        out = await enrich_scan(dict(scan_result), "0xabc", "ethereum")
    assert out["risk_signals"]["rug_risk_score"] == {"score": 7}
    assert "risk_verdict_text" not in out["risk_signals"]
