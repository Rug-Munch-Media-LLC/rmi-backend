"""Helios trustless EVM state/storage proof verification wrapper.

Position in the scanner pipeline
---------------------------------
Verifies ``eth_getProof`` responses (EIP-1186 account + storage proofs)
against a locally-synced, consensus-verified execution state root — WITHOUT
trusting the execution RPC provider.

* ``app/heimdall_bytecode.py`` — bytecode → ABI decompilation (works on
  UNVERIFIED contracts).
* ``app/slither_analyzer.py`` — source-level static analysis.
* ``app/helios_proofs.py`` (this module) — trustless state/storage proof
  verification. Answers "is this balance/code/storage value really what the
  chain committed to?" without trusting any single RPC endpoint.

Backed by `helios` (a16z's Rust Ethereum light client,
https://github.com/a16z/helios). Helios syncs the consensus layer via the
light-client protocol, derives the execution state root from the verified
beacon chain, and serves a local JSON-RPC endpoint that re-fetches
``eth_getProof`` from the (untrusted) execution provider and verifies the
returned proofs against the synced state root. Anything it serves has
therefore been checked against consensus — this module delegates to that
verification and adds an optional caller-supplied cross-check.

Integration: subprocess CLI wrapper
-----------------------------------
Helios has NO stable Python binding (the PyPI package named ``helios`` is an
unrelated network-visualization library). The official distribution is a Rust
binary::

    helios ethereum --network mainnet --execution-rpc $ETH_RPC_URL

which starts a local RPC server (default 127.0.0.1:8545). This module spawns
one long-running ``helios`` subprocess per (chain, rpc-config), queries it
over HTTP JSON-RPC with httpx, reuses it across calls, and terminates it via
:func:`shutdown` (also registered with ``atexit``).

The execution RPC MUST support ``eth_getProof`` (Alchemy, Infura, ...);
public load-balanced endpoints frequently do not. Prefer a keyed provider and
pass it as ``execution_rpc`` or set ``HELIOS_<CHAIN>_EXECUTION_RPC`` (e.g.
``HELIOS_MAINNET_EXECUTION_RPC``).

Operational notes (verified 2026-08 against helios 0.11.1)
-----------------------------------------------------------
* Helios's built-in default checkpoint goes stale and public beacon APIs
  prune bootstrap data for old roots. When ``consensus_rpc`` is supplied and
  no explicit ``checkpoint`` is, this module derives a fresh finalized
  epoch-boundary checkpoint from the consensus RPC automatically.
* ``block`` must be at or after helios's synced checkpoint — a light client
  has no deep block history, so ancient block numbers cannot be served.
* Single-node execution providers are strongly preferred. Load-balanced
  public endpoints can return inconsistent block hashes across requests,
  which makes helios clear its block-history cache and refuse state queries
  with ``out of sync`` / ``block not found``.

Error model
-----------
* :class:`HeliosNotAvailable` — the ``helios`` binary is missing (module
  still imports cleanly).
* :class:`HeliosError` — infrastructure failure (cannot sync, RPC error,
  block not found, unexpected payload shape).
* ``False`` — a proof/state-root MISMATCH (verification failure, not a
  crash); the reason is logged via the module logger.

Install (any one)
-----------------
1. Release binary: https://github.com/a16z/helios/releases
   (``helios_linux_amd64.tar.gz`` → extract ``helios`` onto PATH).
2. ``curl https://raw.githubusercontent.com/a16z/helios/master/heliosup/install | bash && heliosup``
3. ``cargo install --git https://github.com/a16z/helios --locked helios``
4. Point ``HELIOS_BIN`` at an existing binary.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import IO, TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from types import TracebackType

__all__ = [
    "HeliosError",
    "HeliosNotAvailable",
    "get_verified_state_root",
    "helios_available",
    "shutdown",
    "verify_account_proof",
    "verify_storage_proof",
]

_LOG = logging.getLogger(__name__)

_RPC_TIMEOUT_SECS = 30.0
_STARTUP_TIMEOUT_SECS = 120.0
_READY_POLL_INTERVAL_SECS = 0.5
_RPC_BIND_IP = "127.0.0.1"

_HELIOS_INSTALL_HINT = (
    "helios binary not found. Install it (https://github.com/a16z/helios):\n"
    "  1. Download a release binary: https://github.com/a16z/helios/releases "
    "(e.g. helios_linux_amd64.tar.gz) and put `helios` on PATH\n"
    "  2. Or install via heliosup: curl https://raw.githubusercontent.com/a16z/helios/master/heliosup/install | bash && heliosup\n"
    "  3. Or build from source: cargo install --git https://github.com/a16z/helios --locked helios\n"
    "  Or set HELIOS_BIN=/path/to/helios"
)

# Normalized helios network names → canonical helios network names.
_CHAIN_ALIASES: dict[str, str] = {
    "ethereum": "mainnet",
    "eth": "mainnet",
    "mainnet": "mainnet",
    "sepolia": "sepolia",
    "holesky": "holesky",
    "base": "base",
    "op-mainnet": "op-mainnet",
    "optimism": "op-mainnet",
    "op": "op-mainnet",
    "linea": "linea",
}

_ETHEREUM_NETWORKS = {"mainnet", "sepolia", "holesky"}
_OPSTACK_NETWORKS = {"base", "op-mainnet"}
_SUPPORTED_CHAINS = sorted(set(_CHAIN_ALIASES.values()))

_BLOCK_TAGS = {"latest", "finalized", "safe", "earliest", "pending"}

_CLIENTS: dict[str, _HeliosClient] = {}
_LOCK = threading.Lock()


class HeliosNotAvailable(RuntimeError):
    """Raised when no usable ``helios`` binary can be resolved."""


class HeliosError(RuntimeError):
    """Raised on helios infrastructure failures (sync, RPC, payload shape)."""


def _normalize_chain(chain: str) -> str:
    """Map a user-facing chain name to a helios network name."""
    normalized = _CHAIN_ALIASES.get(chain.strip().lower())
    if normalized is None:
        raise ValueError(f"unsupported chain {chain!r}; supported: {', '.join(_SUPPORTED_CHAINS)}")
    return normalized


def _block_to_hex(block: int | str) -> str:
    """Convert a block spec (int, hex str, or tag) to a JSON-RPC hex quantity."""
    if isinstance(block, int):
        return hex(block)
    if block in _BLOCK_TAGS:
        return block
    if isinstance(block, str) and block.startswith("0x"):
        return block.lower()
    if isinstance(block, str):
        try:
            return hex(int(block))
        except ValueError as exc:
            raise ValueError(f"invalid block spec: {block!r}") from exc
    raise ValueError(f"invalid block spec: {block!r}")


def _slot_to_hex(slot: int | str) -> str:
    """Convert a storage slot spec (int or hex) to a 32-byte hex quantity."""
    try:
        value = int(slot, 16) if isinstance(slot, str) else slot
    except ValueError as exc:
        raise ValueError(f"invalid storage slot: {slot!r}") from exc
    return f"0x{value:064x}"


def _normalize_hash(value: str) -> str:
    """Lowercase hex without the ``0x`` prefix (for exact hash comparison)."""
    return value.strip().lower().removeprefix("0x")


def _normalize_quantity(value: str) -> int:
    """Parse a hex quantity; ``-1`` sentinel for malformed/missing values."""
    try:
        return int(value, 16)
    except ValueError:
        return -1


def _hashes_equal(a: str, b: str) -> bool:
    """Compare two hex hashes/addresses case- and prefix-insensitively."""
    return _normalize_hash(a) == _normalize_hash(b)


def _proof_nodes_equal(a: Any, b: Any) -> bool:
    """Compare two MPT node-hash lists element-wise (exact match)."""
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return False
    return all(isinstance(x, str) and isinstance(y, str) and _hashes_equal(x, y) for x, y in zip(a, b, strict=True))


def _account_proof_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare the account-level fields of two ``eth_getProof`` responses."""
    for field in ("address", "balance", "nonce", "codeHash", "storageHash", "accountProof"):
        if field not in actual or field not in expected:
            return False
    if not _hashes_equal(str(actual["address"]), str(expected["address"])):
        return False
    if _normalize_quantity(str(actual["balance"])) != _normalize_quantity(str(expected["balance"])):
        return False
    if _normalize_quantity(str(actual["nonce"])) != _normalize_quantity(str(expected["nonce"])):
        return False
    if not _hashes_equal(str(actual["codeHash"]), str(expected["codeHash"])):
        return False
    if not _hashes_equal(str(actual["storageHash"]), str(expected["storageHash"])):
        return False
    return _proof_nodes_equal(actual["accountProof"], expected["accountProof"])


