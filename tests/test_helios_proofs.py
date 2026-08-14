"""Unit tests for ``app.helios_proofs`` — subprocess + httpx fully mocked.

No helios binary is spawned and no RPC is touched: binary resolution, client
construction, and every JSON-RPC call are monkeypatched. Covers:

* pure helpers: chain normalization, block/slot/hash/quantity conversion, and
  the account/storage proof comparators (match + mismatch paths);
* ``_build_command`` per network family (ethereum / opstack / linea);
* binary resolution (explicit path, ``HELIOS_BIN``, PATH, missing) and
  ``helios_available`` true/false;
* ``_resolve_execution_rpc`` precedence (param -> env -> registry -> error);
* ``get_verified_state_root``;
* ``verify_account_proof`` / ``verify_storage_proof`` true + false paths and
  ``HeliosNotAvailable`` propagation when the binary is missing;
* ``shutdown`` terminates all managed clients.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest

from app import helios_proofs as hp

ADDR = "0x" + "11" * 20
STATE_ROOT = "0x" + "ab" * 32

_ACCOUNT_PROOF = {
    "address": ADDR,
    "balance": "0x1",
    "nonce": "0x2",
    "codeHash": "0x" + "cd" * 32,
    "storageHash": "0x" + "ef" * 32,
    "accountProof": ["0x" + "aa" * 32, "0x" + "bb" * 32],
}

_STORAGE_PROOF = {
    **_ACCOUNT_PROOF,
    "storageProof": [{"key": "0x5", "value": "0x01", "proof": ["0x" + "11" * 32]}],
}


# ── Pure helpers ────────────────────────────────────────────────────────────


def test_normalize_chain() -> None:
    assert hp._normalize_chain("ethereum") == "mainnet"
    assert hp._normalize_chain("ETH") == "mainnet"
    assert hp._normalize_chain("mainnet") == "mainnet"
    assert hp._normalize_chain("sepolia") == "sepolia"
    assert hp._normalize_chain("base") == "base"
    assert hp._normalize_chain("op-mainnet") == "op-mainnet"
    assert hp._normalize_chain("optimism") == "op-mainnet"
    assert hp._normalize_chain("linea") == "linea"


def test_normalize_chain_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported chain"):
        hp._normalize_chain("bogus-chain")


def test_block_to_hex() -> None:
    assert hp._block_to_hex(123) == "0x7b"
    assert hp._block_to_hex("latest") == "latest"
    assert hp._block_to_hex("0xABC") == "0xabc"
    assert hp._block_to_hex("123") == "0x7b"
    with pytest.raises(ValueError, match="invalid block spec"):
        hp._block_to_hex("not-a-block")


def test_slot_to_hex() -> None:
    assert hp._slot_to_hex(5) == "0x" + "0" * 63 + "5"
    assert hp._slot_to_hex("0x5") == "0x" + "0" * 63 + "5"
    with pytest.raises(ValueError, match="invalid storage slot"):
        hp._slot_to_hex("nope")


def test_hash_and_quantity_normalization() -> None:
    assert hp._normalize_hash("0xABC") == "abc"
    assert hp._normalize_hash("0XABC") == "abc"
    assert hp._normalize_quantity("0x10") == 16
    assert hp._normalize_quantity("garbage") == -1
    assert hp._hashes_equal("0xABC", "0xabc") is True
    assert hp._hashes_equal("0xABC", "0xdef") is False


def test_proof_nodes_equal() -> None:
    a = ["0x" + "aa" * 32, "0x" + "bb" * 32]
    assert hp._proof_nodes_equal(a, ["0X" + "AA" * 32, "0x" + "bb" * 32]) is True  # case-insensitive
    assert hp._proof_nodes_equal(a, ["0x" + "aa" * 32]) is False
    assert hp._proof_nodes_equal(a, ["not-a-hash", "0x" + "bb" * 32]) is False
    assert hp._proof_nodes_equal("not-a-list", a) is False


def test_account_proof_matches() -> None:
    assert hp._account_proof_matches(dict(_ACCOUNT_PROOF), dict(_ACCOUNT_PROOF)) is True
    mismatch = dict(_ACCOUNT_PROOF)
    mismatch["balance"] = "0xff"
    assert hp._account_proof_matches(mismatch, _ACCOUNT_PROOF) is False
    missing = {k: v for k, v in _ACCOUNT_PROOF.items() if k != "nonce"}
    assert hp._account_proof_matches(missing, _ACCOUNT_PROOF) is False
    bad_nodes = dict(_ACCOUNT_PROOF)
    bad_nodes["accountProof"] = ["0x" + "ff" * 32]
    assert hp._account_proof_matches(bad_nodes, _ACCOUNT_PROOF) is False


def test_storage_proof_matches() -> None:
    assert hp._storage_proof_matches(dict(_STORAGE_PROOF), dict(_STORAGE_PROOF)) is True
    mismatch = dict(_STORAGE_PROOF)
    mismatch["storageProof"] = [{"key": "0x5", "value": "0x99", "proof": ["0x" + "11" * 32]}]
    assert hp._storage_proof_matches(mismatch, _STORAGE_PROOF) is False
    no_proof = dict(_STORAGE_PROOF)
    no_proof["storageProof"] = []
    assert hp._storage_proof_matches(no_proof, _STORAGE_PROOF) is False


# ── _build_command ──────────────────────────────────────────────────────────


def test_build_command_ethereum() -> None:
    cmd = hp._build_command(
        "/bin/helios", "mainnet", "http://rpc", consensus_rpc="http://beacon", checkpoint="0xcc", port=8545
    )
    assert cmd[:4] == ["/bin/helios", "ethereum", "--network", "mainnet"]
    assert "--execution-rpc" in cmd
    assert "http://rpc" in cmd
    assert "--consensus-rpc" in cmd
    assert "http://beacon" in cmd
    assert "--checkpoint" in cmd
    assert "0xcc" in cmd
    assert "--rpc-port" in cmd
    assert "8545" in cmd


def test_build_command_opstack_uses_ethereum_checkpoint() -> None:
    cmd = hp._build_command(
        "/bin/helios", "base", "http://rpc", consensus_rpc="http://beacon", checkpoint="0xcc", port=8545
    )
    assert cmd[:3] == ["/bin/helios", "opstack", "--network"]
    assert "--ethereum-checkpoint" in cmd
    assert "--checkpoint" not in cmd


def test_build_command_linea_rejects_checkpoint() -> None:
    with pytest.raises(hp.HeliosError, match="not supported for chain 'linea'"):
        hp._build_command(
            "/bin/helios", "linea", "http://rpc", consensus_rpc="http://beacon", checkpoint=None, port=8545
        )
    cmd = hp._build_command("/bin/helios", "linea", "http://rpc", consensus_rpc=None, checkpoint=None, port=8545)
    assert cmd[:3] == ["/bin/helios", "linea", "--execution-rpc"]


# ── Binary resolution / availability ────────────────────────────────────────


def test_resolve_binary_explicit_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    bin_path = tmp_path / "helios"
    bin_path.write_text("#!/bin/sh\n")
    bin_path.chmod(0o755)
    assert hp._resolve_binary(str(bin_path)) == str(bin_path)


def test_resolve_binary_non_executable_path_raises(tmp_path: Any) -> None:
    non_exec = tmp_path / "helios"
    non_exec.write_text("#!/bin/sh\n")
    non_exec.chmod(0o644)
    with pytest.raises(hp.HeliosNotAvailable, match="non-executable"):
        hp._resolve_binary(str(non_exec))


def test_resolve_binary_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    bin_path = tmp_path / "helios"
    bin_path.write_text("#!/bin/sh\n")
    bin_path.chmod(0o755)
    monkeypatch.setenv("HELIOS_BIN", str(bin_path))
    assert hp._resolve_binary() == str(bin_path)


def test_resolve_binary_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/helios" if name == "helios" else None)
    assert hp._resolve_binary() == "/usr/local/bin/helios"


def test_resolve_binary_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(hp.HeliosNotAvailable, match="helios binary not found"):
        hp._resolve_binary()


def test_helios_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hp, "_resolve_binary", lambda helios_bin=None: "/bin/helios")
    assert hp.helios_available() is True
    monkeypatch.setattr(
        hp, "_resolve_binary", lambda helios_bin=None: (_ for _ in ()).throw(hp.HeliosNotAvailable("nope"))
    )
    assert hp.helios_available() is False


# ── _resolve_execution_rpc ──────────────────────────────────────────────────


def test_resolve_execution_rpc_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hp._resolve_execution_rpc("mainnet", "http://explicit") == "http://explicit"
    monkeypatch.setenv("HELIOS_MAINNET_EXECUTION_RPC", "http://from-env")
    assert hp._resolve_execution_rpc("mainnet", None) == "http://from-env"


def test_resolve_execution_rpc_registry_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELIOS_MAINNET_EXECUTION_RPC", raising=False)
    monkeypatch.setattr(
        "app.chain_registry.get_rpc_urls", lambda chain: ["${SECRET}", "https://rpc.example.invalid"], raising=False
    )
    assert hp._resolve_execution_rpc("mainnet", None) == "https://rpc.example.invalid"


def test_resolve_execution_rpc_none_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELIOS_MAINNET_EXECUTION_RPC", raising=False)
    monkeypatch.setattr("app.chain_registry.get_rpc_urls", lambda chain: [], raising=False)
    with pytest.raises(hp.HeliosError, match="no execution RPC"):
        hp._resolve_execution_rpc("mainnet", None)


# ── Verify functions (client + RPC mocked) ──────────────────────────────────


class _FakeClient:
    """Minimal stand-in: only ``.http`` is touched when ``_rpc_call`` is patched."""

    def __init__(self) -> None:
        self.http = object()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _install_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state_root: str = STATE_ROOT,
    proof: dict[str, Any],
    fail_get_proof: bool = False,
) -> list[tuple[str, list[Any]]]:
    """Patch ``_get_client``/``_rpc_call`` with a scripted in-memory RPC."""
    calls: list[tuple[str, list[Any]]] = []

    def fake_get_client(chain: str, **kwargs: Any) -> _FakeClient:
        return _FakeClient()

    def fake_rpc_call(client: Any, method: str, params: list[Any], **kwargs: Any) -> Any:
        calls.append((method, params))
        if method == "eth_getBlockByNumber":
            return {"stateRoot": state_root}
        if method == "eth_getProof":
            if fail_get_proof:
                return None
            return dict(proof)
        return None

    monkeypatch.setattr(hp, "_get_client", fake_get_client)
    monkeypatch.setattr(hp, "_rpc_call", fake_rpc_call)
    return calls


def test_get_verified_state_root(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    assert hp.get_verified_state_root("ethereum", block=123, execution_rpc="http://rpc") == STATE_ROOT
    assert calls[0] == ("eth_getBlockByNumber", ["0x7b", False])


def test_get_verified_state_root_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    monkeypatch.setattr(hp, "_rpc_call", lambda client, method, params, **kw: {"noStateRoot": True})
    with pytest.raises(hp.HeliosError, match="no stateRoot"):
        hp.get_verified_state_root("ethereum", execution_rpc="http://rpc")


def test_verify_account_proof_true_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    result = hp.verify_account_proof(
        "ethereum",
        ADDR,
        proof=_ACCOUNT_PROOF,
        expected_state_root=STATE_ROOT,
        execution_rpc="http://rpc",
    )
    assert result is True
    assert ("eth_getProof", [ADDR, [], "latest"]) in calls


def test_verify_account_proof_state_root_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    result = hp.verify_account_proof(
        "ethereum",
        ADDR,
        proof=_ACCOUNT_PROOF,
        expected_state_root="0x" + "ff" * 32,
        execution_rpc="http://rpc",
    )
    assert result is False
    # eth_getProof must not even be attempted once the root check fails.
    assert not any(method == "eth_getProof" for method, _ in calls)


def test_verify_account_proof_caller_proof_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    tampered = dict(_ACCOUNT_PROOF)
    tampered["balance"] = "0x999"
    result = hp.verify_account_proof("ethereum", ADDR, proof=tampered, execution_rpc="http://rpc")
    assert result is False


def test_verify_account_proof_unexpected_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, proof=_ACCOUNT_PROOF, fail_get_proof=True)
    result = hp.verify_account_proof("ethereum", ADDR, execution_rpc="http://rpc")
    assert result is False


def test_verify_account_proof_no_caller_proof_true(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, proof=_ACCOUNT_PROOF)
    result = hp.verify_account_proof("ethereum", ADDR, proof=None, execution_rpc="http://rpc")
    assert result is True


def test_verify_storage_proof_true_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_client(monkeypatch, proof=_STORAGE_PROOF)
    result = hp.verify_storage_proof(
        "ethereum",
        ADDR,
        slot=5,
        proof=_STORAGE_PROOF,
        expected_state_root=STATE_ROOT,
        execution_rpc="http://rpc",
    )
    assert result is True
    slot_hex = "0x" + "0" * 63 + "5"
    assert ("eth_getProof", [ADDR, [slot_hex], "latest"]) in calls


def test_verify_storage_proof_slot_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_client(monkeypatch, proof=_STORAGE_PROOF)
    tampered = dict(_STORAGE_PROOF)
    tampered["storageProof"] = [{"key": "0x6", "value": "0x01", "proof": ["0x" + "11" * 32]}]
    result = hp.verify_storage_proof("ethereum", ADDR, slot=5, proof=tampered, execution_rpc="http://rpc")
    assert result is False


def test_verify_storage_proof_state_root_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_client(monkeypatch, proof=_STORAGE_PROOF)
    result = hp.verify_storage_proof(
        "ethereum",
        ADDR,
        slot=5,
        proof=_STORAGE_PROOF,
        expected_state_root="0x" + "ff" * 32,
        execution_rpc="http://rpc",
    )
    assert result is False
    assert not any(method == "eth_getProof" for method, _ in calls)


def test_verify_propagation_of_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(chain: str, **kwargs: Any) -> Any:
        raise hp.HeliosNotAvailable("helios binary not found")

    monkeypatch.setattr(hp, "_get_client", missing)
    with pytest.raises(hp.HeliosNotAvailable):
        hp.verify_account_proof("ethereum", ADDR, execution_rpc="http://rpc")
    with pytest.raises(hp.HeliosNotAvailable):
        hp.verify_storage_proof("ethereum", ADDR, slot=0, execution_rpc="http://rpc")


# ── shutdown ────────────────────────────────────────────────────────────────


def test_shutdown_stops_all_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    c1, c2 = _FakeClient(), _FakeClient()
    hp._CLIENTS["mainnet|rpc1|||/bin/helios"] = c1
    hp._CLIENTS["base|rpc2|||/bin/helios"] = c2
    try:
        hp.shutdown()
    finally:
        hp._CLIENTS.clear()
    assert c1.stopped is True
    assert c2.stopped is True
    assert hp._CLIENTS == {}
