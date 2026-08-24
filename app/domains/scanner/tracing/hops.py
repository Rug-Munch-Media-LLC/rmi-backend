"""Wallet-hop mapping — extract wallet→wallet interactions from decoded logs.

Known event patterns drive the extraction:
  - ``Transfer(address,address,uint256)``   — ERC20/721 transfers
  - ``TransferSingle(address,address,address,uint256,uint256)`` — ERC1155
  - ``Approval(address,address,uint256)``   — token approval (owner→spender)
  - ``Swap(...)``                           — DEX pool swaps
  - ``Deposit``/``Withdrawal``              — WETH9 wraps/unwraps

For unrecognized events we fall back to a heuristic: the first two
``address``-typed args are treated as from→to. Token is always the emitting
contract (log.address). Values are kept as decimal strings so the frontend
never hits JS float precision issues.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.tracing.models import WalletHop

logger = get_logger(__name__)

# Event names where arg0/arg1 are the from/to addresses.
_KNOWN_PATTERNS = frozenset(
    {
        "Transfer",
        "TransferSingle",
        "TransferBatch",
        "Approval",
        "ApprovalForAll",
        "Swap",
        "Deposit",
        "Withdrawal",
        "Mint",
        "Burn",
        "Send",
        "Receive",
    }
)

# Events where the from/to semantics differ from positional args.
_DEPOSIT_EVENTS = frozenset({"Deposit"})  # dst only — treat contract as source
_WITHDRAWAL_EVENTS = frozenset({"Withdrawal"})  # src only — treat contract as sink


def _is_address(value: Any) -> bool:
    """True if a decoded arg value looks like an EVM address."""
    return isinstance(value, str) and value.startswith("0x") and len(value) == 42


def _decimal(value: Any) -> str:
    """Convert a decoded value to a decimal string (or "0" for None)."""
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # Already decimal string, or hex-ish address we cannot sum
        if value.startswith("0x") and len(value) != 42:
            try:
                return str(int(value, 16))
            except (ValueError, TypeError):
                return "0"
        return value if value.isdigit() else "0"
    return "0"


def _first_numeric_arg(args: dict[str, Any], skip_keys: set[str] | None = None) -> str:
    """Find the first numeric-looking arg value (for value extraction)."""
    skip = skip_keys or set()
    for key, value in args.items():
        if key in skip or _is_address(value) or key in ("operator", "owner", "spender"):
            continue
        if isinstance(value, (int, bool)):
            return _decimal(value)
        if isinstance(value, str) and (value.isdigit() or value.startswith("0x")) and len(value) != 42:
            return _decimal(value)
    return "0"


def _address_args(args: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return [(key, value)] for every address-typed decoded arg, preserving order."""
    return [(k, v) for k, v in args.items() if _is_address(v)]


def build_wallet_hops(
    logs: list[dict[str, Any]],
    decoded: dict[str, dict[str, Any]],
) -> list[WalletHop]:
    """Build wallet→wallet hops from receipt logs + their decoded args.

    Args:
        logs: receipt log dicts (each with ``address``, ``logIndex``).
        decoded: log_index → {"event": name, "args": {...}} mapping produced
            by the decoding layer.

    Returns a list of WalletHop models (deduplicated by from/to/token/event).
    """
    hops: list[WalletHop] = []
    seen: set[tuple[str, str, str, str]] = set()

    for log in logs:
        try:
            log_index = int(log.get("logIndex", "0x0"), 16)
        except (ValueError, TypeError):
            log_index = 0
        contract = str(log.get("address", "")).lower()
        info = decoded.get(str(log_index))
        if not info:
            continue
        event = info.get("event") or ""
        args = info.get("args") or {}

        from_addr = ""
        to_addr = ""
        value = "0"

        if event == "Transfer":
            from_addr = str(args.get("from", ""))
            to_addr = str(args.get("to", ""))
            value = _first_numeric_arg(args, skip_keys={"from", "to"})
        elif event == "TransferSingle":
            # TransferSingle(operator, from, to, id, value)
            from_addr = str(args.get("from", ""))
            to_addr = str(args.get("to", ""))
            value = _first_numeric_arg(args, skip_keys={"operator", "from", "to", "id"})
        elif event in ("Approval", "ApprovalForAll"):
            # owner → spender
            from_addr = str(args.get("owner", ""))
            to_addr = str(args.get("spender", ""))
            value = _first_numeric_arg(args, skip_keys={"owner", "spender"})
        elif event == "Swap":
            # Uniswap V2: Swap(sender, amount0In, amount1In, amount0Out, amount1Out, to)
            from_addr = str(args.get("sender", ""))
            to_addr = str(args.get("to", ""))
            value = _first_numeric_arg(args, skip_keys={"sender", "to"})
        elif event == "Deposit":
            # WETH Deposit(dst, wad): contract → dst
            to_addr = str(args.get("dst", ""))
            from_addr = contract
            value = _first_numeric_arg(args, skip_keys={"dst"})
        elif event == "Withdrawal":
            # WETH Withdrawal(src, wad): src → contract
            from_addr = str(args.get("src", ""))
            to_addr = contract
            value = _first_numeric_arg(args, skip_keys={"src"})
        elif event in _KNOWN_PATTERNS:
            addr_pairs = _address_args(args)
            if len(addr_pairs) >= 2:
                from_addr = str(addr_pairs[0][1])
                to_addr = str(addr_pairs[1][1])
                value = _first_numeric_arg(args, skip_keys={addr_pairs[0][0], addr_pairs[1][0]})
        else:
            # Generic heuristic: first two address args are from→to.
            addr_pairs = _address_args(args)
            if len(addr_pairs) >= 2:
                from_addr = str(addr_pairs[0][1])
                to_addr = str(addr_pairs[1][1])
                value = _first_numeric_arg(args, skip_keys={addr_pairs[0][0], addr_pairs[1][0]})

        if not from_addr or not to_addr:
            continue
        if from_addr == to_addr:
            continue
        from_addr = from_addr.lower()
        to_addr = to_addr.lower()
        dedupe_key = (from_addr, to_addr, contract, event)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        hops.append(
            WalletHop(
                from_=from_addr,
                to=to_addr,
                value=_decimal(value),
                token=contract,
                event=event,
                log_index=log_index,
            )
        )

    return hops