def _storage_proof_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare the ``storageProof`` arrays of two ``eth_getProof`` responses."""
    act = actual.get("storageProof")
    exp = expected.get("storageProof")
    if not isinstance(act, list) or not isinstance(exp, list) or len(act) != len(exp):
        return False
    for a, e in zip(act, exp, strict=True):
        if not isinstance(a, dict) or not isinstance(e, dict):
            return False
        if _normalize_quantity(str(a.get("key", ""))) != _normalize_quantity(str(e.get("key", ""))):
            return False
        if _normalize_quantity(str(a.get("value", ""))) != _normalize_quantity(str(e.get("value", ""))):
            return False
        if not _proof_nodes_equal(a.get("proof"), e.get("proof")):
            return False
    return True


def _free_port() -> int:
    """Ask the OS for an available localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_RPC_BIND_IP, 0))
        return int(sock.getsockname()[1])


def _resolve_binary(helios_bin: str | None = None) -> str:
    """Resolve the helios binary: explicit path → ``HELIOS_BIN`` → PATH."""
    candidate = helios_bin or os.getenv("HELIOS_BIN")
    if candidate:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        raise HeliosNotAvailable(f"HELIOS_BIN points to a non-executable path: {candidate!r}")
    found = shutil.which("helios")
    if found:
        return found
    raise HeliosNotAvailable(_HELIOS_INSTALL_HINT)


