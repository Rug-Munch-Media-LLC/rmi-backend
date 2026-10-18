"""MCP protocol revision must come from ONE constant, never a literal.

Regression guards for the 2024-11-05 stragglers: the local MCP stdio client and
the ``/mcp/sdk`` status payload must both advertise ``MCP_PROTOCOL_VERSION``.
"""

from __future__ import annotations

import inspect
import json

from app.caching_shield import local_mcp, mcp_stdio, quality_endpoints
from app.mcp._catalog import MCP_PROTOCOL_VERSION

HARDCODED_REVISIONS = ("2024-11-05", "2026-07-28")


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeStdout:
    def __init__(self, replies: list[bytes]) -> None:
        self._replies = replies

    async def readline(self) -> bytes:
        return self._replies.pop(0)


class _FakeProc:
    def __init__(self, replies: list[bytes]) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(replies)
        self.stderr = None


def test_shared_constant_is_current_revision():
    """The single source of truth carries the current MCP revision."""
    assert MCP_PROTOCOL_VERSION == "2026-07-28"


async def test_sdk_status_advertises_shared_constant():
    """``/mcp/sdk`` reports the shared constant, not a hardcoded string."""
    payload = await quality_endpoints.mcp_sdk()
    assert payload["mcp_native"]["protocol"] == f"MCP {MCP_PROTOCOL_VERSION}"


async def test_stdio_client_initializes_with_shared_constant(monkeypatch, tmp_path):
    """The stdio client's ``initialize`` params carry the shared revision."""
    (tmp_path / "server.py").write_text("")
    proc = _FakeProc(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode() + b"\n",
            json.dumps({"result": {"tools": [{"name": "demo"}]}}).encode() + b"\n",
        ]
    )

    async def _fake_exec(*_args, **_kwargs):
        return proc

    monkeypatch.setattr(mcp_stdio.asyncio, "create_subprocess_exec", _fake_exec)
    tools, live_proc = await mcp_stdio.discover_tools("demo", tmp_path)

    assert tools == [{"name": "demo"}]
    assert live_proc is proc
    init_msg = json.loads(proc.stdin.writes[0].decode())
    assert init_msg["params"]["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_no_module_hardcodes_a_revision():
    """Neither the facade nor the transport may carry a revision literal."""
    for module in (local_mcp, mcp_stdio):
        source = inspect.getsource(module)
        for literal in HARDCODED_REVISIONS:
            assert literal not in source, f"{module.__name__} hardcodes {literal}"
