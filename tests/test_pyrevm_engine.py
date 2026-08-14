"""Unit tests for ``app.pyrevm_engine`` — pyrevm EVM and RPC fetchers mocked.

No real RPC, no real pyrevm EVM execution: the module's ``EVM`` class and the
RPC helpers (``_fetch_code`` / ``_resolve_pair`` / ``_fetch_decimals``) are
monkeypatched with an in-memory fake EVM that implements a scriptable mini-ERC20
(``transfer`` / ``balanceOf`` over the same storage layout the module's
slot-discovery probes expect).  Covers:

* ABI helpers (calldata encoding, keccak mapping keys, revert-reason decoding);
* the ``SellSimulationResult`` dataclass contract (incl. ``is_honeypot``);
* balance-mapping slot discovery (found / balanceOf-reverts / never seen);
* every graceful-failure path of ``simulate_sell_round_trip`` (invalid address,
  non-positive amount, EOA/no code, undiscoverable slot, internal exception);
* full simulation flows: clean token, no-sell honeypot, all-transfers-revert,
  anti-whale, blacklist, async variant.

[REVIEW] ``_decode_revert_reason`` is tested against its *actual* layout
expectation: the decoder reads the length word at ``payload[36+offset:68+offset]``
which only yields the revert string when the Error(string) offset word is 0.
Solidity's standard ABI encoding for ``revert("...")`` uses offset=32, which the
current decoder reports as ``revert (empty Error(string))``.  This looks like a
decoder convention mismatch rather than a test error — flagged for the module
owner to confirm against real pyrevm output before relying on decoded reasons.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pyrevm")

from app import pyrevm_engine as pe  # pyrevm must be present before importing the engine

TOKEN = "0x" + "11" * 20
SELLER = "0x" + "aa" * 20
RECIPIENT = "0x" + "bb" * 20
PAIR = "0x" + "cd" * 20

# Hardcoded golden values computed with eth_utils.keccak (regression anchors,
# not self-referential computations).
_GOLDEN_ZERO_SLOT0 = 0xAD3228B676F7D3CD4284A5443F17F1962B36E491B30A40B2405849E597BA5FB5
_GOLDEN_ZERO_SLOT1 = 0xA6EEF7E35ABE7026729641147F7915573C7E97B47EFA546F5F6E3230263BCB49


# ── Revert-data fixtures ─────────────────────────────────────────────────────


def _error_string_hex(offset_word: int) -> str:
    """ABI-encoded ``Error(string)`` for the message "TEST" with a chosen offset."""
    data = b"TEST" + b"\x00" * 28
    payload = b"\x08\xc3\x79\xa0" + offset_word.to_bytes(32, "big") + (4).to_bytes(32, "big") + data
    return "0x" + payload.hex()


def _panic_hex(code: int) -> str:
    payload = b"\x4e\x48\x7b\x71" + code.to_bytes(32, "big")
    return "0x" + payload.hex()


# Standard Solidity ABI layout for revert("TEST"); see [REVIEW] note above.
_STD_ERROR_HEX = _error_string_hex(offset_word=32)


def _revert_message(hex_output: str) -> str:
    return f"execution reverted: output: {hex_output}"


# ── Fake EVM ─────────────────────────────────────────────────────────────────


class FakeEVM:
    """In-memory EVM with a scriptable mini-ERC20 ``transfer``/``balanceOf``.

    Storage is keyed by ``(address, slot_key)`` like pyrevm.  Balance reads
    mirror the module's slot-discovery scan (first non-zero value across the
    scanned mapping slots) — exactly what ``_discover_balance_slot`` relies on.
    ``transfer`` moves balances within the discovered slot and reverts
    (``RuntimeError``) according to the scripted policy.
    """

    def __init__(
        self,
        *,
        revert_all: bool = False,
        revert_recipients: set[str] | tuple[str, ...] = (),
        max_transfer: int | None = None,
        balance_of_reverts: bool = False,
        ignore_markers: bool = False,
    ) -> None:
        self.store: dict[tuple[str, int], int] = {}
        self.revert_all = revert_all
        self.revert_recipients = set(revert_recipients)
        self.max_transfer = max_transfer
        self.balance_of_reverts = balance_of_reverts
        self.ignore_markers = ignore_markers
        self.block_env: object | None = None
        self.accounts: dict[str, object] = {}
        self.snapshot_count = 0
        self.revert_count = 0

    # pyrevm surface used by the engine
    def set_block_env(self, env: object) -> None:
        self.block_env = env

    def insert_account_info(self, address: str, info: object) -> None:
        self.accounts[address] = info

    def insert_account_storage(self, address: str, key: int, value: int) -> None:
        self.store[(address, key)] = value

    def storage(self, address: str, key: int) -> int:
        return self.store.get((address, key), 0)

    def snapshot(self) -> int:
        self.snapshot_count += 1
        return self.snapshot_count

    def revert(self, checkpoint: int) -> None:
        self.revert_count += 1

    # mini-ERC20 execution
    def _find_balance(self, token: str, holder: str) -> tuple[int | None, int]:
        for slot_idx in range(pe.MAX_SLOT_SCAN):
            key = pe._balance_slot_key(holder, slot_idx)
            val = self.store.get((token, key), 0)
            if val:
                return slot_idx, val
        return None, 0

    def message_call(self, caller: str, to: str, calldata: bytes) -> bytes:
        if isinstance(calldata, str):
            calldata = bytes.fromhex(calldata[2:])
        sel = calldata[:4].hex()
        if sel == pe.SELECTOR_BALANCE_OF:
            if self.balance_of_reverts:
                raise RuntimeError(_revert_message(_STD_ERROR_HEX))
            holder = "0x" + calldata[4:36][12:].hex()
            if self.ignore_markers:
                return (0).to_bytes(32, "big")
            _slot, bal = self._find_balance(to, holder)
            return bal.to_bytes(32, "big")
        if sel == pe.SELECTOR_TRANSFER:
            recipient = "0x" + calldata[4:36][12:].hex()
            amount = int.from_bytes(calldata[36:68], "big")
            if (
                self.revert_all
                or recipient in self.revert_recipients
                or (self.max_transfer is not None and amount > self.max_transfer)
            ):
                raise RuntimeError(_revert_message(_STD_ERROR_HEX))
            slot_idx, caller_bal = self._find_balance(to, caller)
            if slot_idx is None or caller_bal < amount:
                raise RuntimeError(_revert_message(_STD_ERROR_HEX))
            self.store[(to, pe._balance_slot_key(caller, slot_idx))] = caller_bal - amount
            recipient_key = (to, pe._balance_slot_key(recipient, slot_idx))
            self.store[recipient_key] = self.store.get(recipient_key, 0) + amount
            return b""
        raise RuntimeError(_revert_message(_STD_ERROR_HEX))


# ── Fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def mock_evm_rpc(monkeypatch):
    """Patch pyrevm constructors + RPC fetchers so no real pyrevm/network runs."""
    monkeypatch.setattr(pe, "BlockEnv", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(pe, "AccountInfo", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(pe, "_resolve_pair", lambda *args: None)
    monkeypatch.setattr(pe, "_fetch_decimals", lambda *args: 18)
    monkeypatch.setattr(pe, "_fetch_code", lambda *args: b"\x60\x00")
    return monkeypatch


def _install_evm(monkeypatch, evm: FakeEVM) -> None:
    monkeypatch.setattr(pe, "EVM", lambda: evm)


# ── ABI helpers ──────────────────────────────────────────────────────────────


def test_encode_address_left_pads_to_32_bytes() -> None:
    encoded = pe._encode_address(TOKEN)
    assert len(encoded) == 32
    assert encoded[12:] == bytes.fromhex(TOKEN[2:])
    assert encoded[:12] == b"\x00" * 12


def test_balance_of_calldata_selector_and_encoding() -> None:
    cd = pe._balance_of_calldata(SELLER)
    assert cd[:4].hex() == pe.SELECTOR_BALANCE_OF
    assert cd[4:36][12:] == bytes.fromhex(SELLER[2:])


def test_transfer_calldata_selector_recipient_amount() -> None:
    cd = pe._transfer_calldata(RECIPIENT, 250)
    assert cd[:4].hex() == pe.SELECTOR_TRANSFER
    assert cd[4:36][12:] == bytes.fromhex(RECIPIENT[2:])
    assert int.from_bytes(cd[36:68], "big") == 250


def test_balance_slot_key_matches_keccak_mapping_layout() -> None:
    assert pe._balance_slot_key(pe.ZERO_ADDRESS, 0) == _GOLDEN_ZERO_SLOT0
    assert pe._balance_slot_key(pe.ZERO_ADDRESS, 1) == _GOLDEN_ZERO_SLOT1
    # distinct holders / slots must produce distinct keys
    assert pe._balance_slot_key(pe.ZERO_ADDRESS, 0) != pe._balance_slot_key(pe.ZERO_ADDRESS, 1)
    assert pe._balance_slot_key(SELLER, 0) != pe._balance_slot_key(RECIPIENT, 0)
    assert 0 <= pe._balance_slot_key(SELLER, 0) < 2**256


def test_decode_revert_reason_error_string_supported_layout() -> None:
    # Layout the decoder handles (offset word == 0): selector + length + data.
    reason = pe._decode_revert_reason(_revert_message(_error_string_hex(offset_word=0)))
    assert reason == "TEST"


def test_decode_revert_reason_standard_abi_layout() -> None:
    # [REVIEW] Standard Solidity Error(string) uses offset=32; the current
    # decoder reports it as an empty string.  Documented so the behavior change
    # is visible if the module owner fixes the decoder.
    reason = pe._decode_revert_reason(_revert_message(_STD_ERROR_HEX))
    assert reason == "revert (empty Error(string))"


def test_decode_revert_reason_panic() -> None:
    reason = pe._decode_revert_reason(_revert_message(_panic_hex(0x11)))
    assert reason == "Panic(0x11)"


def test_decode_revert_reason_no_reason_and_raw() -> None:
    assert pe._decode_revert_reason(_revert_message("0x")) == "revert (no reason)"
    assert pe._decode_revert_reason(_revert_message("0x1234")) == "0x1234"
    # message without an `output:` marker is truncated, not mis-parsed
    assert pe._decode_revert_reason("boom") == "boom"


def test_parse_execution_error_wraps_decode() -> None:
    assert pe._parse_execution_error(RuntimeError(_revert_message(_STD_ERROR_HEX))) == "revert (empty Error(string))"


# ── Result dataclass ─────────────────────────────────────────────────────────


def test_result_dataclass_defaults() -> None:
    res = pe.SellSimulationResult(token_address=TOKEN, chain="ethereum", simulation_success=False)
    assert res.simulation_success is False
    assert res.can_sell is False
    assert res.can_buy is False
    assert res.method == "pyrevm-state-override-transfer"
    assert res.warnings == []
    assert res.risk == "unknown"
    assert res.is_honeypot is False


def test_result_is_honeypot_signature() -> None:
    res = pe.SellSimulationResult(
        token_address=TOKEN,
        chain="ethereum",
        simulation_success=True,
        can_sell=True,
        sell_to_pair_reverted=True,
    )
    assert res.is_honeypot is True
    res.sell_to_pair_reverted = False
    assert res.is_honeypot is False
    res.can_sell = False
    res.sell_to_pair_reverted = True
    assert res.is_honeypot is False


# ── Slot discovery ───────────────────────────────────────────────────────────


def test_discover_balance_slot_finds_mapping_slot() -> None:
    evm = FakeEVM()
    assert pe._discover_balance_slot(evm, TOKEN, SELLER) == 0
    # marker must be reset to zero after the probe (the slot key remains, value 0)
    assert evm.store[(TOKEN, pe._balance_slot_key(SELLER, 0))] == 0


def test_discover_balance_slot_none_when_balance_of_reverts() -> None:
    evm = FakeEVM(balance_of_reverts=True)
    assert pe._discover_balance_slot(evm, TOKEN, SELLER) is None


def test_discover_balance_slot_none_when_marker_never_seen() -> None:
    evm = FakeEVM(ignore_markers=True)
    assert pe._discover_balance_slot(evm, TOKEN, SELLER) is None


def test_seed_and_read_balance_roundtrip() -> None:
    evm = FakeEVM()
    pe._seed_balances(evm, TOKEN, 3, {SELLER: 42})
    assert pe._read_balance(evm, TOKEN, 3, SELLER) == 42
    assert pe._read_balance(evm, TOKEN, 3, RECIPIENT) == 0


def test_transfer_probe_moves_balance_and_reverts_journal() -> None:
    evm = FakeEVM()
    pe._seed_balances(evm, TOKEN, 0, {SELLER: 1000, RECIPIENT: 0})
    out = pe._transfer_probe(evm, TOKEN, 0, SELLER, RECIPIENT, 250)
    assert out.success is True
    assert out.received == 250
    assert evm.snapshot_count == 1
    assert evm.revert_count == 1


def test_transfer_probe_reports_revert_reason() -> None:
    evm = FakeEVM(revert_recipients={RECIPIENT})
    pe._seed_balances(evm, TOKEN, 0, {SELLER: 1000})
    out = pe._transfer_probe(evm, TOKEN, 0, SELLER, RECIPIENT, 250)
    assert out.success is False
    assert out.reason == "revert (empty Error(string))"  # standard ABI layout — see [REVIEW]


def test_probe_blacklist_flags_random_recipient_only() -> None:
    evm = FakeEVM(revert_recipients={pe.ONE_ADDRESS})
    hit, flagged = pe._probe_blacklist(evm, TOKEN, 0, SELLER, 1000, (pe.ONE_ADDRESS, pe.ZERO_ADDRESS, pe.DEAD_ADDRESS))
    assert hit is True
    assert flagged == [pe.ONE_ADDRESS]
    # zero/burn destinations alone must not trigger the blacklist signal
    evm2 = FakeEVM(revert_recipients={pe.ZERO_ADDRESS, pe.DEAD_ADDRESS})
    hit2, flagged2 = pe._probe_blacklist(
        evm2, TOKEN, 0, SELLER, 1000, (pe.ONE_ADDRESS, pe.ZERO_ADDRESS, pe.DEAD_ADDRESS)
    )
    assert hit2 is False
    assert flagged2 == []


# ── simulate_sell_round_trip: graceful failure paths ─────────────────────────


def test_simulate_invalid_address_graceful(mock_evm_rpc) -> None:
    res = pe.simulate_sell_round_trip("0x123")
    assert res.simulation_success is False
    assert any("Invalid EVM address" in w for w in res.warnings)


def test_simulate_non_string_address_graceful(mock_evm_rpc) -> None:
    res = pe.simulate_sell_round_trip(12345)  # type: ignore[arg-type]
    assert res.simulation_success is False
    assert res.token_address == ""


def test_simulate_non_positive_amount_graceful(mock_evm_rpc) -> None:
    res = pe.simulate_sell_round_trip(TOKEN, amount=0)
    assert res.simulation_success is False
    assert any("amount must be > 0" in w for w in res.warnings)


def test_simulate_no_contract_code_graceful(mock_evm_rpc, monkeypatch) -> None:
    monkeypatch.setattr(pe, "_fetch_code", lambda *args: b"")
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is False
    assert any("No contract code" in w for w in res.warnings)


def test_simulate_slot_discovery_failure_graceful(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM())
    monkeypatch.setattr(pe, "_discover_balance_slot", lambda *args: None)
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is False
    assert any("Could not map balance storage slot" in w for w in res.warnings)


def test_simulate_internal_exception_graceful(mock_evm_rpc, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pe, "_simulate_with_evm", boom)
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is False
    assert any("Simulation failed" in w for w in res.warnings)


# ── simulate_sell_round_trip: full flows ─────────────────────────────────────


def test_simulate_clean_token_succeeds(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM())
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is True
    assert res.can_sell is True
    assert res.can_buy is False
    assert res.sell_to_pair_reverted is False
    assert res.output_amount == 1000
    assert res.tax_pct == 0.0
    assert res.sell_tax_pct is None
    assert res.buy_tax_pct is None
    assert res.blacklist_hit is False
    assert res.anti_whale_hit is False
    assert res.risk == "safe"
    assert res.balance_slot == 0
    assert any("No Uniswap V2 pair resolved" in w for w in res.warnings)


def test_simulate_honeypot_sell_to_pair_reverts(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM(revert_recipients={PAIR}))
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000, pair_address=PAIR)
    assert res.simulation_success is True
    assert res.can_sell is True
    assert res.sell_to_pair_reverted is True
    assert res.is_honeypot is True
    assert res.risk == "critical"
    assert any("honeypot" in w.lower() for w in res.warnings)


def test_simulate_all_transfers_revert(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM(revert_all=True))
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is True
    assert res.can_sell is False
    assert res.can_buy is False
    assert res.all_transfers_revert is True
    assert res.output_amount == 0
    assert res.tax_pct == 100.0
    assert res.risk == "critical"
    # [REVIEW] decode of the standard ABI Error(string) — see module docstring note
    assert res.revert_reason == "revert (empty Error(string))"


def test_simulate_anti_whale_detected(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM(max_transfer=1000))
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is True
    assert res.can_sell is True
    assert res.anti_whale_hit is True
    assert res.risk == "dangerous"
    assert any("anti-whale" in w.lower() or "max-tx" in w.lower() for w in res.warnings)


def test_simulate_blacklist_detected(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM(revert_recipients={pe.ONE_ADDRESS}))
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000)
    assert res.simulation_success is True
    assert res.can_sell is True
    assert res.blacklist_hit is True
    assert res.risk == "dangerous"
    assert any("blacklist" in w.lower() for w in res.warnings)


def test_simulate_unknown_chain_falls_back_to_default(mock_evm_rpc, monkeypatch) -> None:
    _install_evm(monkeypatch, FakeEVM())
    res = pe.simulate_sell_round_trip(TOKEN, amount=1000, chain="not-a-real-chain")
    assert res.simulation_success is True
    assert res.chain == "not-a-real-chain"


def test_simulate_amount_defaults_to_whole_token(mock_evm_rpc, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sim(chain, token, cfg, amount, pair, timeout):
        captured.update(chain=chain, token=token, cfg=cfg, amount=amount, pair=pair)
        return pe.SellSimulationResult(token_address=token, chain=chain, simulation_success=True)

    monkeypatch.setattr(pe, "_simulate_with_evm", fake_sim)
    res = pe.simulate_sell_round_trip(TOKEN)
    assert res.simulation_success is True
    assert captured["amount"] == 10**18  # 18 decimals via mocked _fetch_decimals
    assert captured["token"] == TOKEN


def test_simulate_rpc_url_override_replaces_chain_config(mock_evm_rpc, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sim(chain, token, cfg, amount, pair, timeout):
        captured.update(chain=chain, cfg=cfg, amount=amount)
        return pe.SellSimulationResult(token_address=token, chain=chain, simulation_success=True)

    monkeypatch.setattr(pe, "_simulate_with_evm", fake_sim)
    res = pe.simulate_sell_round_trip(TOKEN, amount=5, rpc_url="https://example.com/rpc")
    assert res.simulation_success is True
    assert captured["chain"] == "ethereum"
    assert captured["amount"] == 5
    assert captured["cfg"].rpc_urls == ("https://example.com/rpc",)  # type: ignore[union-attr]


async def test_asimulate_sell_round_trip_async_variant(mock_evm_rpc, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_sync(token, amount, **kwargs):
        captured.update(token=token, amount=amount, kwargs=kwargs)
        return pe.SellSimulationResult(
            token_address=token, chain=kwargs.get("chain", "ethereum"), simulation_success=True
        )

    monkeypatch.setattr(pe, "simulate_sell_round_trip", fake_sync)
    res = await pe.asimulate_sell_round_trip(TOKEN, chain="bsc")
    assert res.simulation_success is True
    assert captured["token"] == TOKEN
    assert captured["amount"] is None
    assert captured["kwargs"]["chain"] == "bsc"