def _resolve_execution_rpc(chain: str, execution_rpc: str | None) -> str:
    """Execution RPC: explicit param → ``HELIOS_<CHAIN>_EXECUTION_RPC`` → registry."""
    if execution_rpc:
        return execution_rpc
    env_key = f"HELIOS_{chain.upper()}_EXECUTION_RPC"
    env_val = os.getenv(env_key)
    if env_val:
        return env_val
    try:
        from app.chain_registry import get_rpc_urls  # lazy: keep this module import-light
    except ImportError:  # pragma: no cover - registry always present in this repo
        urls: list[str] = []
    else:
        registry_name = "ethereum" if chain == "mainnet" else chain
        urls = get_rpc_urls(registry_name)
    for url in urls:
        if url.startswith(("http://", "https://")) and "${" not in url:
            return url
    raise HeliosError(
        f"no execution RPC for chain {chain!r}; pass execution_rpc= or set {env_key} "
        "(the provider must support eth_getProof, e.g. Alchemy/Infura)"
    )


def _build_command(
    helios_bin: str,
    chain: str,
    execution_rpc: str,
    *,
    consensus_rpc: str | None,
    checkpoint: str | None,
    port: int,
) -> list[str]:
    """Build the helios CLI argv for the given network."""
    if chain in _ETHEREUM_NETWORKS:
        cmd = [helios_bin, "ethereum", "--network", chain, "--execution-rpc", execution_rpc]
        if consensus_rpc:
            cmd += ["--consensus-rpc", consensus_rpc]
        if checkpoint:
            cmd += ["--checkpoint", checkpoint]
    elif chain in _OPSTACK_NETWORKS:
        cmd = [helios_bin, "opstack", "--network", chain, "--execution-rpc", execution_rpc]
        if consensus_rpc:
            cmd += ["--consensus-rpc", consensus_rpc]
        if checkpoint:
            cmd += ["--ethereum-checkpoint", checkpoint]
    else:  # linea — no consensus/checkpoint flags in the 0.11.1 CLI
        if consensus_rpc or checkpoint:
            raise HeliosError("consensus_rpc/checkpoint are not supported for chain 'linea'")
        cmd = [helios_bin, "linea", "--execution-rpc", execution_rpc]
    cmd += ["--rpc-port", str(port), "--rpc-bind-ip", _RPC_BIND_IP]
    return cmd


