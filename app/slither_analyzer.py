"""Slither source-level static analyzer wrapper.

Runs the ``slither`` CLI (Trail of Bits' Solidity static analyzer) against
VERIFIED contract source code and returns structured vulnerability findings.

Position in the scanner pipeline
---------------------------------
Complements the two existing analysis modules:

* ``app/heimdall_bytecode.py`` — bytecode → ABI decompilation. Works on
  UNVERIFIED contracts (most rug/honeypot tokens ship without verified
  source), but cannot see logic-level flaws.
* ``app/pyrevm_engine.py`` — runtime sell-simulation (honeypot / transfer-tax
  / blacklist / anti-whale detection) via local EVM execution of real bytecode.
* ``app/slither_analyzer.py`` (this module) — source-level static analysis.
  Requires verified source, and catches what bytecode-level tools cannot:
  reentrancy, unchecked low-level calls, ``tx.origin`` misuse, controlled
  ``delegatecall``, weak PRNG, and the other logic detectors Slither reports.

Binary resolution
-----------------
The ``slither`` executable is located on PATH via ``shutil.which``. If it is
absent, :func:`analyze_source` raises :class:`SlitherNotAvailable` with
install instructions; the module itself still imports cleanly.

Exit-code note (verified 2026-08 with slither 0.11.6)
-----------------------------------------------------
Slither returns exit code 255 even when a run SUCCEEDS and findings exist.
The JSON payload's ``success`` field is the authoritative signal — the process
exit code is not used to judge success.

JSON contract
-------------
``slither <file> --json -`` prints a JSON object to stdout::

    {
      "success": true,
      "error": null,
      "results": {
        "detectors": [
          {
            "check": "reentrancy-eth",
            "impact": "High",
            "confidence": "Medium",
            "description": "...",
            "elements": [ { "name": ..., "type": ..., "source_mapping": ... } ]
          }
        ]
      }
    }

All detectors are returned. :data:`SECURITY_FOCUS_CHECKS` marks the subset
with real exploit potential so callers can prioritize.

Dependency (install only — never imported)
------------------------------------------
``slither-analyzer`` — pin ``slither-analyzer==0.11.6`` when it is added to
the project. A ``solc`` binary is also required (``solc-select install
<version>`` / ``solc-select use <version>``, or a system ``solc``).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, cast

logger = logging.getLogger(__name__)

__all__ = [
    "SECURITY_FOCUS_CHECKS",
    "SlitherNotAvailable",
    "SlitherResult",
    "analyze_source",
    "is_security_relevant",
    "slither_available",
    "slither_version",
]

_DEFAULT_TIMEOUT_SECS = 120

_INSTALL_HINT = (
    "slither is not installed or not on PATH. Install it with: "
    "pip install slither-analyzer, then ensure a solc binary is available "
    "(solc-select install <version> && solc-select use <version>, or apt install solc). "
    "See https://github.com/crytic/slither for details."
)

# Security-relevant detector checks (verified against slither 0.11.6
# --list-detectors). ALL detectors are returned by analyze_source(); this set
# exists so callers can flag/prioritize the findings with real exploit
# potential (reentrancy, unchecked calls, tx.origin, delegatecall, etc.).
SECURITY_FOCUS_CHECKS: Final[frozenset[str]] = frozenset(
    {
        "arbitrary-send-erc20",
        "arbitrary-send-erc20-permit",
        "arbitrary-send-eth",
        "controlled-array-length",
        "controlled-delegatecall",
        "delegatecall-loop",
        "divide-before-multiply",
        "encode-packed-collision",
        "erc20-interface",
        "erc721-interface",
        "incorrect-equality",
        "incorrect-exp",
        "locked-ether",
        "missing-zero-check",
        "multiple-constructors",
        "reentrancy-balance",
        "reentrancy-benign",
        "reentrancy-eth",
        "reentrancy-events",
        "reentrancy-no-eth",
        "reentrancy-unlimited-gas",
        "shadowing-abstract",
        "shadowing-builtin",
        "shadowing-local",
        "shadowing-state",
        "suicidal",
        "tx-origin",
        "unchecked-lowlevel",
        "unchecked-send",
        "unchecked-transfer",
        "uninitialized-fptr-cst",
        "uninitialized-local",
        "uninitialized-state",
        "uninitialized-storage",
        "unprotected-upgrade",
        "unused-return",
        "weak-prng",
        "write-after-write",
    }
)


class SlitherNotAvailable(RuntimeError):
    """Raised when the ``slither`` executable is not installed / on PATH."""

    def __init__(self, message: str = _INSTALL_HINT) -> None:
        super().__init__(message)


@dataclass
class SlitherResult:
    """Structured outcome of a Slither source analysis run.

    ``success`` is authoritative (from Slither's JSON payload, not the process
    exit code). On failure, ``error`` carries a human-readable reason and
    ``detectors`` is empty.
    """

    success: bool
    detectors: list[dict[str, Any]] = field(default_factory=lambda: cast("list[dict[str, Any]]", []))
    error: str | None = None
    solc_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)


# ────────────────────────────────────────────────────────────────────────────
# Binary discovery
# ────────────────────────────────────────────────────────────────────────────


def _resolve_slither() -> str | None:
    """Absolute path to the slither executable, or None when unavailable."""
    return shutil.which("slither")


def slither_available() -> bool:
    """True when a ``slither`` executable is on PATH."""
    return _resolve_slither() is not None


def slither_version() -> str | None:
    """Run ``slither --version``; returns the version string or None."""
    slither_bin = _resolve_slither()
    if slither_bin is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [slither_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    version = proc.stdout.strip()
    return version or None


# ────────────────────────────────────────────────────────────────────────────
# JSON parsing
# ────────────────────────────────────────────────────────────────────────────


def _element_to_dict(element: dict[str, Any]) -> dict[str, Any]:
    """Compact a Slither JSON element (keep source mapping, drop deep nesting)."""
    source_mapping: dict[str, Any] = element.get("source_mapping") or {}
    type_specific: dict[str, Any] = element.get("type_specific_fields") or {}
    parent_fields: dict[str, Any] = type_specific.get("parent") or {}
    return {
        "name": element.get("name", ""),
        "type": element.get("type", ""),
        "source_mapping": {
            "filename_short": source_mapping.get("filename_short", ""),
            "start": source_mapping.get("start", 0),
            "length": source_mapping.get("length", 0),
            "starting_column": source_mapping.get("starting_column", 0),
            "ending_column": source_mapping.get("ending_column", 0),
            "lines": source_mapping.get("lines", []),
        },
        "parent": {
            "type": parent_fields.get("type", ""),
            "name": parent_fields.get("name", ""),
        },
    }


def _detector_to_dict(detector: dict[str, Any]) -> dict[str, Any]:
    raw_elements: list[Any] = cast("list[Any]", detector.get("elements", []))
    return {
        "check": detector.get("check", ""),
        "description": detector.get("description", ""),
        "impact": detector.get("impact", ""),
        "confidence": detector.get("confidence", ""),
        "elements": [_element_to_dict(cast("dict[str, Any]", e)) for e in raw_elements if isinstance(e, dict)],
    }


def _tail(text: str, limit: int = 2000) -> str:
    """Last ``limit`` chars of stderr/stdout for error context."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def _parse_slither_output(
    proc: subprocess.CompletedProcess[str],
    solc_version: str | None,
) -> SlitherResult:
    stdout = proc.stdout.strip()
    if not stdout:
        if proc.returncode != 0:
            # With --json -, Slither suppresses its console output entirely on
            # compile failure (verified slither 0.11.6): both streams are empty
            # and the source likely failed to compile.
            return SlitherResult(
                success=False,
                error=(
                    f"slither exited {proc.returncode} with no JSON output — the source "
                    "likely failed to compile (compile errors are suppressed in JSON mode)"
                ),
            )
        return SlitherResult(
            success=False,
            error=f"slither exited {proc.returncode} with no JSON output",
        )
    try:
        raw_payload: Any = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return SlitherResult(
            success=False,
            error=f"slither produced invalid JSON ({exc}); stderr: {_tail(proc.stderr)}",
        )
    if not isinstance(raw_payload, dict):
        return SlitherResult(success=False, error="slither JSON output was not an object")
    payload: dict[str, Any] = cast("dict[str, Any]", raw_payload)

    ok = bool(payload.get("success", False))
    if not ok:
        slither_error: Any = payload.get("error")
        return SlitherResult(
            success=False,
            error=str(slither_error) if slither_error else _tail(proc.stderr),
        )

    results: dict[str, Any] = cast("dict[str, Any]", payload.get("results") or {})
    raw_detectors: list[Any] = cast("list[Any]", results.get("detectors", []))
    detectors = [_detector_to_dict(cast("dict[str, Any]", d)) for d in raw_detectors if isinstance(d, dict)]
    return SlitherResult(success=True, detectors=detectors, solc_version=solc_version)


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def is_security_relevant(check: str) -> bool:
    """True when ``check`` is in :data:`SECURITY_FOCUS_CHECKS`."""
    return check in SECURITY_FOCUS_CHECKS


