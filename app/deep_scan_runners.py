"""Guarded sub-module runners for :mod:`app.deep_scan`.

Each runner executes one of the platform's ML / contract-analysis modules
(fraud_gnn, pyod, pygod, heimdall_bytecode, pyrevm_engine, slither_analyzer)
inside ``try/except`` so a missing optional dependency or module failure
becomes an ``error``-level finding instead of crashing the scan.

Contract: every runner returns ``(result_dict, findings, severity_level)``
where ``severity_level`` is ``0..3`` (low/medium/high/critical) and feeds the
aggregate risk derivation in :func:`app.deep_scan.deep_scan_address`. No
runner raises for module-level failures.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from app.chain_registry import is_evm
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

# ── Runner tunables ───────────────────────────────────────────────────────────

DEFAULT_DECOMPILE_TIMEOUT_SECS = 25

# pyrevm sell-simulation risk labels -> severity level.
SELL_RISK_LEVELS: dict[str, int] = {
    "critical": 3,
    "dangerous": 2,
    "warning": 1,
    "safe": 0,
    "unknown": 0,
}

# Function-name substrings that indicate rug/exit-scam capability in a
# decompiled ABI (self-contained subset of the scanner's known patterns).
DANGEROUS_FUNCTION_PATTERNS: tuple[str, ...] = (
    "withdraw",
    "drain",
    "setowner",
    "emergencywithdraw",
    "pullfunds",
    "takefunds",
    "sweepfunds",
    "rescuetokens",
    "takeownership",
    "forcewithdraw",
    "manualwithdraw",
    "adminwithdraw",
    "ownerwithdraw",
    "setswapandliquify",
    "settradingenabled",
    "setcooldownenabled",
    "blacklist",
    "setexcludedfromfee",
    "setmaxbuyamount",
    "setmaxtxamount",
    "setmaxwalletamount",
    "setfees",
    "setfeerate",
    "settaxrate",
)


def _run_fraud_gnn(fingerprint: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    try:
        from app.fraud_gnn import get_fraud_gnn

        prob, is_fraud, model = get_fraud_gnn().predict(fingerprint)
        level = 2 if prob >= 0.7 else 1 if prob >= 0.4 else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "fraud_gnn",
                "level": "warning" if level >= 1 else "info",
                "message": f"fraud probability {prob:.4f} (model={model})",
            }
        ]
        return (
            {"fraud_probability": prob, "is_fraud": is_fraud, "model": model, "risk_level": level},
            findings,
            level,
        )
    except Exception as exc:
        logger.warning("deep_scan_fraud_gnn_failed", detail=str(exc))
        return (
            {"error": str(exc), "risk_level": 0},
            [{"module": "fraud_gnn", "level": "error", "message": f"fraud_gnn failed: {exc}"}],
            0,
        )


def _run_pyod(fingerprint: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    try:
        from app.pyod_anomaly import get_pyod_anomaly

        result = get_pyod_anomaly().score(fingerprint)
        level = 2 if result.is_anomaly else 1 if result.anomaly_score >= 0.6 else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "pyod_anomaly",
                "level": "warning" if level >= 1 else "info",
                "message": f"anomaly score {result.anomaly_score:.4f} (model={result.model_type})",
            }
        ]
        return (result.to_dict(), findings, level)
    except Exception as exc:
        logger.warning("deep_scan_pyod_failed", detail=str(exc))
        return (
            {"anomaly_score": 0.0, "is_anomaly": False, "model_type": "error", "error": str(exc), "risk_level": 0},
            [{"module": "pyod_anomaly", "level": "error", "message": f"pyod failed: {exc}"}],
            0,
        )


def _run_pygod(
    transfers: Sequence[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run PyGOD graph anomaly detection when a transfer graph is available."""
    if not transfers:
        return (
            {
                "anomaly_score": 0.0,
                "is_anomaly": False,
                "model_type": "skipped",
                "warnings": ["no transfer graph provided; PyGOD skipped"],
                "risk_level": 0,
            },
            [{"module": "pygod_anomaly", "level": "info", "message": "PyGOD skipped: no transfer graph provided"}],
            0,
        )
    try:
        import networkx as nx

        from app.pygod_anomaly import get_pygod_anomaly

        graph: Any = nx.DiGraph()
        for edge in transfers:
            src = edge.get("from_addr") or edge.get("source")
            dst = edge.get("to_addr") or edge.get("target")
            if src and dst and str(src) != str(dst):
                graph.add_edge(str(src), str(dst))
        if graph.number_of_nodes() == 0:
            return (
                {
                    "anomaly_score": 0.0,
                    "is_anomaly": False,
                    "model_type": "skipped",
                    "warnings": ["transfer graph has no edges; PyGOD skipped"],
                    "risk_level": 0,
                },
                [{"module": "pygod_anomaly", "level": "info", "message": "PyGOD skipped: empty transfer graph"}],
                0,
            )

        result = get_pygod_anomaly().score(graph)
        level = 2 if result.is_anomaly else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "pygod_anomaly",
                "level": "warning" if level >= 1 else "info",
                "message": f"graph anomaly score {result.anomaly_score:.4f} (model={result.model_type})",
            }
        ]
        return (result.to_dict(), findings, level)
    except Exception as exc:
        logger.warning("deep_scan_pygod_failed", detail=str(exc))
        return (
            {"anomaly_score": 0.0, "is_anomaly": False, "model_type": "error", "error": str(exc), "risk_level": 0},
            [{"module": "pygod_anomaly", "level": "error", "message": f"pygod failed: {exc}"}],
            0,
        )


