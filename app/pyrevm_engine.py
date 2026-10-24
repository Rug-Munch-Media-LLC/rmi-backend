"""
pyrevm sell-simulation engine — honeypot / transfer-tax / blacklist / anti-whale
detection via local EVM execution of the *real* token bytecode.

Why state overrides instead of a full fork
------------------------------------------
``pyrevm`` 0.3.7 exposes ``EVM(fork_url=..., fork_block=...)``, but empirically
(verified 2026-08 on Ethereum mainnet via ``ethereum-rpc.publicnode.com``):

* execution-side storage reads during ``message_call`` are **unreliable** on a
  forked EVM (a fresh fork returned nonzero ``balanceOf`` for brand-new
  addresses, and storage markers injected via ``insert_account_storage`` were
  not visible to ``balanceOf``);
* on a plain (non-forked) EVM, ``insert_account_storage`` / ``set_balance`` ARE
  visible to execution (verified with minimal SLOAD/BALANCE probes), and
  ``evm.storage()`` sees uncommitted journal changes.

Therefore this module simulates with the **state-override pattern**: it fetches
the verified token bytecode via ``eth_getCode`` (public RPC), injects it into a
plain pyrevm EVM, discovers the ERC20 balance-mapping storage slot empirically,
seeds seller/recipient balances, and executes the token's own ``transfer()``
against those balances. No archive node is required; every number reported is
produced by executing the real contract code locally.

Limitations (documented, per design)
------------------------------------
* Requires a full-node RPC that answers ``eth_getCode`` / ``eth_call``.
* Balance-slot discovery assumes a standard ``mapping(address => uint256)``
  balance layout with the mapping slot in ``0..MAX_SLOT_SCAN``. Exotic layouts
  (arrays, EnumerableSet-based balances, proxies whose implementation is not
  loaded) are reported as "cannot simulate" rather than guessed at.
* ``transfer()`` probes cover the sell leg; the buy leg is probed as
  pair→seller transfer when a Uniswap V2 pair can be resolved. Full
  pair-storage ``pair.swap`` simulation is intentionally out of scope here.
* The anti-whale probe is a heuristic (normal amount succeeds, 100x amount
  reverts) and can be fooled by other revert triggers; it is reported as a
  flag plus the actual revert reason, never as a hard fact.

Public API
----------
    result = simulate_sell_round_trip("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE")
    result = asimulate_sell_round_trip("0x...", chain="bsc")   # async variant

Dependencies (already in the project venv; NOT yet declared in pyproject.toml):
``pyrevm==0.3.7`` and ``eth-utils`` (used only for ``keccak``). ``httpx`` is a
declared project dependency.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as time_mod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from eth_utils.crypto import keccak
from pyrevm import EVM, AccountInfo, BlockEnv, JournalCheckpoint

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Chain configuration (RPC fallbacks verified 2026-08-14; publicnode first)
# ────────────────────────────────────────────────────────────────────────────

ZERO_ADDRESS = "0x" + "0" * 40
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"
ONE_ADDRESS = "0x" + "0" * 39 + "1"

SELECTOR_BALANCE_OF = "70a08231"
SELECTOR_TRANSFER = "a9059cbb"
SELECTOR_DECIMALS = "313ce567"
SELECTOR_GET_PAIR = "e6a43905"  # UniswapV2Factory.getPair(address,address)
SELECTOR_ERROR_STRING = "08c379a0"  # Error(string) revert prefix
SELECTOR_PANIC = "4e487b71"  # Panic(uint256) revert prefix

MAX_SLOT_SCAN = 64  # balance-mapping slot candidates tried during discovery
ANTI_WHALE_MULTIPLIER = 100
DEFAULT_DECIMALS = 18


@dataclass(frozen=True)
class ChainConfig:
    rpc_urls: tuple[str, ...]
    weth: str
    factory_v2: str


# Public RPC endpoints — verified reachable from this deployment 2026-08-14.
# eth.llamarpc.com was down at the time; it is kept as a fallback so the list
# matches tx_simulator's intent without editing that module.
_CHAINS: dict[str, ChainConfig] = {
    "ethereum": ChainConfig(
        rpc_urls=(
            "https://ethereum-rpc.publicnode.com",
            "https://eth.llamarpc.com",
            "https://cloudflare-eth.com",
        ),
        weth="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        factory_v2="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    ),
    "bsc": ChainConfig(
        rpc_urls=(
            "https://bsc-dataseed.binance.org",
            "https://bsc-rpc.publicnode.com",
        ),
        weth="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        factory_v2="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
    ),
    "polygon": ChainConfig(
        rpc_urls=(
            "https://polygon-rpc.com",
            "https://polygon-bor-rpc.publicnode.com",
        ),
        weth="0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        factory_v2="0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
    ),
    "arbitrum": ChainConfig(
        rpc_urls=(
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum-one-rpc.publicnode.com",
        ),
        weth="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        factory_v2="0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
    ),
    "base": ChainConfig(
        rpc_urls=(
            "https://mainnet.base.org",
            "https://base-rpc.publicnode.com",
        ),
        weth="0x4200000000000000000000000000000000000006",
        factory_v2="0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6",
    ),
    "optimism": ChainConfig(
        rpc_urls=(
            "https://mainnet.optimism.io",
            "https://optimism-rpc.publicnode.com",
        ),
        weth="0x4200000000000000000000000000000000000006",
        factory_v2="0x0c642051767c3Ee34DE4F5B57DD61A5A2c2c9d52",
    ),
}

_DEFAULT_CHAIN = "ethereum"

# ────────────────────────────────────────────────────────────────────────────
# Result model
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class SellSimulationResult:
    """Outcome of the pyrevm sell round-trip simulation."""

    token_address: str
    chain: str
    simulation_success: bool
    method: str = "pyrevm-state-override-transfer"
    can_sell: bool = False
    can_buy: bool = False
    sell_to_pair_reverted: bool = False
    input_amount: int = 0
    output_amount: int | None = None
    tax_pct: float | None = None
    sell_tax_pct: float | None = None
    buy_tax_pct: float | None = None
    all_transfers_revert: bool = False
    blacklist_hit: bool = False
    anti_whale_hit: bool = False
    revert_reason: str | None = None
    balance_slot: int | None = None
    pair_address: str | None = None
    warnings: list[str] = field(default_factory=list)
    risk: str = "unknown"

    @property
    def is_honeypot(self) -> bool:
        """Classic honeypot signature: transfers work but selling to the pair reverts."""
        return self.can_sell and self.sell_to_pair_reverted


# ────────────────────────────────────────────────────────────────────────────
# RPC helpers (sync; httpx.Client per call mirrors tx_simulator's style)
# ────────────────────────────────────────────────────────────────────────────


def _rpc_call(
    rpc_urls: Sequence[str],
    method: str,
    params: list[Any],
    timeout: float,
) -> Any:
    """Call a JSON-RPC method across the fallback URL list. Returns result or None."""
    last_error: Exception | None = None
    for url in rpc_urls:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    last_error = RuntimeError(f"rpc error: {payload['error']}")
                    continue
                return payload.get("result")
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_error = exc
            continue
    if last_error is not None:
        logger.debug("pyrevm_rpc_all_failed: method=%s error=%s", method, str(last_error))
    return None


def _eth_call_view(rpc_urls: Sequence[str], to: str, data: str, timeout: float) -> str | None:
    """eth_call against 'latest'. Returns the raw hex result or None."""
    result = _rpc_call(
        rpc_urls,
        "eth_call",
        [{"to": to, "data": data}, "latest"],
        timeout,
    )
    if isinstance(result, str) and result.startswith("0x"):
        return result
    return None


def _fetch_code(rpc_urls: Sequence[str], address: str, timeout: float) -> bytes:
    """eth_getCode → runtime bytecode bytes (empty bytes for EOAs)."""
    result = _rpc_call(rpc_urls, "eth_getCode", [address, "latest"], timeout)
    if isinstance(result, str) and result.startswith("0x") and len(result) > 2:
        return bytes.fromhex(result[2:])
    return b""


def _resolve_pair(
    rpc_urls: Sequence[str],
    factory: str,
    token_address: str,
    weth: str,
    timeout: float,
) -> str | None:
    """Uniswap V2 factory.getPair(token, weth) → pair address or None."""
    data = "0x" + SELECTOR_GET_PAIR + token_address[2:].lower().rjust(64, "0") + weth[2:].lower().rjust(64, "0")
    result = _eth_call_view(rpc_urls, factory, data, timeout)
    if result is None or result == "0x":
        return None
    address = "0x" + result[-40:].lower()
    if address == ZERO_ADDRESS or address.lower() == token_address.lower():
        return None
    return address


def _fetch_decimals(rpc_urls: Sequence[str], token_address: str, timeout: float) -> int:
    """decimals() via RPC; falls back to the ERC20 default (18)."""
    result = _eth_call_view(rpc_urls, token_address, "0x" + SELECTOR_DECIMALS, timeout)
    if result is not None and len(result) >= 66:
        try:
            return int(result[2:], 16)
        except ValueError:
            return DEFAULT_DECIMALS
    return DEFAULT_DECIMALS


# ────────────────────────────────────────────────────────────────────────────
# ABI helpers (manual 32-byte word packing — no eth_abi dependency)
# ────────────────────────────────────────────────────────────────────────────


def _encode_address(address: str) -> bytes:
    """Left-pad a 0x address to 32 bytes."""
    return bytes.fromhex(address[2:].lower()).rjust(32, b"\x00")


def _balance_of_calldata(who: str) -> bytes:
    return bytes.fromhex(SELECTOR_BALANCE_OF) + _encode_address(who)


def _transfer_calldata(to: str, amount: int) -> bytes:
    return bytes.fromhex(SELECTOR_TRANSFER) + _encode_address(to) + amount.to_bytes(32, "big")


def _balance_slot_key(address: str, slot_idx: int) -> int:
    """Solidity mapping key: keccak256(abi.encode(holder, slot_idx))."""
    padded = bytes.fromhex(address[2:].lower()).rjust(32, b"\x00")
    return int.from_bytes(keccak(padded + slot_idx.to_bytes(32, "big")), "big")


def _decode_revert_reason(error: str) -> str:
    """Parse a pyrevm RuntimeError message into a human-readable reason."""
    match = re.search(r"output: (0x[0-9a-fA-F]*)", error)
    if match is None:
        return error[:200]
    raw = match.group(1)
    if raw in ("0x", ""):
        return "revert (no reason)"
    try:
        payload = bytes.fromhex(raw[2:])
    except ValueError:
        return raw[:66]
    if len(payload) >= 4 and payload[:4].hex() == SELECTOR_ERROR_STRING:
        try:
            offset = int.from_bytes(payload[4:36], "big")
            length = int.from_bytes(payload[36 + offset : 68 + offset], "big")
            text = payload[68 + offset : 68 + offset + length].decode("utf-8", "replace")
            return text or "revert (empty Error(string))"
        except (IndexError, ValueError):
            return "revert (Error(string) malformed)"
    if len(payload) >= 4 and payload[:4].hex() == SELECTOR_PANIC:
        code = int.from_bytes(payload[4:36], "big")
        return f"Panic(0x{code:x})"
    return raw[:66]


def _parse_execution_error(exc: Exception) -> str:
    return _decode_revert_reason(str(exc))


# ────────────────────────────────────────────────────────────────────────────
# pyrevm simulation core
# ────────────────────────────────────────────────────────────────────────────


def _discover_balance_slot(evm: EVM, token_address: str, holder: str) -> int | None:
    """Find the balance mapping slot by marker injection + balanceOf probe."""
    probe = _balance_of_calldata(holder)
    for slot_idx in range(MAX_SLOT_SCAN):
        marker = 10**17 + slot_idx
        key = _balance_slot_key(holder, slot_idx)
        evm.insert_account_storage(token_address, key, marker)
        try:
            result = evm.message_call(caller=ZERO_ADDRESS, to=token_address, calldata=probe)
            if int.from_bytes(result, "big") == marker:
                evm.insert_account_storage(token_address, key, 0)
                return slot_idx
        except RuntimeError:
            # balanceOf itself reverted — token may not be a standard ERC20.
            evm.insert_account_storage(token_address, key, 0)
            return None
        evm.insert_account_storage(token_address, key, 0)
    return None


def _seed_balances(evm: EVM, token_address: str, slot_idx: int, balances: dict[str, int]) -> None:
    """Directly write token storage balances (persists across journal reverts)."""
    for holder, amount in balances.items():
        evm.insert_account_storage(token_address, _balance_slot_key(holder, slot_idx), amount)


def _read_balance(evm: EVM, token_address: str, slot_idx: int, holder: str) -> int:
    """Read a balance from storage (sees uncommitted journal changes)."""
    return evm.storage(token_address, _balance_slot_key(holder, slot_idx))


@dataclass
class _ProbeOutcome:
    success: bool
    received: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _TokenProbe:
    """Probe context (EVM handle + token storage slot), carried as one value.

    Every storage-level probe needs the same three arguments; grouping them
    keeps the probe helpers small and makes the shared context explicit.
    """

    evm: EVM
    token_address: str
    slot_idx: int

    def seed(self, balances: dict[str, int]) -> None:
        """Write token balances straight into storage for the next probe."""
        _seed_balances(self.evm, self.token_address, self.slot_idx, balances)

    def transfer(self, caller: str, recipient: str, amount: int) -> _ProbeOutcome:
        """Execute token.transfer(recipient, amount) as `caller` and read the result.

        The caller's balance must be seeded beforehand. The recipient's received
        amount is read from storage, which sees the uncommitted journal state.
        """
        checkpoint: JournalCheckpoint = self.evm.snapshot()
        try:
            self.evm.message_call(
                caller=caller,
                to=self.token_address,
                calldata=_transfer_calldata(recipient, amount),
            )
            received = _read_balance(self.evm, self.token_address, self.slot_idx, recipient)
            return _ProbeOutcome(success=True, received=received)
        except RuntimeError as exc:
            return _ProbeOutcome(success=False, reason=_parse_execution_error(exc))
        finally:
            self.evm.revert(checkpoint)


def _probe_all_transfers_revert(probe: _TokenProbe, seller: str, amount: int) -> bool:
    """True when even a tiny transfer reverts — paused/blacklist-everything."""
    tiny = max(1, amount // 1000)
    probe.seed({seller: tiny})
    outcome = probe.transfer(seller, ONE_ADDRESS, tiny)
    return not outcome.success


def _probe_blacklist(
    probe: _TokenProbe,
    seller: str,
    amount: int,
    probe_addresses: Sequence[str],
) -> tuple[bool, list[str]]:
    """True if a transfer to a probe address reverts while normal transfers work.

    The zero/burn addresses commonly trigger intentional burn-policy reverts;
    they are reported separately from the 'random recipient' blacklist signal.
    """
    blacklisted: list[str] = []
    for probe_address in probe_addresses:
        probe.seed({seller: amount})
        outcome = probe.transfer(seller, probe_address, amount)
        if outcome.success:
            continue
        if probe_address in (ZERO_ADDRESS, DEAD_ADDRESS):
            logger.debug("pyrevm_burn_destination_restricted: %s", probe_address)
        else:
            blacklisted.append(probe_address)
    return bool(blacklisted), blacklisted


def _probe_anti_whale(
    probe: _TokenProbe,
    seller: str,
    recipient: str,
    amount: int,
) -> tuple[bool, str | None]:
    """True if amount x 100 reverts while the normal amount succeeds."""
    big = amount * ANTI_WHALE_MULTIPLIER
    probe.seed({seller: big})
    outcome = probe.transfer(seller, recipient, big)
    return (not outcome.success), outcome.reason


def _simulate_with_evm(
    chain: str,
    token_address: str,
    chain_cfg: ChainConfig,
    amount: int,
    pair_address: str | None,
    timeout: float,
) -> SellSimulationResult:
    token_code = _fetch_code(chain_cfg.rpc_urls, token_address, timeout)
    if not token_code:
        return SellSimulationResult(
            token_address=token_address,
            chain=chain,
            simulation_success=False,
            warnings=["No contract code at address (EOA or RPC did not return code)."],
        )

    evm = EVM()
    evm.set_block_env(BlockEnv(timestamp=int(time_mod.time())))
    evm.insert_account_info(token_address, AccountInfo(nonce=0, code=token_code))

    seller = "0x" + "aa" * 20
    recipient = "0x" + "bb" * 20

    slot_idx = _discover_balance_slot(evm, token_address, seller)
    if slot_idx is None:
        return SellSimulationResult(
            token_address=token_address,
            chain=chain,
            simulation_success=False,
            warnings=[
                "Could not map balance storage slot (nonstandard layout, proxy, "
                "or balanceOf not readable). Cannot simulate transfers."
            ],
        )

    if pair_address is None:
        pair_address = _resolve_pair(
            chain_cfg.rpc_urls,
            chain_cfg.factory_v2,
            token_address,
            chain_cfg.weth,
            timeout,
        )

    warnings: list[str] = []
    if pair_address is None:
        warnings.append("No Uniswap V2 pair resolved — buy/sell-vs-pair probes skipped.")

    probe = _TokenProbe(evm, token_address, slot_idx)
    probe.seed({seller: amount, recipient: 0})

    # 1) normal sell transfer
    normal = probe.transfer(seller, recipient, amount)
    if not normal.success:
        reason = normal.reason or "unknown"
        tiny_reverts = _probe_all_transfers_revert(probe, seller, amount)
        if tiny_reverts:
            warnings.append(
                "ALL transfer() calls revert (even tiny amounts) — token is likely "
                f"paused, blacklists everything, or requires whitelisting. Reason: {reason}"
            )
        return SellSimulationResult(
            token_address=token_address,
            chain=chain,
            simulation_success=True,
            can_sell=False,
            can_buy=False,
            input_amount=amount,
            output_amount=0,
            tax_pct=100.0,
            all_transfers_revert=tiny_reverts,
            revert_reason=reason,
            balance_slot=slot_idx,
            pair_address=pair_address,
            warnings=warnings,
            risk="critical" if tiny_reverts else "dangerous",
        )

    received = normal.received if normal.received is not None else 0
    tax_pct = (1.0 - received / amount) * 100.0 if amount else 0.0

    # 2) pair-leg probes
    sell_tax_pct: float | None = None
    buy_tax_pct: float | None = None
    can_buy = False
    sell_to_pair_reverted = False
    if pair_address is not None:
        probe.seed({seller: amount})
        sell = probe.transfer(seller, pair_address, amount)
        if sell.success and sell.received is not None:
            sell_tax_pct = (1.0 - sell.received / amount) * 100.0
        else:
            sell_to_pair_reverted = True
            warnings.append(
                "Sell TO the DEX pair reverts while normal transfers work — "
                f"classic 'no-sell' honeypot pattern. Reason: {sell.reason}"
            )

        pre_seller = _read_balance(evm, token_address, slot_idx, seller)
        probe.seed({pair_address: amount})
        buy = probe.transfer(pair_address, seller, amount)
        if buy.success and buy.received is not None:
            delta = buy.received - pre_seller
            if delta > 0:
                buy_tax_pct = (1.0 - delta / amount) * 100.0
                can_buy = True
            else:
                warnings.append("Buy-from-pair transfer returned no balance increase.")
        else:
            warnings.append(
                "Buy FROM the DEX pair reverts while normal transfers work — "
                f"possible buy restriction. Reason: {buy.reason}"
            )

    # 3) anti-whale probe
    anti_whale_hit, anti_whale_reason = _probe_anti_whale(probe, seller, recipient, amount)
    if anti_whale_hit:
        warnings.append(
            f"Transfer of {amount * ANTI_WHALE_MULTIPLIER} units reverts while the "
            f"normal amount succeeds — possible anti-whale / max-tx limit. "
            f"Reason: {anti_whale_reason}"
        )

    # 4) blacklist probe (fresh random recipient)
    blacklist_hit, _blacklisted = _probe_blacklist(
        probe,
        seller,
        amount,
        (ONE_ADDRESS, ZERO_ADDRESS, DEAD_ADDRESS),
    )
    if blacklist_hit:
        warnings.append(
            "Transfer to a fresh random address reverted while normal transfers "
            "work — possible blacklist / destination restriction."
        )

    # 5) risk assessment
    risk = "safe"
    if sell_tax_pct is not None and sell_tax_pct > 50:
        risk = "dangerous"
        warnings.append(f"Sell tax {sell_tax_pct:.1f}%.")
    elif sell_tax_pct is not None and sell_tax_pct > 20:
        risk = "warning"
        warnings.append(f"High sell tax {sell_tax_pct:.1f}%.")
    if tax_pct > 50:
        risk = "dangerous"
    elif tax_pct > 20 and risk == "safe":
        risk = "warning"
    if sell_to_pair_reverted:
        risk = "critical"
    if anti_whale_hit or blacklist_hit:
        risk = "dangerous" if risk == "safe" else risk

    return SellSimulationResult(
        token_address=token_address,
        chain=chain,
        simulation_success=True,
        can_sell=True,
        can_buy=can_buy,
        sell_to_pair_reverted=sell_to_pair_reverted,
        input_amount=amount,
        output_amount=received,
        tax_pct=round(tax_pct, 4),
        sell_tax_pct=round(sell_tax_pct, 4) if sell_tax_pct is not None else None,
        buy_tax_pct=round(buy_tax_pct, 4) if buy_tax_pct is not None else None,
        all_transfers_revert=False,
        blacklist_hit=blacklist_hit,
        anti_whale_hit=anti_whale_hit,
        revert_reason=normal.reason,
        balance_slot=slot_idx,
        pair_address=pair_address,
        warnings=warnings,
        risk=risk,
    )


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def simulate_sell_round_trip(
    token_address: str,
    amount: int | None = None,
    *,
    chain: str = _DEFAULT_CHAIN,
    rpc_url: str | None = None,
    pair_address: str | None = None,
    timeout: float = 20.0,
) -> SellSimulationResult:
    """Simulate a sell round-trip for an EVM ERC20 using pyrevm state overrides.

    Executes the token's real ``transfer()`` bytecode locally against seeded
    balances and reports sell metrics: output amount, transfer/sell/buy tax %,
    blacklist/restriction reverts, anti-whale limits, and an overall risk label.

    Args:
        token_address: ERC20 address to simulate (0x-prefixed, 40 hex chars).
        amount: raw token amount to simulate selling. Defaults to 1 whole token
            (10**decimals).
        chain: chain key from the internal config (ethereum/bsc/polygon/
            arbitrum/base/optimism). Ignored when ``rpc_url`` is provided.
        rpc_url: optional explicit RPC URL (overrides the chain's fallback list).
        pair_address: optional Uniswap V2 pair address for buy/sell-vs-pair
            probes. Auto-resolved via the chain's V2 factory when omitted.
        timeout: per-HTTP-request timeout in seconds.

    Returns:
        SellSimulationResult — never raises for RPC/EVM errors; errors are
        surfaced via ``simulation_success=False`` and ``warnings``.
    """
    if not isinstance(token_address, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", token_address):
        return SellSimulationResult(
            token_address=token_address if isinstance(token_address, str) else "",
            chain=chain,
            simulation_success=False,
            warnings=["Invalid EVM address (expected 0x + 40 hex chars)."],
        )

    chain_cfg = _CHAINS.get(chain, _CHAINS[_DEFAULT_CHAIN])
    if rpc_url is not None:
        chain_cfg = ChainConfig(
            rpc_urls=(rpc_url,),
            weth=chain_cfg.weth,
            factory_v2=chain_cfg.factory_v2,
        )

    if amount is None:
        decimals = _fetch_decimals(chain_cfg.rpc_urls, token_address, timeout)
        amount = 10**decimals

    if amount <= 0:
        return SellSimulationResult(
            token_address=token_address,
            chain=chain,
            simulation_success=False,
            warnings=["amount must be > 0."],
        )

    try:
        return _simulate_with_evm(chain, token_address, chain_cfg, amount, pair_address, timeout)
    except Exception as exc:
        logger.exception("pyrevm_simulation_failed: %s", token_address)
        return SellSimulationResult(
            token_address=token_address,
            chain=chain,
            simulation_success=False,
            warnings=[f"Simulation failed: {exc}"],
        )


async def asimulate_sell_round_trip(
    token_address: str,
    amount: int | None = None,
    *,
    chain: str = _DEFAULT_CHAIN,
    rpc_url: str | None = None,
    pair_address: str | None = None,
    timeout: float = 20.0,
) -> SellSimulationResult:
    """Async variant of :func:`simulate_sell_round_trip`.

    Runs the synchronous simulation in a worker thread so callers can await it
    without blocking the event loop (the heavy work is RPC-bound; pyrevm calls
    are short-lived local executions).
    """
    return await asyncio.to_thread(
        simulate_sell_round_trip,
        token_address,
        amount,
        chain=chain,
        rpc_url=rpc_url,
        pair_address=pair_address,
        timeout=timeout,
    )


__all__ = [
    "SellSimulationResult",
    "asimulate_sell_round_trip",
    "simulate_sell_round_trip",
]