def analyze_source(
    source_code: str,
    solc_version: str | None = None,
    *,
    timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
) -> SlitherResult:
    """Run Slither against verified Solidity source code.

    Writes ``source_code`` to a temp ``.sol`` file and executes
    ``slither <file> --json -`` (JSON to stdout), parsing the payload into a
    :class:`SlitherResult` with one entry per detector.

    Args:
        source_code: Full Solidity source (single file). Imports of local
            dependencies (e.g. OpenZeppelin) are NOT resolved — pass a
            self-contained file or one whose imports resolve from PATH.
        solc_version: Optional compiler version to force, e.g. ``"0.8.20"``.
            Passed via ``--solc-solcs-select`` (requires ``solc-select`` with
            that version installed). When omitted, Slither uses the default
            solc on PATH.
        timeout_secs: Per-run timeout for the slither subprocess.

    Returns:
        SlitherResult — ``success=True`` with all detected ``detectors`` on a
        clean run (exit code 255 is normal when findings exist); on analysis
        failure ``success=False`` with a human-readable ``error``.

    Raises:
        SlitherNotAvailable: when the ``slither`` executable is missing.
    """
    slither_bin = _resolve_slither()
    if slither_bin is None:
        raise SlitherNotAvailable()

    if not source_code or not source_code.strip():
        return SlitherResult(success=False, error="source_code is empty")

    with tempfile.TemporaryDirectory(prefix="slither_analyzer_") as tmp_dir:
        source_file = Path(tmp_dir) / "contract.sol"
        source_file.write_text(source_code, encoding="utf-8")

        cmd = [slither_bin, str(source_file), "--json", "-"]
        if solc_version:
            cmd.extend(["--solc-solcs-select", solc_version])

        logger.debug("slither_cmd: %s", " ".join(cmd))
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_secs,
                check=False,
                shell=False,
                cwd=tmp_dir,
            )
        except FileNotFoundError as exc:  # binary vanished between which() and run
            raise SlitherNotAvailable() from exc
        except subprocess.TimeoutExpired:
            return SlitherResult(
                success=False,
                error=f"slither timed out after {timeout_secs}s",
            )

        return _parse_slither_output(proc, solc_version)
