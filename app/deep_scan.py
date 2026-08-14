"""Deep-scan orchestrator — run the platform's ML + contract modules in one call.

Composes the already-added, self-contained analysis modules into a single
:func:`deep_scan_address` entry point so they actually get used together:

* :mod:`app.fraud_gnn` — ``FraudGNNDetector.predict(fingerprint)``: fraud
  probability over the :data:`FRAUD_FEATURES` fingerprint.
* :mod:`app.pyod_anomaly` — ``PyODAnomalyDetector.score(features)``: tabular
  anomaly score over the same fingerprint.
* :mod:`app.pygod_anomaly` — ``PyGODAnomalyDetector.score(graph)``: graph
  anomaly detection. Runs only when a transfer graph can be cheaply built
  (callers pass ``transfers=``); otherwise recorded as skipped.
* :mod:`app.heimdall_bytecode` — ``analyze_bytecode``: ABI extraction from raw
  bytecode (unverified contracts) after the code is fetched over RPC.
* :mod:`app.pyrevm_engine` — ``simulate_sell_round_trip``: honeypot / tax /
  blacklist / anti-whale detection via local EVM execution of real bytecode.
* :mod:`app.slither_analyzer` — ``analyze_source``: source-level static
  analysis. Requires verified Solidity source (``source_code=`` kwarg); when no
  source is available it is recorded as skipped.

Guarantees
----------
* Every sub-module call is wrapped in ``try/except``; a failure becomes an
  ``error``-level finding in ``report.findings`` instead of crashing the scan.
* Missing optional dependencies (heimdall-py, pyrevm, pyod, slither binary,
  networkx) degrade the affected section to an error/skipped entry.
* :func:`deep_scan_address` never raises for module-level failures.

Risk derivation
---------------
Each sub-module contributes a severity level ``0..3`` (low/medium/high/
critical); ``overall_risk`` is the maximum level across available modules:

* fraud_gnn: ``fraud_probability >= 0.7 -> high``, ``>= 0.4 -> medium``.
* pyod: ``is_anomaly -> high``, ``anomaly_score >= 0.6 -> medium``.
* pygod: ``is_anomaly -> high`` (only when a graph was provided).
* heimdall: dangerous function names found in the decompiled ABI -> medium.
* pyrevm: ``risk`` field mapped critical/dangerous/warning/safe.
* slither: High-impact security-relevant detector -> high, Medium -> medium.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from app.chain_registry import get_rpc_urls, is_evm
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

__all__ = ["DeepScanReport", "deep_scan_address"]

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CHAIN = "ethereum"
DEFAULT_RPC_TIMEOUT = 15.0
DEFAULT_DECOMPILE_TIMEOUT_SECS = 25

# Risk level -> human label. Higher is riskier.
RISK_LABELS: dict[int, str] = {0: "low", 1: "medium", 2: "high", 3: "critical"}

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


# ── Report model ─────────────────────────────────────────────────────────────


@dataclass
class DeepScanReport:
    """Outcome of a deep scan over one address."""

    address: str
    chain: str
    risk_scores: dict[str, Any]
    anomaly: dict[str, Any]
    decompiled: dict[str, Any]
    sell_sim: dict[str, Any]
    overall_risk: str
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "address": self.address,
            "chain": self.chain,
            "risk_scores": self.risk_scores,
            "anomaly": self.anomaly,
            "decompiled": self.decompiled,
            "sell_sim": self.sell_sim,
            "overall_risk": self.overall_risk,
            "findings": self.findings,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_chain(address: str, chain: str) -> str:
    """Resolve ``chain`` (``"auto"`` -> heuristic default)."""
    if chain and chain != "auto":
        return chain.lower()
    addr = (address or "").strip().lower()
    # EVM addresses are 0x + 40 hex chars; everything else is treated as Solana.
    if addr.startswith("0x") and len(addr) == 42:
        return DEFAULT_CHAIN
    return "solana"


def _build_fingerprint(address: str, chain: str) -> dict[str, Any]:
    """Build a FRAUD_FEATURES fingerprint for the address.

    Prefers the deterministic offline adapter (``app.ml_datasets.adapters``);
    falls back to an all-zeros fingerprint when it cannot be built, so the ML
    modules still receive a valid feature vector.
    """
    try:
        from app.ml_datasets.adapters import address_to_fingerprint

        return address_to_fingerprint(address, chain=chain)
    except Exception as exc:
        logger.warning("deep_scan_fingerprint_fallback", detail=str(exc))
        pyod_anomaly: Any | None
        try:
            from app import pyod_anomaly
        except ImportError:
            pyod_anomaly = None
        feature_names = getattr(pyod_anomaly, "FRAUD_FEATURES", ()) if pyod_anomaly is not None else ()
        return dict.fromkeys(feature_names, 0.0)


def _fetch_code(rpc_urls: Sequence[str], address: str, timeout: float) -> str:
    """Fetch runtime bytecode via ``eth_getCode`` across fallback RPC URLs.

    Returns the ``0x``-prefixed hex code, or ``""`` when the address has no
    code (EOA) or every RPC URL fails.
    """
    last_error: Exception | None = None
    for url in rpc_urls:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]},
                )
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    last_error = RuntimeError(f"rpc error: {payload['error']}")
                    continue
                result = payload.get("result")
                if isinstance(result, str) and result.startswith("0x"):
                    return result
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_error = exc
            continue
    if last_error is not None:
        logger.debug("deep_scan_fetch_code_failed: address=%s error=%s", address, str(last_error))
    return ""


# ── Guarded sub-module runners ───────────────────────────────────────────────
# Each runner returns (result_dict, findings, severity_level). No runner raises:
# exceptions are converted into error findings.


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

        graph = nx.DiGraph()
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


# ── Public API ───────────────────────────────────────────────────────────────


def deep_scan_address(
    address: str,
    *,
    chain: str = "auto",
    transfers: Sequence[dict[str, Any]] | None = None,
    source_code: str | None = None,
    solc_version: str | None = None,
    rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
) -> DeepScanReport:
    """Run the platform's ML + contract-analysis modules in one shot.

    Args:
        address: Address to scan (``0x``-prefixed EVM or Solana base58).
        chain: Chain name, or ``"auto"`` to resolve heuristically (``0x`` ->
            ethereum; otherwise solana).
        transfers: Optional transfer edges (``{"from_addr"/"source",
            "to_addr"/"target"}``) enabling PyGOD graph anomaly detection.
            When omitted, PyGOD is recorded as skipped.
        source_code: Optional verified Solidity source for Slither static
            analysis. When omitted, Slither is recorded as skipped.
        solc_version: Optional solc version hint for Slither.
        rpc_timeout: Per-RPC-request timeout in seconds.

    Returns:
        DeepScanReport — never raises for sub-module failures.
    """
    address = (address or "").strip()
    chain = _resolve_chain(address, chain)
    findings: list[dict[str, Any]] = []

    # 1) Fingerprint + ML anomaly modules.
    fingerprint = _build_fingerprint(address, chain)
    fraud_result, fraud_findings, fraud_level = _run_fraud_gnn(fingerprint)
    pyod_result, pyod_findings, pyod_level = _run_pyod(fingerprint)
    pygod_result, pygod_findings, pygod_level = _run_pygod(transfers)
    findings.extend(fraud_findings)
    findings.extend(pyod_findings)
    findings.extend(pygod_findings)

    # 2) Bytecode -> decompiled ABI (EVM only).
    decompiled: dict[str, Any]
    decompile_level = 0
    if is_evm(chain):
        code = _fetch_code(get_rpc_urls(chain), address, rpc_timeout)
        if not code or code == "0x":
            decompiled = {"success": False, "decompile_error": "no code at address (EOA or RPC unreachable)"}
            findings.append(
                {
                    "module": "heimdall_bytecode",
                    "level": "info",
                    "message": "no contract code at address; bytecode analysis skipped",
                }
            )
        else:
            decompiled, decompile_findings, decompile_level = _run_decompile(code)
            findings.extend(decompile_findings)
    else:
        decompiled = {
            "success": False,
            "decompile_error": f"bytecode analysis not supported for chain {chain!r} (EVM only)",
        }
        findings.append(
            {
                "module": "heimdall_bytecode",
                "level": "info",
                "message": f"bytecode analysis skipped: {chain} is not EVM",
            }
        )

    # 3) Sell simulation (EVM only) + Slither (requires source).
    sell_sim, sell_findings, sell_level = _run_sell_sim(address, chain)
    slither_result, slither_findings, slither_level = _run_slither(source_code, solc_version)
    findings.extend(sell_findings)
    findings.extend(slither_findings)

    # 4) Aggregate risk.
    overall_level = max(fraud_level, pyod_level, pygod_level, decompile_level, sell_level, slither_level)
    overall_risk = RISK_LABELS.get(overall_level, "low")

    risk_scores: dict[str, Any] = {
        "fraud_gnn": {**fraud_result, "risk_level": fraud_level},
        "pyod": {**pyod_result, "risk_level": pyod_level},
        "pygod": {**pygod_result, "risk_level": pygod_level},
        "decompiled": {**decompiled, "risk_level": decompile_level},
        "sell_sim": {**sell_sim, "risk_level": sell_level},
        "slither": {**slither_result, "risk_level": slither_level},
        "overall_level": overall_level,
    }

    return DeepScanReport(
        address=address,
        chain=chain,
        risk_scores=risk_scores,
        anomaly={"fraud_gnn": fraud_result, "pyod": pyod_result, "pygod": pygod_result},
        decompiled=decompiled,
        sell_sim=sell_sim,
        overall_risk=overall_risk,
        findings=findings,
    )