def _resolve_checkpoint(consensus_rpc: str) -> str | None:
    """Derive a recent weak-subjectivity checkpoint (epoch-boundary block root).

    Helios's built-in default checkpoint goes stale, and public beacon APIs
    prune light-client bootstrap data for old roots — the failure mode seen on
    mainnet in 2026-08 (``NOT_FOUND: Sync committee branch ... not found``).
    To avoid it, resolve a fresh finalized checkpoint from the consensus RPC:
    the block root of the first slot of the latest finalized epoch.

    Best-effort: returns ``None`` and logs a warning on any failure so helios
    falls back to its built-in default.
    """
    base = consensus_rpc.rstrip("/")
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as http:
            resp = http.get(f"{base}/eth/v1/beacon/headers/finalized")
            if resp.status_code != 200:
                _LOG.warning("could not resolve helios checkpoint: HTTP %s from %s", resp.status_code, base)
                return None
            data = resp.json()
            slot = int(data["data"]["header"]["message"]["slot"])
            checkpoint_slot = (slot // 32) * 32
            root = str(data["data"]["root"])
            if checkpoint_slot < slot:
                # The finalized header is not an epoch boundary; the epoch
                # checkpoint is the last block at or before the boundary slot.
                for _ in range(32):
                    resp = http.get(f"{base}/eth/v1/beacon/headers/{checkpoint_slot}")
                    if resp.status_code != 200:
                        checkpoint_slot -= 1  # skipped block; walk back
                        continue
                    root = str(resp.json()["data"]["root"])
                    break
            checkpoint = f"0x{root.removeprefix('0x')}"
            _LOG.info("resolved helios checkpoint for %s at slot %s: %s", base, slot, checkpoint)
            return checkpoint
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        _LOG.warning("could not resolve helios checkpoint from %s: %s", base, exc)
        return None


class _HeliosClient:
    """Manages one long-running ``helios`` subprocess and its local JSON-RPC.

    The subprocess is spawned lazily on first use and kept alive until
    :meth:`stop` (or interpreter exit via :func:`shutdown`). stderr is
    captured to a temp file so diagnostics survive a failed startup without
    risking pipe-buffer deadlock.
    """

    def __init__(
        self,
        *,
        chain: str,
        execution_rpc: str,
        consensus_rpc: str | None,
        checkpoint: str | None,
        helios_bin: str,
        timeout_secs: float,
    ) -> None:
        self.chain = chain
        self._execution_rpc = execution_rpc
        self._consensus_rpc = consensus_rpc
        self._checkpoint = checkpoint
        self._helios_bin = helios_bin
        self._timeout_secs = timeout_secs
        self._port = _free_port()
        self._proc: subprocess.Popen[bytes] | None = None
        self._http: httpx.Client | None = None
        self._stderr_file: IO[Any] | None = None

    @property
    def http(self) -> httpx.Client:
        """The JSON-RPC client, starting the helios subprocess on first use."""
        if self._http is None or (self._proc is not None and self._proc.poll() is not None):
            self._start()
        if self._http is None:
            raise HeliosError("helios client not initialized")  # pragma: no cover
        return self._http

    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        # Helios's built-in default checkpoint goes stale; derive a recent one
        # from the consensus RPC when the caller did not pin one explicitly.
        checkpoint = self._checkpoint
        if checkpoint is None and self.chain != "linea" and self._consensus_rpc:
            checkpoint = _resolve_checkpoint(self._consensus_rpc)
        cmd = _build_command(
            self._helios_bin,
            self.chain,
            self._execution_rpc,
            consensus_rpc=self._consensus_rpc,
            checkpoint=checkpoint,
            port=self._port,
        )
        # The file must stay open for the subprocess lifetime so stderr can be
        # read on failure; a context manager would close it early.
        self._stderr_file = tempfile.TemporaryFile("w+b")  # noqa: SIM115 - see above
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except OSError as exc:
            raise HeliosError(f"failed to start helios binary {self._helios_bin!r}: {exc}") from exc
        self._http = httpx.Client(
            base_url=f"http://{_RPC_BIND_IP}:{self._port}",
            timeout=httpx.Timeout(self._timeout_secs),
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        """Poll ``web3_clientVersion`` until the local RPC is live."""
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECS
        last_error: str | None = None
        while time.monotonic() < deadline:
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                raise HeliosError(f"helios exited during startup (rc={proc.returncode}): {self._stderr_tail()}")
            http = self._http
            if http is not None:
                try:
                    version = _rpc_call(http, "web3_clientVersion", [])
                    if version:
                        _LOG.info("helios ready (%s) — %s", self.chain, version)
                        return
                except HeliosError as exc:
                    last_error = str(exc)
            time.sleep(_READY_POLL_INTERVAL_SECS)
        raise HeliosError(
            f"helios did not become ready within {_STARTUP_TIMEOUT_SECS:.0f}s: "
            f"{last_error or 'timeout'}\n{self._stderr_tail()}"
        )

    def _stderr_tail(self, max_chars: int = 2000) -> str:
        if self._stderr_file is None:
            return "<no stderr captured>"
        try:
            self._stderr_file.seek(0, os.SEEK_END)
            size = self._stderr_file.tell()
            self._stderr_file.seek(max(0, size - max_chars))
            data = self._stderr_file.read()
            return data.decode("utf-8", "replace")
        except (OSError, ValueError):
            return "<unable to read helios stderr>"

    def stop(self) -> None:
        """Terminate the subprocess and release all resources."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if self._http is not None:
            self._http.close()
        if self._stderr_file is not None:
            with contextlib.suppress(OSError):
                self._stderr_file.close()
        self._proc = None
        self._http = None
        self._stderr_file = None

    def __enter__(self) -> _HeliosClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()


def _rpc_call(client: httpx.Client, method: str, params: list[Any], *, timeout_secs: float | None = None) -> Any:
    """Perform a JSON-RPC call against the helios local endpoint."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        if timeout_secs is not None:
            response = client.post("/", json=payload, timeout=timeout_secs)
        else:
            response = client.post("/", json=payload)
    except httpx.HTTPError as exc:
        raise HeliosError(f"helios RPC {method} failed: {exc}") from exc
    if response.status_code != 200:
        raise HeliosError(f"helios RPC {method} returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise HeliosError(f"helios RPC {method} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HeliosError(f"helios RPC {method} error: unexpected payload {data!r}")
    if data.get("error"):
        raise HeliosError(f"helios RPC {method} error: {data['error']}")
    return data.get("result")


def _get_client(
    chain: str,
    *,
    execution_rpc: str | None,
    consensus_rpc: str | None,
    checkpoint: str | None,
    helios_bin: str | None,
) -> _HeliosClient:
    """Resolve config and return the (cached, lazily started) helios client."""
    normalized = _normalize_chain(chain)
    binary = _resolve_binary(helios_bin)
    rpc = _resolve_execution_rpc(normalized, execution_rpc)
    key = "|".join((normalized, rpc, consensus_rpc or "", checkpoint or "", binary))
    with _LOCK:
        client = _CLIENTS.get(key)
        if client is None:
            client = _HeliosClient(
                chain=normalized,
                execution_rpc=rpc,
                consensus_rpc=consensus_rpc,
                checkpoint=checkpoint,
                helios_bin=binary,
                timeout_secs=_RPC_TIMEOUT_SECS,
            )
            _CLIENTS[key] = client
    return client


def _client_state_root(client: _HeliosClient, block: int | str) -> str:
    """Return the consensus-verified state root for ``block`` (lowercase hex)."""
    block_hex = _block_to_hex(block)
    result = _rpc_call(client.http, "eth_getBlockByNumber", [block_hex, False])
    if not isinstance(result, dict):
        raise HeliosError(f"eth_getBlockByNumber({block_hex}) returned unexpected shape: {result!r}")
    root = result.get("stateRoot")
    if not isinstance(root, str) or not root:
        raise HeliosError(f"block {block_hex} has no stateRoot (block keys: {sorted(result)})")
    return root.lower()


def helios_available(helios_bin: str | None = None) -> bool:
    """Return True if a usable ``helios`` binary is resolvable."""
    try:
        _resolve_binary(helios_bin)
        return True
    except HeliosNotAvailable:
        return False


def get_verified_state_root(
    chain: str,
    block: int | str = "latest",
    *,
    execution_rpc: str | None = None,
    consensus_rpc: str | None = None,
    checkpoint: str | None = None,
    helios_bin: str | None = None,
) -> str:
    """Return the trustless execution state root helios synced for ``block``.

    Helios derives this root from the consensus layer (light-client protocol),
    not from the execution RPC — so it can be used as the anchor for verifying
    externally-supplied ``eth_getProof`` payloads without trusting any provider.

    Raises :class:`HeliosNotAvailable` if the binary is missing and
    :class:`HeliosError` on sync/RPC failures.
    """
    client = _get_client(
        chain,
        execution_rpc=execution_rpc,
        consensus_rpc=consensus_rpc,
        checkpoint=checkpoint,
        helios_bin=helios_bin,
    )
    return _client_state_root(client, block)


def verify_account_proof(
    chain: str,
    address: str,
    block: int | str = "latest",
    proof: dict[str, Any] | None = None,
    *,
    expected_state_root: str | None = None,
    execution_rpc: str | None = None,
    consensus_rpc: str | None = None,
    checkpoint: str | None = None,
    helios_bin: str | None = None,
) -> bool:
    """Trustlessly verify an account (EIP-1186) proof for ``address``.

    The proof is verified against the state root helios synced from consensus
    (helios re-fetches and validates ``eth_getProof`` itself). When ``proof``
    is supplied, the caller's payload must match what helios independently
    served; when ``expected_state_root`` is supplied, it must equal helios's
    verified root for ``block``.

    Returns False (and logs the reason) on any mismatch. Raises
    :class:`HeliosNotAvailable` / :class:`HeliosError` for environment or
    infrastructure failures.
    """
    client = _get_client(
        chain,
        execution_rpc=execution_rpc,
        consensus_rpc=consensus_rpc,
        checkpoint=checkpoint,
        helios_bin=helios_bin,
    )
    state_root = _client_state_root(client, block)
    if expected_state_root is not None and not _hashes_equal(expected_state_root, state_root):
        _LOG.warning(
            "account proof rejected: expected state root %s != verified root %s",
            expected_state_root,
            state_root,
        )
        return False
    block_hex = _block_to_hex(block)
    actual = _rpc_call(client.http, "eth_getProof", [address, [], block_hex])
    if not isinstance(actual, dict):
        _LOG.warning("account proof verification failed: unexpected eth_getProof response: %r", actual)
        return False
    if proof is not None and not _account_proof_matches(actual, proof):
        _LOG.warning(
            "account proof verification failed: caller proof does not match helios-verified proof for %s @ %s",
            address,
            block_hex,
        )
        return False
    return True


def verify_storage_proof(
    chain: str,
    address: str,
    slot: int | str,
    block: int | str = "latest",
    proof: dict[str, Any] | None = None,
    *,
    expected_state_root: str | None = None,
    execution_rpc: str | None = None,
    consensus_rpc: str | None = None,
    checkpoint: str | None = None,
    helios_bin: str | None = None,
) -> bool:
    """Trustlessly verify a storage (EIP-1186) proof for ``slot`` of ``address``.

    Semantics mirror :func:`verify_account_proof`, comparing the
    ``storageProof`` array against helios's independently-verified payload and
    (optionally) the synced state root.
    """
    client = _get_client(
        chain,
        execution_rpc=execution_rpc,
        consensus_rpc=consensus_rpc,
        checkpoint=checkpoint,
        helios_bin=helios_bin,
    )
    state_root = _client_state_root(client, block)
    if expected_state_root is not None and not _hashes_equal(expected_state_root, state_root):
        _LOG.warning(
            "storage proof rejected: expected state root %s != verified root %s",
            expected_state_root,
            state_root,
        )
        return False
    slot_hex = _slot_to_hex(slot)
    block_hex = _block_to_hex(block)
    actual = _rpc_call(client.http, "eth_getProof", [address, [slot_hex], block_hex])
    if not isinstance(actual, dict):
        _LOG.warning("storage proof verification failed: unexpected eth_getProof response: %r", actual)
        return False
    if proof is not None and not _storage_proof_matches(actual, proof):
        _LOG.warning(
            "storage proof verification failed: caller proof does not match helios-verified proof for %s slot %s @ %s",
            address,
            slot_hex,
            block_hex,
        )
        return False
    return True


def shutdown() -> None:
    """Terminate all managed helios subprocesses (also runs at exit)."""
    with _LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        client.stop()


atexit.register(shutdown)
