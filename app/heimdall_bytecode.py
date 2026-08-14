"""Heimdall EVM bytecode decompilation wrapper.

Extracts an ABI (functions, events, errors, storage layout) from raw EVM
bytecode — including UNVERIFIED contracts, which is the primary value for
token scanning (most rug/honeypot tokens ship without verified source).

Backed by ``heimdall-py`` (the Heimdall decompiler by Jon-Becker,
https://github.com/Jon-Becker/heimdall-rs).  If the package is absent the
module still imports; :func:`analyze_bytecode` then raises
:class:`HeimdallNotAvailable` with install instructions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from heimdall_py import ABI

__all__ = ["DecompiledContract", "HeimdallNotAvailable", "analyze_bytecode"]

_DEFAULT_TIMEOUT_SECS = 25


class HeimdallNotAvailable(RuntimeError):
    """Raised when the ``heimdall-py`` package is not installed."""


@dataclass
class DecompiledContract:
    """Machine-readable result of decompiling a contract's bytecode."""

    success: bool
    functions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    storage_slots: list[dict[str, Any]] = field(default_factory=list)
    decompile_error: str | None = None
    storage_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)


def _import_heimdall() -> Any:
    try:
        import heimdall_py
    except ImportError as exc:
        raise HeimdallNotAvailable("heimdall-py is not installed; run: pip install heimdall-py") from exc
    return heimdall_py


def _param_to_dict(param: Any) -> dict[str, Any]:
    return {
        "name": param.name,
        "type": param.type_,
        "internal_type": param.internal_type,
    }


def _function_to_dict(func: Any) -> dict[str, Any]:
    selector = func.selector
    return {
        "name": func.name,
        "signature": func.signature,
        "selector": f"0x{bytes(selector).hex()}" if selector else None,
        "state_mutability": func.state_mutability,
        "inputs": [_param_to_dict(p) for p in func.inputs],
        "outputs": [_param_to_dict(p) for p in func.outputs],
    }


def _event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "name": event.name,
        "anonymous": event.anonymous,
        "inputs": [
            {
                "name": p.name,
                "type": p.type_,
                "indexed": p.indexed,
                "internal_type": p.internal_type,
            }
            for p in event.inputs
        ],
    }


def _error_to_dict(error: Any) -> dict[str, Any]:
    return {
        "name": error.name,
        "inputs": [_param_to_dict(p) for p in error.inputs],
    }


def _abi_to_contract(abi: ABI) -> DecompiledContract:
    success = not abi.decompile_error
    return DecompiledContract(
        success=success,
        functions=[_function_to_dict(f) for f in abi.functions],
        events=[_event_to_dict(e) for e in abi.events],
        errors=[_error_to_dict(e) for e in abi.errors],
        storage_slots=[{"index": s.index, "offset": s.offset, "type": s.typ} for s in abi.storage_layout],
        decompile_error=abi.decompile_error,
        storage_error=abi.storage_error,
    )


def analyze_bytecode(
    code_or_address: str,
    *,
    rpc_url: str | None = None,
    timeout_secs: int = _DEFAULT_TIMEOUT_SECS,
    skip_resolving: bool = False,
) -> DecompiledContract:
    """Decompile EVM bytecode (or a contract address) into a structured ABI.

    ``code_or_address`` may be raw hex bytecode (with or without ``0x``
    prefix) or a contract address.  When a plain address is given, ``rpc_url``
    is required so Heimdall can fetch the bytecode.

    Always returns a :class:`DecompiledContract`; check ``.success`` (or
    ``.decompile_error``) to determine whether decompilation produced an ABI.
    """
    heimdall_py = _import_heimdall()
    abi: ABI = heimdall_py.decompile_code(
        code_or_address,
        skip_resolving=skip_resolving,
        extract_storage=True,
        use_cache=True,
        rpc_url=rpc_url,
        timeout_secs=timeout_secs,
    )
    return _abi_to_contract(abi)
