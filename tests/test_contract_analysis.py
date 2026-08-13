"""Pure unit tests for contract-level static analysis enrichment (Slither/Mythril).

No network, no DB, no real slither/mythril: every test injects a fake
``runner`` (and monkeypatches ``shutil.which`` where binary detection must
be forced off). The default subprocess/Sourcify paths are never exercised.
"""

from __future__ import annotations

import json
import shutil
from typing import TYPE_CHECKING, Any

from app.domains.scanner.contract_analysis import analyze_contract, contract_signal

if TYPE_CHECKING:
    import pytest


class _FakeRunResult:
    """Duck-typed runner result: ``.returncode`` / ``.stdout`` / ``.stderr``."""

    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_runner(stdout: str, *, returncode: int = 0) -> tuple[Any, list[list[str]]]:
    """Build an injected runner returning fixed output; records every command."""
    calls: list[list[str]] = []

    async def _runner(cmd: list[str]) -> _FakeRunResult:
        calls.append(list(cmd))
        return _FakeRunResult(stdout=stdout, returncode=returncode)

    return _runner, calls


_SOURCE = "pragma solidity ^0.8.0; contract C {}"


async def test_returns_findings_from_mocked_slither() -> None:
    stdout = json.dumps(
        {
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "description": "Reentrancy",
                        "first_markdown_element": "Reentrancy in withdraw()",
                    },
                    {
                        "check": "unchecked-lowlevel",
                        "impact": "Medium",
                        "description": "Low level call",
                        "first_markdown_element": "",
                    },
                ]
            }
        }
    )
    runner, calls = _fake_runner(stdout)

    result = await analyze_contract("0xabc", "ethereum", source=_SOURCE, runner=runner)

    assert result["available"] is True
    assert result["tool"] == "slither"
    findings = result["findings"]
    assert len(findings) == 2
    assert findings[0]["check"] == "reentrancy-eth"
    assert findings[0]["severity"] == "high"
    assert findings[0]["description"] == "Reentrancy in withdraw()"
    assert findings[1]["severity"] == "medium"

    assert len(calls) == 1
    cmd = calls[0]
    assert any(str(arg).endswith(".sol") for arg in cmd)
    assert "--json" in cmd


async def test_available_false_when_tool_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    result = await analyze_contract("0xabc", "ethereum", source=_SOURCE)

    assert result["available"] is False


async def test_available_false_when_runner_fails() -> None:
    async def _boom(_cmd: list[str]) -> Any:
        raise RuntimeError("slither crashed")

    result = await analyze_contract("0xabc", "ethereum", source=_SOURCE, runner=_boom)

    assert result["available"] is False


async def test_fetches_source_via_injectable_fetcher() -> None:
    calls: list[tuple[str, str]] = []

    async def _fetcher(address: str, chain: str) -> str:
        calls.append((address, chain))
        return _SOURCE

    runner, _ = _fake_runner(json.dumps({"results": {"detectors": []}}))

    result = await analyze_contract("0xabc", "ethereum", source_fetcher=_fetcher, runner=runner)

    assert result["available"] is True
    assert calls == [("0xabc", "ethereum")]
    assert result["findings"] == []


async def test_mythril_bytecode_analysis() -> None:
    stdout = json.dumps(
        {
            "issues": [
                {
                    "title": "Unchecked CALL",
                    "severity": "High",
                    "description": "The return value of an external call is not checked",
                }
            ]
        }
    )
    runner, calls = _fake_runner(stdout)

    result = await analyze_contract(
        "0xabc",
        "ethereum",
        bytecode="0x6001600055",
        tool="mythril",
        runner=runner,
    )

    assert result["available"] is True
    assert result["tool"] == "mythril"
    assert result["findings"][0]["check"] == "Unchecked CALL"
    assert result["findings"][0]["severity"] == "high"

    cmd = calls[0]
    assert cmd[0].endswith("mythril")
    assert cmd[1] == "analyze"
    assert "--code" in cmd
    assert "0x6001600055" in cmd


async def test_slither_requires_source() -> None:
    runner, calls = _fake_runner("{}")

    result = await analyze_contract("0xabc", "ethereum", bytecode="0x00", runner=runner)

    assert result["available"] is False
    assert calls == []  # never invoked


async def test_available_false_without_source_or_bytecode() -> None:
    async def _fetch_none(_address: str, _chain: str) -> None:
        return None

    runner, _ = _fake_runner("{}")

    result = await analyze_contract("0xabc", "ethereum", source_fetcher=_fetch_none, runner=runner)

    assert result["available"] is False


def test_contract_signal_single_finding() -> None:
    result = {
        "available": True,
        "tool": "slither",
        "findings": [{"check": "reentrancy-eth", "severity": "high", "description": "x"}],
    }
    assert contract_signal(result) == "slither: reentrancy (high)"


def test_contract_signal_multiple_findings() -> None:
    result = {
        "available": True,
        "tool": "slither",
        "findings": [
            {"check": "reentrancy-eth", "severity": "high"},
            {"check": "unchecked-lowlevel", "severity": "medium"},
            {"check": "assembly", "severity": "low"},
        ],
    }
    assert contract_signal(result) == "slither: 3 findings"


def test_contract_signal_none() -> None:
    assert contract_signal({"available": False}) is None
    assert contract_signal({"available": False, "findings": [{"check": "x"}]}) is None
    assert contract_signal({"available": True, "tool": "mythril", "findings": []}) is None
