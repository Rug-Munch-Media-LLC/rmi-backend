"""Unit tests for the deep-scan honeypot sell-sim check.

Covers ``app.deep_scan_runners._run_honeypot_sell_sim`` (Honeypot.is primary,
GoPlus fallback; OK / BLOCKED / EXTREME_TAXES / unavailable verdicts) and the
``deep_scan_address`` merge (namespaced keys, pyrevm fields preserved, risk
derivation). No real network — httpx and the sub-module runners are stubbed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import deep_scan
from app.deep_scan import DeepScanReport, deep_scan_address
from app.deep_scan_runners import _run_honeypot_sell_sim

EVM_ADDR = "0x" + "11" * 20
SOLANA_ADDR = "So11111111111111111111111111111111111111111"


# ── httpx stubs ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, json_data: Any) -> None:
        self.status_code = status_code
        self._json = json_data

    def json(self) -> Any:
        return self._json


class _FakeClient:
    """Minimal httpx.Client stub — routes by URL substring."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, params))
        for needle, resp in self._responses.items():
            if needle in url:
                return resp
        return _FakeResponse(404, {})


def _install_http(monkeypatch: pytest.MonkeyPatch, responses: dict[str, _FakeResponse]) -> _FakeClient:
    client = _FakeClient(responses)
    monkeypatch.setattr("app.deep_scan_runners.httpx.Client", lambda *a, **k: client)
    return client


def _honeypot_is_payload(is_honeypot: bool = False, buy_tax: str = "0.01", sell_tax: str = "0.02") -> dict[str, Any]:
    return {
        "honeypotResult": {
            "isHoneypot": is_honeypot,
            "simulationResult": {
                "isHoneypot": is_honeypot,
                "buyTax": buy_tax,
                "sellTax": sell_tax,
                "transferTax": "0",
            },
        },
        "pair": {},
    }


def _goplus_payload(address: str, is_honeypot: str = "0", buy_tax: str = "0", sell_tax: str = "0") -> dict[str, Any]:
    return {
        "result": {
            address.lower(): {
                "is_honeypot": is_honeypot,
                "buy_tax": buy_tax,
                "sell_tax": sell_tax,
                "transfer_pausable": "0",
            }
        }
    }


# ── Runner verdicts ─────────────────────────────────────────────────────────


def test_ok_via_honeypot_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_http(monkeypatch, {"api.honeypot.is": _FakeResponse(200, _honeypot_is_payload())})
    result, findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["result"] == "scanned"
    assert result["source"] == "honeypot.is"
    assert result["sell_sim_verdict"] == "OK"
    assert result["buy_tax"] == pytest.approx(0.01)
    assert result["sell_tax"] == pytest.approx(0.02)
    assert result["transfer_tax"] == pytest.approx(0.0)
    assert result["is_honeypot"] is False
    assert level == 0
    assert any("verdict=OK" in f["message"] for f in findings)


def test_blocked_via_honeypot_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_http(monkeypatch, {"api.honeypot.is": _FakeResponse(200, _honeypot_is_payload(is_honeypot=True))})
    result, _findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["sell_sim_verdict"] == "BLOCKED"
    assert level == 3  # critical


def test_extreme_taxes_via_honeypot_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_http(monkeypatch, {"api.honeypot.is": _FakeResponse(200, _honeypot_is_payload(sell_tax="30"))})
    result, _findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["sell_sim_verdict"] == "EXTREME_TAXES"
    assert level == 2  # high


def test_goplus_fallback_when_honeypot_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_http(
        monkeypatch,
        {
            "api.honeypot.is": _FakeResponse(500, {}),
            "api.gopluslabs.io": _FakeResponse(200, _goplus_payload(EVM_ADDR, sell_tax="3")),
        },
    )
    result, _findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["source"] == "goplus"
    assert result["sell_sim_verdict"] == "OK"
    assert result["sell_tax"] == pytest.approx(3.0)
    assert result["transfer_tax"] is None  # GoPlus exposes no transfer tax
    assert level == 0
    assert any("goplus" in url for url, _ in client.calls)