def _run_decompile(code: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Decompile raw bytecode into an ABI (unverified contracts)."""
    try:
        from app.heimdall_bytecode import analyze_bytecode

        result = analyze_bytecode(code, rpc_url=None, timeout_secs=DEFAULT_DECOMPILE_TIMEOUT_SECS)
        payload = result.to_dict()
        findings: list[dict[str, Any]] = []
        if not result.success:
            message = result.decompile_error or result.storage_error or "unknown error"
            findings.append(
                {"module": "heimdall_bytecode", "level": "warning", "message": f"decompile incomplete: {message}"}
            )
            return payload, findings, 0

        dangerous = sorted(
            {
                str(fn.get("name", ""))
                for fn in result.functions
                if any(pattern in str(fn.get("name", "")).lower() for pattern in DANGEROUS_FUNCTION_PATTERNS)
            }
        )
        if dangerous:
            findings.append(
                {
                    "module": "heimdall_bytecode",
                    "level": "warning",
                    "message": f"dangerous function names detected: {', '.join(dangerous[:5])}",
                }
            )
            return payload, findings, 1
        findings.append(
            {
                "module": "heimdall_bytecode",
                "level": "info",
                "message": f"decompiled {len(result.functions)} functions from bytecode",
            }
        )
        return payload, findings, 0
    except Exception as exc:
        logger.warning("deep_scan_decompile_failed", detail=str(exc))
        return (
            {"success": False, "decompile_error": str(exc), "risk_level": 0},
            [{"module": "heimdall_bytecode", "level": "error", "message": f"decompile failed: {exc}"}],
            0,
        )


def _run_sell_sim(address: str, chain: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run the pyrevm sell round-trip simulation (EVM chains only)."""
    if not is_evm(chain):
        return (
            {
                "simulation_success": False,
                "chain": chain,
                "warnings": ["sell simulation skipped: chain is not EVM"],
                "risk_level": 0,
            },
            [{"module": "pyrevm_engine", "level": "info", "message": f"sell simulation skipped: {chain} is not EVM"}],
            0,
        )
    try:
        from app.pyrevm_engine import simulate_sell_round_trip

        result = simulate_sell_round_trip(address, chain=chain)
        payload = asdict(result)
        level = SELL_RISK_LEVELS.get(result.risk, 0)
        findings: list[dict[str, Any]] = []
        if result.simulation_success:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "warning" if level >= 2 else "info",
                    "message": f"sell simulation risk={result.risk} tax_pct={result.tax_pct}",
                }
            )
        else:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "info",
                    "message": f"sell simulation failed: {'; '.join(result.warnings[:2]) or 'unknown reason'}",
                }
            )
        if result.is_honeypot:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "warning",
                    "message": "honeypot signature detected (can sell, sell-to-pair reverts)",
                }
            )
        return (payload, findings, level)
    except Exception as exc:
        logger.warning("deep_scan_sell_sim_failed", detail=str(exc))
        return (
            {"simulation_success": False, "warnings": [str(exc)], "risk_level": 0},
            [{"module": "pyrevm_engine", "level": "error", "message": f"sell simulation failed: {exc}"}],
            0,
        )


def _run_slither(source_code: str | None, solc_version: str | None) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run Slither source analysis when verified Solidity source is provided."""
    if not source_code or not source_code.strip():
        return (
            {"success": False, "skipped": "no verified source provided", "detectors": [], "risk_level": 0},
            [
                {
                    "module": "slither_analyzer",
                    "level": "info",
                    "message": "Slither skipped: no verified source provided",
                }
            ],
            0,
        )
    try:
        from app.slither_analyzer import analyze_source, is_security_relevant

        result = analyze_source(source_code, solc_version)
        payload = result.to_dict()
        findings: list[dict[str, Any]] = []
        if not result.success:
            findings.append(
                {"module": "slither_analyzer", "level": "warning", "message": f"slither failed: {result.error}"}
            )
            return payload, findings, 0

        high = [
            d for d in result.detectors if d.get("impact") == "High" and is_security_relevant(str(d.get("check", "")))
        ]
        medium = [
            d for d in result.detectors if d.get("impact") == "Medium" and is_security_relevant(str(d.get("check", "")))
        ]
        level = 2 if high else 1 if medium else 0
        if high:
            checks = ", ".join(str(d.get("check", "")) for d in high[:5])
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "warning",
                    "message": f"{len(high)} high-impact security-relevant findings: {checks}",
                }
            )
        elif medium:
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "warning",
                    "message": f"{len(medium)} medium-impact security-relevant findings",
                }
            )
        else:
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "info",
                    "message": f"slither ran clean: {len(result.detectors)} detectors total",
                }
            )
        return (payload, findings, level)
    except Exception as exc:
        logger.warning("deep_scan_slither_failed", detail=str(exc))
        return (
            {"success": False, "detectors": [], "error": str(exc), "risk_level": 0},
            [{"module": "slither_analyzer", "level": "error", "message": f"slither failed: {exc}"}],
            0,
        )
