"""Unit tests for ``app.deep_scan`` — the deep-scan orchestrator.

Every sub-module call (fraud_gnn / pyod / pygod / heimdall / pyrevm /
slither) is monkeypatched with canned results, so nothing touches ML models,
RPC, or the network. The only real code exercised is ``app.deep_scan``'s own
composition, risk-derivation, and degradation logic.

Covers:

* composition: ``deep_scan_address`` runs all six modules and merges findings;
* risk derivation: per-module severity (0..3) -> ``overall_risk`` max mapping;
* graceful degradation: a raising module becomes an ``error`` finding instead
  of crashing the scan, and a scan with every module failing still returns a
  report with ``overall_risk == "low"``;
* skip paths: PyGOD without ``transfers``, Slither without ``source_code``,
  and non-EVM chains (bytecode + sell-sim skipped);
* EOA / no-code path and dangerous-function decompile signals;
* pure helpers: ``_resolve_chain`` and ``_fetch_code`` (httpx mocked).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app import deep_scan
from app.deep_scan import DeepScanReport, deep_scan_address
from app.pyrevm_engine import SellSimulationResult

EVM_ADDR = "0x" + "11" * 20
SOLANA_ADDR = "So11111111111111111111111111111111111111111"
CONTRACT_CODE = "0x6080604052348015600f57600080fd5b50"

# Canned fingerprint — keys do not matter (ML modules are stubbed).
FINGERPRINT: dict[str, float] = {"txn_count_30d": 0.0, "volume_30d": 0.0, "interacted_count": 0.0}


# ── Canned-result builders ──────────────────────────────────────────────────


def _anomaly_result(score: float = 0.0, is_anomaly: bool = False, model_type: str = "stub") -> SimpleNamespace:
    """A pyod/pygod ``AnomalyResult``-shaped stub (has ``to_dict``)."""
    return SimpleNamespace(
        anomaly_score=score,
        is_anomaly=is_anomaly,
        model_type=model_type,
        contributing_features=[],
        to_dict=lambda: {
            "anomaly_score": score,
            "is_anomaly": is_anomaly,
            "contributing_features": [],
            "model_type": model_type,
        },
    )


def _decompiled(success: bool = True, functions: list[dict[str, Any]] | None = None, **extra: Any) -> SimpleNamespace:
    """A heimdall ``DecompiledContract``-shaped stub."""
    kwargs: dict[str, Any] = {
        "success": success,
        "functions": functions
        if functions is not None
        else [{"name": "transfer", "signature": "transfer(address,uint256)"}],
        "events": [],
        "errors": [],
        "storage_slots": [],
        "decompile_error": None,
        "storage_error": None,
    }
    kwargs.update(extra)
    obj = SimpleNamespace(**kwargs)

    def to_dict() -> dict[str, Any]:
        return {
            "success": obj.success,
            "functions": obj.functions,
            "events": obj.events,
            "errors": obj.errors,
            "storage_slots": obj.storage_slots,
            "decompile_error": obj.decompile_error,
            "storage_error": obj.storage_error,
        }

    obj.to_dict = to_dict
    return obj


def _sell_result(**overrides: Any) -> SellSimulationResult:
    """A real ``SellSimulationResult`` (dataclass — ``asdict`` requires it)."""
    fields: dict[str, Any] = {
        "token_address": EVM_ADDR,
        "chain": "ethereum",
        "simulation_success": True,
        "can_sell": True,
        "can_buy": True,
        "sell_to_pair_reverted": False,
        "input_amount": 1000,
        "output_amount": 900,
        "tax_pct": 0.1,
        "sell_tax_pct": 0.1,
        "buy_tax_pct": 0.0,
        "all_transfers_revert": False,
        "blacklist_hit": False,
        "anti_whale_hit": False,
        "revert_reason": None,
        "balance_slot": None,
        "pair_address": None,
        "warnings": [],
        "risk": "safe",
    }
    fields.update(overrides)
    return SellSimulationResult(**fields)


def _slither_result(
    success: bool = True, detectors: list[dict[str, Any]] | None = None, error: str | None = None
) -> SimpleNamespace:
    """A slither ``SlitherResult``-shaped stub."""
    dets = detectors or []
    return SimpleNamespace(
        success=success,
        detectors=dets,
        error=error,
        solc_version=None,
        to_dict=lambda: {"success": success, "detectors": dets, "error": error, "solc_version": None},
    )


# ── Fixture: install default (all-safe) sub-module stubs ────────────────────


@pytest.fixture
def stub_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every sub-module call with canned results (all default to safe/level-0)."""
    monkeypatch.setattr(deep_scan, "_build_fingerprint", lambda address, chain: dict(FINGERPRINT))
    monkeypatch.setattr(deep_scan, "_fetch_code", lambda rpc_urls, address, timeout: CONTRACT_CODE)

    monkeypatch.setattr(
        "app.fraud_gnn.get_fraud_gnn",
        lambda: SimpleNamespace(predict=lambda fingerprint: (0.1, False, "stub-model")),
    )
    monkeypatch.setattr(
        "app.pyod_anomaly.get_pyod_anomaly",
        lambda: SimpleNamespace(
            score=lambda features: _anomaly_result(score=0.1, is_anomaly=False, model_type="stub-pyod")
        ),
    )
    monkeypatch.setattr(
        "app.pygod_anomaly.get_pygod_anomaly",
        lambda: SimpleNamespace(
            score=lambda graph: _anomaly_result(score=0.1, is_anomaly=False, model_type="stub-pygod")
        ),
    )
    monkeypatch.setattr(
        "app.heimdall_bytecode.analyze_bytecode",
        lambda code, **kwargs: _decompiled(success=True),
    )
    monkeypatch.setattr(
        "app.pyrevm_engine.simulate_sell_round_trip",
        lambda address, chain="ethereum": _sell_result(),
    )
    monkeypatch.setattr(
        "app.slither_analyzer.analyze_source",
        lambda source, solc_version=None: _slither_result(success=True, detectors=[]),
    )
    monkeypatch.setattr("app.slither_analyzer.is_security_relevant", lambda check: True)
    # Honeypot sell-sim check (Honeypot.is / GoPlus) — stub to avoid network.
    monkeypatch.setattr(
        "app.deep_scan._run_honeypot_sell_sim",
        lambda address, chain="ethereum": (
            {
                "check": "honeypot_sell_sim",
                "result": "scanned",
                "source": "goplus",
                "sell_sim_verdict": "OK",
                "is_honeypot": False,
                "buy_tax": 0.0,
                "sell_tax": 0.0,
                "transfer_tax": None,
                "warnings": [],
            },
            [{"module": "honeypot_sell_sim", "level": "info", "message": "honeypot sell-sim verdict=OK"}],
            0,
        ),
    )