def test_goplus_blocked_flags_honeypot(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_http(
        monkeypatch,
        {
            "api.honeypot.is": _FakeResponse(500, {}),
            "api.gopluslabs.io": _FakeResponse(200, _goplus_payload(EVM_ADDR, is_honeypot="1")),
        },
    )
    result, _findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["source"] == "goplus"
    assert result["sell_sim_verdict"] == "BLOCKED"
    assert level == 3


def test_unavailable_when_all_sources_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_http(
        monkeypatch,
        {
            "api.honeypot.is": _FakeResponse(500, {}),
            "api.gopluslabs.io": _FakeResponse(500, {}),
        },
    )
    result, _findings, level = _run_honeypot_sell_sim(EVM_ADDR, "ethereum")
    assert result["result"] == "unavailable"
    assert result["sell_sim_verdict"] == "unavailable"
    assert result["buy_tax"] is None
    assert level == 0
    assert result["warnings"]


def test_solana_uses_goplus_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _install_http(
        monkeypatch,
        {"api.gopluslabs.io": _FakeResponse(200, _goplus_payload(SOLANA_ADDR, sell_tax="5"))},
    )
    result, _findings, level = _run_honeypot_sell_sim(SOLANA_ADDR, "solana")
    assert result["source"] == "goplus"
    assert result["sell_sim_verdict"] == "OK"
    assert level == 0
    # Honeypot.is must NOT be called for Solana (EVM-only endpoint).
    assert all("honeypot.is" not in url for url, _ in client.calls)


# ── Orchestrator merge + risk derivation ────────────────────────────────────


@pytest.fixture
def stub_all_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every deep-scan sub-module to safe results (no network/ML)."""

    def _safe_sell_sim(address: str, chain: str = "ethereum") -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        return (
            {
                "simulation_success": True,
                "can_sell": True,
                "tax_pct": 0.1,
                "risk": "safe",
                "warnings": [],
            },
            [],
            0,
        )

    monkeypatch.setattr(deep_scan, "_run_fraud_gnn", lambda fingerprint: ({"fraud_probability": 0.1}, [], 0))
    monkeypatch.setattr(deep_scan, "_run_pyod", lambda fingerprint: ({"anomaly_score": 0.1}, [], 0))
    monkeypatch.setattr(deep_scan, "_run_pygod", lambda transfers: ({"model_type": "skipped"}, [], 0))
    monkeypatch.setattr(deep_scan, "_run_decompile", lambda code: ({"success": False}, [], 0))
    monkeypatch.setattr(deep_scan, "_run_sell_sim", _safe_sell_sim)
    monkeypatch.setattr(deep_scan, "_run_slither", lambda source, solc: ({"success": False}, [], 0))
    monkeypatch.setattr(deep_scan, "_fetch_code", lambda rpc_urls, address, timeout: "0x6080")
    monkeypatch.setattr(deep_scan, "_build_fingerprint", lambda address, chain: {})


def test_merge_keeps_pyrevm_fields_and_namespaced_honeypot(
    stub_all_safe: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        deep_scan,
        "_run_honeypot_sell_sim",
        lambda address, chain="ethereum": (
            {
                "sell_sim_verdict": "OK",
                "buy_tax": 0.01,
                "sell_tax": 0.02,
                "source": "goplus",
                "warnings": [],
            },
            [{"module": "honeypot_sell_sim", "level": "info", "message": "verdict=OK"}],
            0,
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    sell = report.sell_sim
    # pyrevm fields preserved…
    assert sell["simulation_success"] is True
    assert sell["tax_pct"] == pytest.approx(0.1)
    # …honeypot check merged with namespaced keys…
    assert sell["sell_sim_verdict"] == "OK"
    assert sell["buy_tax"] == pytest.approx(0.01)
    assert sell["sell_tax"] == pytest.approx(0.02)
    # …warnings combined (both empty here) and risk level unchanged.
    assert report.risk_scores["sell_sim"]["risk_level"] == 0
    assert report.overall_risk == "low"


def test_blocked_honeypot_drives_overall_risk(stub_all_safe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deep_scan,
        "_run_honeypot_sell_sim",
        lambda address, chain="ethereum": (
            {"sell_sim_verdict": "BLOCKED", "is_honeypot": True, "buy_tax": 0.0, "sell_tax": 0.0, "warnings": []},
            [{"module": "honeypot_sell_sim", "level": "warning", "message": "honeypot sell-sim verdict=BLOCKED"}],
            3,
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.sell_sim["sell_sim_verdict"] == "BLOCKED"
    assert report.risk_scores["sell_sim"]["risk_level"] == 3
    assert report.overall_risk == "critical"


def test_unavailable_honeypot_is_fail_open(stub_all_safe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deep_scan,
        "_run_honeypot_sell_sim",
        lambda address, chain="ethereum": (
            {
                "result": "unavailable",
                "sell_sim_verdict": "unavailable",
                "buy_tax": None,
                "sell_tax": None,
                "warnings": ["data source unreachable"],
            },
            [{"module": "honeypot_sell_sim", "level": "info", "message": "unavailable"}],
            0,
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.sell_sim["sell_sim_verdict"] == "unavailable"
    assert report.overall_risk == "low"
    assert isinstance(report, DeepScanReport)
