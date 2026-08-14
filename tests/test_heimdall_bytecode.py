"""Contract tests for ``app.heimdall_bytecode`` (module created by a parallel agent).

The module is not present in this checkout yet; ``importorskip`` makes the
whole file skip cleanly until it lands.  Once present, these tests assert the
bytecode-fetch contract with every subprocess spawn path mocked — no heimdall
binary on PATH, no RPC, no network.

Contract under test (matching the module's purpose — fetch contract bytecode
via the heimdall CLI subprocess):

* a sync fetch callable (``fetch_bytecode`` / ``get_bytecode`` /
  ``fetch_contract_bytecode``) that returns the runtime bytecode as ``bytes``
  parsed from the subprocess stdout hex;
* graceful failure (falsy result, no exception) on non-zero exit, missing
  binary, timeout, and empty stdout;
* an optional async variant (``afetch_bytecode`` / ``async_fetch_bytecode``).

Tests self-skip with a clear reason when the expected function is not present
yet, so the parallel agent can land the module and run this suite directly.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

import pytest

mod = pytest.importorskip("app.heimdall_bytecode")

TOKEN = "0x" + "11" * 20

_SYNC_FETCH = next(
    (name for name in ("fetch_bytecode", "get_bytecode", "fetch_contract_bytecode") if hasattr(mod, name)),
    None,
)
_ASYNC_FETCH = next(
    (name for name in ("afetch_bytecode", "async_fetch_bytecode", "fetch_bytecode_async") if hasattr(mod, name)),
    None,
)

_SKIP_SYNC = pytest.mark.skipif(
    _SYNC_FETCH is None, reason="no sync bytecode-fetch function in app.heimdall_bytecode yet"
)
_SKIP_ASYNC = pytest.mark.skipif(
    _ASYNC_FETCH is None, reason="no async bytecode-fetch function in app.heimdall_bytecode yet"
)


def _sync_fetch() -> Any:
    return getattr(mod, _SYNC_FETCH) if _SYNC_FETCH is not None else None


def _async_fetch() -> Any:
    return getattr(mod, _ASYNC_FETCH) if _ASYNC_FETCH is not None else None


class _SyncResult:
    """CompletedProcess-shaped stand-in (attribute-compatible with subprocess.run)."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _AsyncProc:
    """Process-shaped stand-in for ``asyncio.create_subprocess_exec``."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class SubprocessStub:
    """Configurable stand-in for every subprocess spawn API the module may use."""

    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = b"0x60806040"
        self.stderr = b""
        self.exc: Exception | None = None
        self.calls: dict[str, list[tuple[tuple[Any, ...], dict[str, Any]]]] = {"run": [], "popen": [], "create": []}

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> _SyncResult:
            self.calls["run"].append((args, kwargs))
            return self._result()

        def fake_popen(*args: Any, **kwargs: Any) -> _SyncResult:
            self.calls["popen"].append((args, kwargs))
            return self._result()

        async def fake_create(*args: Any, **kwargs: Any) -> _AsyncProc:
            self.calls["create"].append((args, kwargs))
            if self.exc is not None:
                raise self.exc
            return _AsyncProc(self.returncode, self.stdout, self.stderr)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
        monkeypatch.setattr(asyncio.subprocess, "create_subprocess_exec", fake_create)

    def _result(self) -> _SyncResult:
        if self.exc is not None:
            raise self.exc
        return _SyncResult(self.returncode, self.stdout, self.stderr)

    def spawned_args_flat(self) -> list[str]:
        flat: list[str] = []
        for name in ("run", "popen", "create"):
            for args, _kwargs in self.calls[name]:
                flat.extend(_flatten(args))
        return flat


def _flatten(value: Any) -> list[str]:
    out: list[str] = []
    for item in value:
        if isinstance(item, (list, tuple)):
            out.extend(_flatten(item))
        else:
            out.append(str(item))
    return out


@pytest.fixture
def subprocess_stub(monkeypatch: pytest.MonkeyPatch) -> SubprocessStub:
    stub = SubprocessStub()
    stub.install(monkeypatch)
    return stub


# ── sync fetch ───────────────────────────────────────────────────────────────


@_SKIP_SYNC
def test_fetch_bytecode_parses_hex_stdout(subprocess_stub: SubprocessStub) -> None:
    result = _sync_fetch()(TOKEN)
    assert result == bytes.fromhex("60806040")
    # the token address must reach the subprocess command line
    assert any(TOKEN.lower() in arg.lower() for arg in subprocess_stub.spawned_args_flat())


@_SKIP_SYNC
def test_fetch_bytecode_nonzero_exit_is_graceful(subprocess_stub: SubprocessStub) -> None:
    subprocess_stub.returncode = 1
    subprocess_stub.stderr = b"heimdall: rpc failed"
    assert not _sync_fetch()(TOKEN)


@_SKIP_SYNC
def test_fetch_bytecode_missing_binary_is_graceful(subprocess_stub: SubprocessStub) -> None:
    subprocess_stub.exc = FileNotFoundError("heimdall binary not found")
    assert not _sync_fetch()(TOKEN)


@_SKIP_SYNC
def test_fetch_bytecode_timeout_is_graceful(subprocess_stub: SubprocessStub) -> None:
    subprocess_stub.exc = subprocess.TimeoutExpired(cmd="heimdall", timeout=30)
    assert not _sync_fetch()(TOKEN)


@_SKIP_SYNC
def test_fetch_bytecode_empty_stdout_is_graceful(subprocess_stub: SubprocessStub) -> None:
    subprocess_stub.stdout = b""
    assert not _sync_fetch()(TOKEN)


# ── async fetch ──────────────────────────────────────────────────────────────


@_SKIP_ASYNC
async def test_afetch_bytecode_async_success(subprocess_stub: SubprocessStub) -> None:
    result = await _async_fetch()(TOKEN)
    assert result == bytes.fromhex("60806040")
    assert subprocess_stub.calls["create"], "async fetch should spawn a subprocess"


@_SKIP_ASYNC
async def test_afetch_bytecode_nonzero_exit_is_graceful(subprocess_stub: SubprocessStub) -> None:
    subprocess_stub.returncode = 2
    assert not await _async_fetch()(TOKEN)