# ── Composition ─────────────────────────────────────────────────────────────


def test_report_schema_evm_all_safe(stub_modules: None) -> None:
    report = deep_scan_address(EVM_ADDR)

    assert isinstance(report, DeepScanReport)
    assert report.address == EVM_ADDR
    assert report.chain == "ethereum"
    assert report.overall_risk == "low"

    scores = report.risk_scores
    assert set(scores) == {"fraud_gnn", "pyod", "pygod", "decompiled", "sell_sim", "slither", "overall_level"}
    assert scores["fraud_gnn"]["fraud_probability"] == pytest.approx(0.1)
    assert scores["fraud_gnn"]["risk_level"] == 0
    assert scores["pyod"]["model_type"] == "stub-pyod"
    assert scores["sell_sim"]["risk_level"] == 0
    assert scores["slither"]["skipped"] == "no verified source provided"

    # Decompiled ABI success surfaced.
    assert report.decompiled["success"] is True
    assert report.anomaly["fraud_gnn"]["is_fraud"] is False


def test_report_to_dict(stub_modules: None) -> None:
    report = deep_scan_address(EVM_ADDR)
    payload = report.to_dict()
    assert payload["address"] == EVM_ADDR
    assert payload["overall_risk"] == "low"
    assert "findings" in payload
    assert "risk_scores" in payload


def test_chain_resolution() -> None:
    assert deep_scan._resolve_chain(EVM_ADDR, "auto") == "ethereum"
    assert deep_scan._resolve_chain(SOLANA_ADDR, "auto") == "solana"
    assert deep_scan._resolve_chain(EVM_ADDR, "bsc") == "bsc"
    assert deep_scan._resolve_chain("", "") == "solana"


# ── Risk derivation ─────────────────────────────────────────────────────────


