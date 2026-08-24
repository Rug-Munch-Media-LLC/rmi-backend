"""Deep contract-level static analysis enrichment (Slither / Mythril).

OPTIONAL "deep" enrichment for the scanner: runs OSS static-analysis tools
(Slither, Mythril) against an EVM contract's source or bytecode to surface
vulnerabilities (reentrancy, unchecked calls, arbitrary-send, ...) as plain
findings the rest of the scanner can consume.

Design contract
---------------
- Fail-open: any error (tool missing, bad JSON, timeout, network fetch
  failure) degrades to ``{"available": False}`` — never raises, never blocks
  the scan.
- ``available: False`` is a *clean* state when the tools are not installed —
  not an error. ``contract_signal()`` returns ``None`` for it.
- Fully injectable so it is testable without the tools installed:
  ``runner`` replaces subprocess execution and ``source_fetcher`` replaces
  source retrieval (Sourcify by default).

Installation (optional — deep enrichment stays off until these exist)
---------------------------------------------------------------------
    pip install slither-analyzer mythril

Slither also needs a ``solc`` binary on PATH (best-effort, source-based):
    pip install solc-select && solc-select install 0.8.28 && solc-select use 0.8.28

Mythril additionally supports raw bytecode analysis (no solc needed):
    mythril analyze --code <hex_bytecode>

Wiring into the scan (the lead wires the actual integration; documented here)
-----------------------------------------------------------------------------
In the scanner service (e.g. ``app/domains/scanner/service/scan.py`` or
``scan_enrich.py``), after the regular checks, run:

    from app.domains.scanner.contract_analysis import analyze_contract, contract_signal

    deep = await analyze_contract(address, chain)      # fail-open
    if deep["available"]:
        signal = contract_signal(deep)                 # e.g. "slither: reentrancy (high)"
        ... append signal/findings to enrichments / RAG text ...

Recommend gating behind an env flag (e.g. ``SCANNER_CONTRACT_ANALYSIS=true``)
so the default scan path never pays the subprocess cost. The SENTINEL
pipeline already runs Slither via ``app/domains/scanners/static_analyzer.py``;
this module is the reusable scanner-domain wrapper.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT = 120.0

# ── Sourcify chain → chain-id map for the default source fetcher ──────
_SOURCIFY_CHAIN_IDS: dict[str, str] = {
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
    "linea": "59144",
    "zksync": "324",
    "scroll": "534352",
    "mantle": "5000",
}

# ── Injectable seam types ─────────────────────────────────────────────
# ``runner`` receives the fully-built command list and must return an object
# with ``.returncode``, ``.stdout`` (str) and ``.stderr`` (str).
SourceFetcher = Callable[[str, str], Awaitable[str | None]]
RunResult = Any  # duck-typed: returncode / stdout / stderr
ToolRunner = Callable[[list[str]], Awaitable[RunResult]]


@dataclass
class _RunResult:
    """Default runner result — mirrors ``asyncio.subprocess`` capture."""

    returncode: int
    stdout: str
    stderr: str


def _tool_bin(tool: str) -> str:
    """Resolve the tool binary, honoring env overrides (SLITHER_BIN/MYTHRIL_BIN)."""
    env_name = "SLITHER_BIN" if tool == "slither" else "MYTHRIL_BIN"
    return os.getenv(env_name, "") or tool


def _detect_tool(prefer: str | None = None) -> str | None:
    """Return the first installed tool, preferring an explicit choice.

    Slither is preferred over Mythril when both are installed. ``None`` means
    no suitable tool is on PATH.
    """
    candidates = [prefer] if prefer else ["slither", "mythril"]
    for tool in candidates:
        if tool and shutil.which(_tool_bin(tool)) is not None:
            return tool
    return None


def _resolve_tool(prefer: str | None, has_source: bool, has_bytecode: bool) -> str | None:
    """Pick a tool honoring input constraints (Slither needs source)."""
    if prefer:
        return prefer if shutil.which(_tool_bin(prefer)) is not None else None
    for tool in ("slither", "mythril"):
        if shutil.which(_tool_bin(tool)) is None:
            continue
        if tool == "slither" and not has_source:
            continue
        return tool
    return None


def _unavailable_reason(has_source: bool, has_bytecode: bool) -> str:
    if has_source or has_bytecode:
        return (
            "no suitable static-analysis tool found: slither/mythril not installed "
            "(pip install slither-analyzer mythril) or slither is present but needs source"
        )
    return "no source or bytecode provided and none could be fetched"


def _make_runner(timeout: float) -> ToolRunner:
    """Build the default async subprocess runner (off the event loop)."""

    async def _runner(cmd: list[str]) -> _RunResult:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return _RunResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    return _runner


async def _run_fetch(fetch: SourceFetcher | None, address: str, chain: str) -> str | None:
    """Await an optional source fetcher; return ``None`` on any failure (fail-open)."""
    if fetch is None:
        return None
    try:
        result = await fetch(address, chain)
        return str(result) if result else None
    except Exception as exc:  # fail-open: fetcher errors degrade to no-source
        logger.debug("contract_analysis_source_fetch_failed: %s", exc)
        return None


async def _sourcify_fetch_source(address: str, chain: str) -> str | None:
    """Best-effort Sourcify v2 source fetch (fail-open, network).

    Returns a flattened single-file source string, or ``None`` when the
    contract is not verified / the chain is unsupported / the request fails.
    """
    chain_id = _SOURCIFY_CHAIN_IDS.get((chain or "").lower())
    if not chain_id:
        return None
    try:
        url = f"https://sourcify.dev/server/api/v2/contract/{chain_id}/{address}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            files = resp.json().get("files") or []
            contents = [f.get("content", "") for f in files if f.get("content")]
            if not contents:
                return None
            if len(contents) == 1:
                return contents[0]
            # Multi-file: flatten with header comments (best-effort analysis).
            return "\n\n".join(
                f"// ===== {f.get('name', 'file')} =====\n{c}" for f, c in zip(files, contents, strict=False)
            )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError) as exc:
        logger.debug("contract_analysis_sourcify_fetch_failed: %s", exc)
        return None


async def _prepare_source(source: str) -> Path:
    """Write contract source to a temp ``.sol`` file (blocking IO off the loop)."""

    def _write() -> Path:
        tmp_dir = Path(tempfile.mkdtemp(prefix="contract_analysis_"))
        sol_path = tmp_dir / "Contract.sol"
        sol_path.write_text(source, encoding="utf-8")
        return sol_path

    return await asyncio.to_thread(_write)


async def _safe_cleanup(sol_path: Path | None) -> None:
    """Remove the temp dir (blocking IO off the loop), never raising."""

    def _rm() -> None:
        if sol_path is not None:
            shutil.rmtree(sol_path.parent, ignore_errors=True)

    await asyncio.to_thread(_rm)


def _build_slither_cmd(bin_path: str, sol_path: Path) -> list[str]:
    return [bin_path, str(sol_path), "--json", "-", "--disable-color"]


def _build_mythril_cmd(bin_path: str, *, source_path: Path | None, bytecode: str | None) -> list[str]:
    cmd = [bin_path, "analyze"]
    if source_path is not None:
        cmd.append(str(source_path))
    elif bytecode:
        cmd += ["--code", bytecode]
    cmd.append("--json")
    return cmd


def _normalize_slither_finding(det: dict[str, Any]) -> dict[str, Any]:
    return {
        "check": det.get("check") or "",
        "severity": str(det.get("impact") or "informational").lower(),
        "description": det.get("first_markdown_element") or det.get("description") or "",
    }


def _normalize_mythril_finding(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "check": issue.get("title") or issue.get("swc-id") or issue.get("type") or "",
        "severity": str(issue.get("severity") or "unknown").lower(),
        "description": issue.get("description") or "",
    }


def _parse_slither_output(output: str) -> list[dict[str, Any]]:
    data = json.loads(output)
    detectors = (data.get("results") or {}).get("detectors") or []
    return [_normalize_slither_finding(d) for d in detectors]


def _parse_mythril_output(output: str) -> list[dict[str, Any]]:
    data = json.loads(output)
    issues = data.get("issues") or []
    return [_normalize_mythril_finding(i) for i in issues]


def _extract_output(result: RunResult) -> str:
    """Slither/Mythril write JSON to stdout (sometimes stderr on failure)."""
    out = str(getattr(result, "stdout", "") or "")
    if not out:
        out = str(getattr(result, "stderr", "") or "")
    return out


async def analyze_contract(
    address: str,
    chain: str,
    *,
    bytecode: str | None = None,
    source: str | None = None,
    source_fetcher: SourceFetcher | None = None,
    runner: ToolRunner | None = None,
    tool: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run optional deep static analysis (Slither / Mythril) on an EVM contract.

    Best-effort and fail-open: any error returns ``{"available": False}`` —
    never raises, so the scanner can call this unconditionally as enrichment.

    Args:
        address: Contract address.
        chain: Chain id (ethereum, bsc, base, ...) used for source fetching.
        bytecode: Raw EVM bytecode (Mythril path; Slither needs source).
        source: Solidity source string (Skips fetching when provided).
        source_fetcher: async (address, chain) -> source str | None. Defaults
            to a Sourcify v2 fetch; inject a stub in tests / callers.
        runner: async (cmd: list[str]) -> result with ``.returncode``,
            ``.stdout``/``.stderr`` str. Defaults to a subprocess runner with
            ``timeout``. Injecting a runner implies the tool is available
            (skips binary detection) — used by tests without the tools.
        tool: Force "slither" or "mythril". Default: detect on PATH.
        timeout: Subprocess timeout in seconds (default 120).

    Returns:
        {
            "available": bool,          # False = clean failure (no tools/no input/error)
            "findings": [{check, severity, description}, ...],  # when available
            "tool": "slither" | "mythril",                     # when available
        }
    """
    try:
        has_source = bool(source)
        has_bytecode = bool(bytecode)

        # Resolve input FIRST so tool selection knows what we actually have:
        # provided source/bytecode, else the (injectable or default Sourcify)
        # source fetcher.
        if not has_source and not has_bytecode:
            fetched = await _run_fetch(source_fetcher or _sourcify_fetch_source, address, chain)
            if not fetched:
                return {"available": False, "reason": "no source or bytecode available for contract analysis"}
            source = fetched
            has_source = True

        # An injected runner short-circuits binary detection (testability);
        # otherwise pick an installed tool that the input supports.
        tool_name: str | None
        if runner is not None:
            tool_name = tool or "slither"
            run: ToolRunner = runner
        else:
            tool_name = _resolve_tool(tool, has_source, has_bytecode)
            if tool_name is None:
                return {"available": False, "reason": _unavailable_reason(has_source, has_bytecode)}
            run = _make_runner(timeout)

        # Slither is source-based only; Mythril can also take raw bytecode.
        if tool_name == "slither" and not has_source:
            return {
                "available": False,
                "reason": "slither requires contract source; pass source or use tool='mythril' with bytecode",
            }

        sol_path = await _prepare_source(source or "") if has_source else None
        try:
            bin_path = _tool_bin(tool_name)
            if tool_name == "slither":
                # Guaranteed by the guard above; kept for type-narrowing.
                if sol_path is None:
                    return {"available": False, "reason": "slither requires contract source"}
                cmd = _build_slither_cmd(bin_path, sol_path)
                parse = _parse_slither_output
            else:
                cmd = _build_mythril_cmd(bin_path, source_path=sol_path, bytecode=bytecode)
                parse = _parse_mythril_output

            result = await run(cmd)
            output = _extract_output(result)
            if not output:
                raise ValueError(f"{tool_name} produced no output")
            findings = parse(output)
            return {"available": True, "findings": findings, "tool": tool_name}
        finally:
            await _safe_cleanup(sol_path)
    except Exception as exc:  # fail-open by contract
        logger.debug("contract_analysis_failed: %s", exc)
        return {"available": False}


def contract_signal(result: dict[str, Any]) -> str | None:
    """Render a short human signal for an :func:`analyze_contract` result.

    - exactly one finding -> ``"slither: reentrancy (high)"``
      (Slither check ids like ``reentrancy-eth`` are shortened to their base
      detector name).
    - multiple findings   -> ``"slither: 3 findings"``
    - none / unavailable  -> ``None``

    Examples:
        {"available": True, "tool": "slither", "findings": [
            {"check": "reentrancy-eth", "severity": "high"}]}   -> "slither: reentrancy (high)"
        {"available": True, "tool": "mythril", "findings": [..3..]} -> "mythril: 3 findings"
        {"available": False}                                    -> None
    """
    if not result.get("available"):
        return None
    findings = result.get("findings") or []
    if not findings:
        return None
    tool = str(result.get("tool") or "slither")
    if len(findings) == 1:
        single = findings[0]
        check = str(single.get("check") or "finding").split("-")[0]
        severity = str(single.get("severity") or "unknown").lower()
        return f"{tool}: {check} ({severity})"
    return f"{tool}: {len(findings)} findings"