def test_fraud_high_drives_overall_risk(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.fraud_gnn.get_fraud_gnn",
        lambda: SimpleNamespace(predict=lambda fingerprint: (0.9, True, "stub-model")),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["fraud_gnn"]["risk_level"] == 2
    assert report.overall_risk == "high"
    assert any("fraud probability 0.9000" in f["message"] for f in report.findings)


def test_fraud_medium_threshold(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.fraud_gnn.get_fraud_gnn",
        lambda: SimpleNamespace(predict=lambda fingerprint: (0.5, False, "stub-model")),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["fraud_gnn"]["risk_level"] == 1
    assert report.overall_risk == "medium"


def test_pyod_anomaly_drives_overall_risk(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pyod_anomaly.get_pyod_anomaly",
        lambda: SimpleNamespace(
            score=lambda features: _anomaly_result(score=0.95, is_anomaly=True, model_type="stub-pyod")
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["pyod"]["risk_level"] == 2
    assert report.overall_risk == "high"


def test_pygod_anomaly_with_transfers(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pygod_anomaly.get_pygod_anomaly",
        lambda: SimpleNamespace(
            score=lambda graph: _anomaly_result(score=0.99, is_anomaly=True, model_type="stub-pygod")
        ),
    )
    transfers = [{"from_addr": "0x" + "aa" * 20, "to_addr": "0x" + "bb" * 20}]
    report = deep_scan_address(EVM_ADDR, transfers=transfers)
    assert report.risk_scores["pygod"]["is_anomaly"] is True
    assert report.risk_scores["pygod"]["risk_level"] == 2
    assert report.overall_risk == "high"


def test_sell_sim_critical_drives_overall_risk(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pyrevm_engine.simulate_sell_round_trip",
        lambda address, chain="ethereum": _sell_result(risk="critical", simulation_success=True, tax_pct=0.5),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["sell_sim"]["risk_level"] == 3
    assert report.overall_risk == "critical"


def test_sell_sim_honeypot_extra_finding(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.pyrevm_engine.simulate_sell_round_trip",
        lambda address, chain="ethereum": _sell_result(
            can_sell=True, sell_to_pair_reverted=True, simulation_success=True
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    assert any("honeypot" in f["message"] for f in report.findings)


def test_decompile_dangerous_function_names(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.heimdall_bytecode.analyze_bytecode",
        lambda code, **kwargs: _decompiled(
            success=True,
            functions=[
                {"name": "transfer", "signature": "transfer(address,uint256)"},
                {"name": "emergencyWithdraw", "signature": "emergencyWithdraw()"},
            ],
        ),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["decompiled"]["risk_level"] == 1
    dangerous_findings = [f for f in report.findings if "dangerous function names" in f["message"]]
    assert dangerous_findings
    assert "emergencyWithdraw" in dangerous_findings[0]["message"]  # original-case names joined


def test_decompile_incomplete_warns(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.heimdall_bytecode.analyze_bytecode",
        lambda code, **kwargs: _decompiled(success=False, decompile_error="panik"),
    )
    report = deep_scan_address(EVM_ADDR)
    assert report.risk_scores["decompiled"]["success"] is False
    assert report.risk_scores["decompiled"]["risk_level"] == 0
    assert any("decompile incomplete" in f["message"] for f in report.findings)


def test_slither_high_impact(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.slither_analyzer.analyze_source",
        lambda source, solc_version=None: _slither_result(
            success=True, detectors=[{"check": "unprotected-withdraw", "impact": "High", "description": "x"}]
        ),
    )
    monkeypatch.setattr("app.slither_analyzer.is_security_relevant", lambda check: check == "unprotected-withdraw")
    report = deep_scan_address(EVM_ADDR, source_code="pragma solidity ^0.8.0; contract C {}")
    assert report.risk_scores["slither"]["risk_level"] == 2
    assert any("high-impact security-relevant" in f["message"] for f in report.findings)
    assert report.overall_risk == "high"


def test_slither_medium_impact(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.slither_analyzer.analyze_source",
        lambda source, solc_version=None: _slither_result(
            success=True, detectors=[{"check": "assembly", "impact": "Medium", "description": "x"}]
        ),
    )
    monkeypatch.setattr("app.slither_analyzer.is_security_relevant", lambda check: True)
    report = deep_scan_address(EVM_ADDR, source_code="pragma solidity ^0.8.0; contract C {}")
    assert report.risk_scores["slither"]["risk_level"] == 1
    assert report.overall_risk == "medium"


# ── Skip paths ──────────────────────────────────────────────────────────────


def test_pygod_skipped_without_transfers(stub_modules: None) -> None:
    report = deep_scan_address(EVM_ADDR, transfers=None)
    assert report.risk_scores["pygod"]["model_type"] == "skipped"
    assert any("PyGOD skipped" in f["message"] for f in report.findings)


def test_slither_skipped_without_source(stub_modules: None) -> None:
    report = deep_scan_address(EVM_ADDR, source_code=None)
    assert report.risk_scores["slither"]["skipped"] == "no verified source provided"
    assert report.risk_scores["slither"]["risk_level"] == 0


def test_solana_skips_evm_only_modules(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fetch_called: list[bool] = []

    def spy_fetch(*args: Any, **kwargs: Any) -> str:
        fetch_called.append(True)
        return CONTRACT_CODE

    monkeypatch.setattr(deep_scan, "_fetch_code", spy_fetch)
    report = deep_scan_address(SOLANA_ADDR)

    assert report.chain == "solana"
    assert not fetch_called, "_fetch_code must not run for non-EVM chains"
    assert "not supported for chain" in report.decompiled["decompile_error"]
    assert report.risk_scores["sell_sim"]["warnings"] == ["sell simulation skipped: chain is not EVM"]
    # ML modules still run for Solana.
    assert report.risk_scores["fraud_gnn"]["risk_level"] == 0


def test_eoa_no_code_path(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    analyze_called: list[bool] = []

    def spy_analyze(code: str, **kwargs: Any) -> Any:
        analyze_called.append(True)
        return _decompiled(success=True)

    monkeypatch.setattr("app.heimdall_bytecode.analyze_bytecode", spy_analyze)
    monkeypatch.setattr(deep_scan, "_fetch_code", lambda rpc_urls, address, timeout: "0x")
    report = deep_scan_address(EVM_ADDR)

    assert not analyze_called, "analyze_bytecode must not run when there is no code"
    assert "no code at address" in report.decompiled["decompile_error"]
    assert any("no contract code at address" in f["message"] for f in report.findings)


# ── Graceful degradation ────────────────────────────────────────────────────


def test_module_exception_becomes_error_finding(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("model load failed")

    monkeypatch.setattr("app.fraud_gnn.get_fraud_gnn", boom)
    report = deep_scan_address(EVM_ADDR)

    assert report.risk_scores["fraud_gnn"]["error"] == "model load failed"
    assert report.risk_scores["fraud_gnn"]["risk_level"] == 0
    assert any("fraud_gnn failed: model load failed" in f["message"] for f in report.findings)
    # Other modules still contribute.
    assert report.risk_scores["pyod"]["risk_level"] == 0
    assert report.overall_risk == "low"


def test_every_module_failing_still_returns_report(stub_modules: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.fraud_gnn.get_fraud_gnn", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("app.pyod_anomaly.get_pyod_anomaly", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("app.pygod_anomaly.get_pygod_anomaly", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(
        "app.heimdall_bytecode.analyze_bytecode", lambda code, **kw: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(
        "app.pyrevm_engine.simulate_sell_round_trip",
        lambda address, chain="ethereum": (_ for _ in ()).throw(RuntimeError("x")),
    )
    monkeypatch.setattr(
        "app.slither_analyzer.analyze_source",
        lambda source, solc_version=None: (_ for _ in ()).throw(RuntimeError("x")),
    )

    report = deep_scan_address(EVM_ADDR, transfers=[{"from_addr": "0x" + "aa" * 20, "to_addr": "0x" + "bb" * 20}])

    assert report.overall_risk == "low"
    error_findings = [f for f in report.findings if f["level"] == "error"]
    assert len(error_findings) >= 5  # fraud, pyod, pygod, heimdall, pyrevm, slither
    assert report.risk_scores["fraud_gnn"]["risk_level"] == 0


def test_fingerprint_adapter_failure_falls_back_to_zeros(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(address: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("adapter down")

    monkeypatch.setattr("app.ml_datasets.adapters.address_to_fingerprint", boom)
    fp = deep_scan._build_fingerprint(EVM_ADDR, "ethereum")
    assert isinstance(fp, dict)
    assert fp
    assert all(isinstance(v, float) and v == 0.0 for v in fp.values())


# ── _fetch_code (httpx mocked) ──────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error

    def json(self) -> dict[str, Any]:
        return self._payload or {}


class _FakeClient:
    def __init__(self, payloads: list[dict[str, Any] | None], errors: list[Exception | None]) -> None:
        self._payloads = payloads
        self._errors = errors
        self.calls: list[tuple[Any, Any]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        idx = len(self.calls)
        self.calls.append((url, json))
        if idx < len(self._errors) and self._errors[idx] is not None:
            return _FakeResponse(error=self._errors[idx])
        return _FakeResponse(payload=self._payloads[idx] if idx < len(self._payloads) else None)


def test_fetch_code_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(payloads=[{"jsonrpc": "2.0", "result": CONTRACT_CODE}], errors=[])
    monkeypatch.setattr(deep_scan.httpx, "Client", lambda **kwargs: client)
    assert deep_scan._fetch_code(["https://rpc.example.invalid"], EVM_ADDR, 5.0) == CONTRACT_CODE
    assert client.calls[0][1]["method"] == "eth_getCode"


def test_fetch_code_skips_rpc_error_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(payloads=[{"jsonrpc": "2.0", "error": {"code": -32000}}], errors=[])
    monkeypatch.setattr(deep_scan.httpx, "Client", lambda **kwargs: client)
    assert deep_scan._fetch_code(["https://rpc.example.invalid"], EVM_ADDR, 5.0) == ""


def test_fetch_code_all_urls_fail_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(payloads=[], errors=[httpx.ConnectError("down"), httpx.ConnectError("down")])
    monkeypatch.setattr(deep_scan.httpx, "Client", lambda **kwargs: client)
    assert deep_scan._fetch_code(["https://a.invalid", "https://b.invalid"], EVM_ADDR, 5.0) == ""
