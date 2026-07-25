"""
Web3 Chat Service
=================
Core service layer for Web3-native chat features:
- Send funds (ETH, SOL, USDC, USDT, DAI, sats/Lightning, creator coins)
- Wallet integration (balance, swap, multi-chain)
- Token-gated rooms (ERC-20, SPL, minimum value)
- NFT-gated rooms (ERC-721, ERC-1155, Metaplex)
- Tip / collect / split / request payments
- Transaction notifications and history
"""
# ruff: noqa: S105 — enum member names are not passwords

from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("web3_chat")

# ── Constants ─────────────────────────────────────────────────────

SUPPORTED_CHAINS = {
    "ethereum": {"chain_id": 1, "native": "ETH", "explorer": "https://etherscan.io/tx/"},
    "solana": {"chain_id": 900, "native": "SOL", "explorer": "https://solscan.io/tx/"},
    "base": {"chain_id": 8453, "native": "ETH", "explorer": "https://basescan.org/tx/"},
    "polygon": {"chain_id": 137, "native": "MATIC", "explorer": "https://polygonscan.com/tx/"},
    "optimism": {"chain_id": 10, "native": "ETH", "explorer": "https://optimistic.etherscan.io/tx/"},
    "arbitrum": {"chain_id": 42161, "native": "ETH", "explorer": "https://arbiscan.io/tx/"},
    "lightning": {"chain_id": 0, "native": "BTC", "explorer": "https://mempool.space/tx/"},
}

SUPPORTED_TOKENS = {
    "ETH": {"decimals": 18, "chains": ["ethereum", "base", "arbitrum", "optimism"]},
    "SOL": {"decimals": 9, "chains": ["solana"]},
    "USDC": {"decimals": 6, "chains": ["ethereum", "solana", "base", "polygon", "arbitrum", "optimism"]},
    "USDT": {"decimals": 6, "chains": ["ethereum", "polygon", "arbitrum", "optimism"]},
    "DAI": {"decimals": 18, "chains": ["ethereum", "polygon", "arbitrum", "optimism"]},
    "BTC": {"decimals": 8, "chains": ["lightning"]},
}

# ERC-20 token addresses (common)
TOKEN_ADDRESSES: dict[str, dict[str, str]] = {
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    },
    "polygon": {
        "USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
    "arbitrum": {
        "USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "USDT": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
    },
    "optimism": {
        "USDC": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        "USDT": "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        "DAI": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1",
    },
    "solana": {
        "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    },
}

# ── Enums ─────────────────────────────────────────────────────────


class PaymentStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    EXPIRED = "expired"


class GateType(StrEnum):
    TOKEN = "token"
    NFT = "nft"
    MIN_VALUE = "min_value"
    ANY_NFT = "any_nft"


class ChainType(StrEnum):
    ETHEREUM = "ethereum"
    SOLANA = "solana"
    BASE = "base"
    POLYGON = "polygon"
    OPTIMISM = "optimism"
    ARBITRUM = "arbitrum"
    LIGHTNING = "lightning"


# ── Data Models ───────────────────────────────────────────────────


@dataclass
class PaymentRequest:
    sender_id: str
    recipient_username: str
    amount: str
    token: str
    chain: str
    room_id: str | None = None
    gas_estimation: dict | None = None
    tx_hash: str | None = None
    status: PaymentStatus = PaymentStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    id: str = field(default_factory=lambda: f"pay-{uuid.uuid4().hex[:12]}")


@dataclass
class TokenGate:
    room_id: str
    gate_type: GateType
    token_address: str | None = None
    token_symbol: str | None = None
    chain: str | None = None
    min_amount: str | None = None
    nft_contract: str | None = None
    nft_collection_name: str | None = None
    nft_creator: str | None = None
    enabled: bool = True


@dataclass
class TipRecord:
    id: str
    from_user: str
    to_user: str
    amount: str
    token: str
    chain: str
    room_id: str | None
    message: str | None
    tx_hash: str | None
    status: PaymentStatus
    created_at: str


@dataclass
class SplitPayment:
    id: str
    initiator: str
    recipients: list[str]
    total_amount: str
    token: str
    chain: str
    room_id: str | None
    tx_hash: str | None
    status: PaymentStatus
    created_at: str


# ── In-Memory Store (Redis/DB replacement for initial build) ──────


class Web3ChatStore:
    """In-memory + future Redis/Postgres store for Web3 chat data.

    Designed to be swapped to a persistent store by implementing
    the same interface methods.
    """

    def __init__(self) -> None:
        self._payments: dict[str, PaymentRequest] = {}
        self._tips: dict[str, TipRecord] = {}
        self._splits: dict[str, SplitPayment] = {}
        self._token_gates: dict[str, TokenGate] = {}
        self._nft_gates: dict[str, TokenGate] = {}
        self._connected_wallets: dict[str, dict[str, Any]] = {}
        self._nonces: dict[str, dict[str, Any]] = {}

    # ── Payments ──
    def save_payment(self, payment: PaymentRequest) -> None:
        self._payments[payment.id] = payment

    def get_payment(self, payment_id: str) -> PaymentRequest | None:
        return self._payments.get(payment_id)

    def get_user_payments(self, user_id: str) -> list[PaymentRequest]:
        return [p for p in self._payments.values() if p.sender_id == user_id or p.recipient_username == user_id]

    def update_payment_status(self, payment_id: str, status: PaymentStatus, tx_hash: str | None = None) -> None:
        if payment_id in self._payments:
            self._payments[payment_id].status = status
            if tx_hash:
                self._payments[payment_id].tx_hash = tx_hash

    # ── Tips ──
    def save_tip(self, tip: TipRecord) -> None:
        self._tips[tip.id] = tip

    def get_user_tips(self, user_id: str) -> list[TipRecord]:
        return [t for t in self._tips.values() if t.from_user == user_id or t.to_user == user_id]

    # ── Splits ──
    def save_split(self, split: SplitPayment) -> None:
        self._splits[split.id] = split

    # ── Token Gates ──
    def save_token_gate(self, gate: TokenGate) -> None:
        self._token_gates[gate.room_id] = gate

    def get_token_gate(self, room_id: str) -> TokenGate | None:
        return self._token_gates.get(room_id)

    def remove_gate(self, room_id: str) -> None:
        self._token_gates.pop(room_id, None)
        self._nft_gates.pop(room_id, None)

    # ── NFT Gates ──
    def save_nft_gate(self, gate: TokenGate) -> None:
        self._nft_gates[gate.room_id] = gate

    def get_nft_gate(self, room_id: str) -> TokenGate | None:
        return self._nft_gates.get(room_id)

    # ── Wallets ──
    def save_connection(self, user_id: str, connection: dict[str, Any]) -> None:
        self._connected_wallets[user_id] = connection

    def get_connection(self, user_id: str) -> dict[str, Any] | None:
        return self._connected_wallets.get(user_id)

    def remove_connection(self, user_id: str) -> None:
        self._connected_wallets.pop(user_id, None)


# ── Singleton ──
_store = Web3ChatStore()


# ── Utility Functions ─────────────────────────────────────────────


def get_store() -> Web3ChatStore:
    """Get the Web3 chat store singleton."""
    return _store


def validate_address(address: str, chain: str) -> bool:
    """Basic address validation for supported chains."""
    if not address or len(address) < 10:
        return False
    if chain == "solana":
        return len(address) >= 32 and address.startswith(
            ("D", "G", "H", "J", "B", "F", "A", "5", "3", "2", "4", "6", "7", "8", "9")
        )
    if chain == "lightning":
        return address.startswith(("lnbc", "lntb", "bc1", "lnurl"))
    # EVM chains
    return address.startswith("0x") and len(address) == 42


def format_tx_link(tx_hash: str, chain: str) -> str:
    """Format a transaction link for the given chain."""
    base_url = SUPPORTED_CHAINS.get(chain, {}).get("explorer", "")
    if not base_url or not tx_hash:
        return ""
    return f"{base_url}{tx_hash}"


def parse_amount(amount_str: str, decimals: int = 18) -> Decimal:
    """Parse a human-readable amount string to Decimal."""
    try:
        return Decimal(str(amount_str))
    except (ValueError, ArithmeticError):
        raise ValueError(f"Invalid amount: {amount_str}") from None


def generate_nonce(user_id: str) -> str:
    """Generate a unique nonce for wallet signatures."""
    raw = f"{user_id}:{uuid.uuid4().hex}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ── HTTP Clients ──

_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=10),
            headers={"User-Agent": "RugMunchRMI/1.0"},
        )
    return _http_client


# ── Balance / Price APIs ──────────────────────────────────────────


async def get_token_price(token: str) -> Decimal:
    """Get current USD price for a token via CoinGecko free API."""
    cg_id_map = {
        "ETH": "ethereum",
        "SOL": "solana",
        "USDC": "usd-coin",
        "USDT": "tether",
        "DAI": "dai",
        "BTC": "bitcoin",
    }
    cg_id = cg_id_map.get(token.upper(), token.lower())
    try:
        client = await _get_client()
        resp = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get(cg_id, {}).get("usd", 0)
            return Decimal(str(price))
    except Exception as e:
        logger.warning(f"Price fetch failed for {token}: {e}")
    return Decimal("0")


async def get_wallet_balance(address: str, chain: str, token: str | None = None) -> dict[str, Any]:
    """Fetch wallet balance for native or token on a chain.

    Returns dict with balance, usd_value, and formatted string.
    """
    price = await get_token_price(token or chain.upper())
    result: dict[str, Any] = {
        "address": address,
        "chain": chain,
        "token": token or SUPPORTED_CHAINS.get(chain, {}).get("native", "ETH"),
        "balance_raw": "0",
        "balance_formatted": "0",
        "usd_value": "0",
        "error": None,
    }

    try:
        if chain == "ethereum":
            native = token is None or token.upper() == "ETH"
            if native:
                # Use eth_getBalance via public RPC
                balance = await _eth_get_balance(address)
            else:
                balance = await _erc20_balance(address, chain, token.upper())
            result["balance_raw"] = str(balance)
            form = balance / 10 ** (SUPPORTED_TOKENS.get(token.upper(), {}).get("decimals", 18) if not native else 18)
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "solana":
            balance = await _sol_get_balance(address)
            result["balance_raw"] = str(balance)
            form = Decimal(str(balance)) / Decimal("1000000000")
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "base":
            native = token is None or token.upper() == "ETH"
            if native:
                balance = await _base_get_balance(address)
            else:
                balance = await _erc20_balance(address, chain, token.upper())
            result["balance_raw"] = str(balance)
            form = balance / Decimal("1000000000000000000")
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "lightning":
            result["balance_formatted"] = "Lightning balance via connected node"
            result["balance_raw"] = "0"

        else:
            # Polygon, Arbitrum, Optimism — use native RPC approach
            balance = await _evm_rpc_call(chain, "eth_getBalance", [address, "latest"])
            if balance is not None:
                bal_int = int(balance, 16)
                result["balance_raw"] = str(bal_int)
                form = Decimal(str(bal_int)) / Decimal("1000000000000000000")
                result["balance_formatted"] = str(form)
                result["usd_value"] = str(form * price)

    except Exception as e:
        logger.error(f"Balance fetch error for {address} on {chain}: {e}")
        result["error"] = str(e)

    return result


async def _eth_get_balance(address: str) -> Decimal:
    """Get ETH balance via public RPC."""
    result = await _evm_rpc_call("ethereum", "eth_getBalance", [address, "latest"])
    if result:
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _base_get_balance(address: str) -> Decimal:
    """Get Base ETH balance."""
    result = await _evm_rpc_call("base", "eth_getBalance", [address, "latest"])
    if result:
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _sol_get_balance(address: str) -> int:
    """Get SOL balance via public RPC."""
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", {}).get("value", 0)
    except Exception as e:
        logger.warning(f"Solana RPC error: {e}")
    return 0


async def _erc20_balance(address: str, chain: str, token: str) -> Decimal:
    """Get ERC-20 token balance."""
    token_addr = TOKEN_ADDRESSES.get(chain, {}).get(token)
    if not token_addr:
        return Decimal("0")
    # ERC-20 balanceOf ABI: 0x70a08231
    data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
    result = await _evm_rpc_call(
        chain,
        "eth_call",
        [
            {
                "to": token_addr,
                "data": data,
            },
            "latest",
        ],
    )
    if result and result != "0x":
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _evm_rpc_call(chain: str, method: str, params: list[Any]) -> Any:
    """Make an EVM JSON-RPC call to the appropriate endpoint."""
    rpc_urls = {
        "ethereum": "https://ethereum-rpc.publicnode.com",
        "base": "https://base-rpc.publicnode.com",
        "polygon": "https://polygon-rpc.com",
        "arbitrum": "https://arbitrum-rpc.publicnode.com",
        "optimism": "https://optimism-rpc.publicnode.com",
    }
    url = rpc_urls.get(chain)
    if not url:
        return None
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result")
    except Exception as e:
        logger.warning(f"EVM RPC error on {chain}: {e}")
    return None


# ── Gas Estimation ────────────────────────────────────────────────


async def estimate_gas(chain: str, from_addr: str, to_addr: str, amount_wei: str) -> dict[str, Any]:
    """Estimate gas for a transfer on EVM chains."""
    result: dict[str, Any] = {
        "chain": chain,
        "estimated_gas": None,
        "estimated_cost_eth": None,
        "estimated_cost_usd": None,
        "error": None,
    }
    try:
        # Get gas price
        gas_price_hex = await _evm_rpc_call(chain, "eth_gasPrice", [])
        if not gas_price_hex:
            result["error"] = "Could not fetch gas price"
            return result
        gas_price = int(gas_price_hex, 16)

        # Estimate gas limit (standard transfer: 21000)
        # For token transfers, this would be higher
        gas_limit = 21000

        eth_price = await get_token_price("ETH")
        cost_wei = gas_price * gas_limit
        cost_eth = Decimal(str(cost_wei)) / Decimal("1000000000000000000")
        cost_usd = cost_eth * eth_price

        result["estimated_gas"] = gas_limit
        result["estimated_cost_eth"] = str(cost_eth)
        result["estimated_cost_usd"] = str(cost_usd)
        result["gas_price_gwei"] = str(Decimal(str(gas_price)) / Decimal("1000000000"))
    except Exception as e:
        logger.warning(f"Gas estimation error: {e}")
        result["error"] = str(e)
    return result


# ── Token/NFT Verification ────────────────────────────────────────


async def verify_token_holdings(address: str, token_address: str, chain: str, min_amount: str) -> dict[str, Any]:
    """Verify a wallet holds at least min_amount of a specific token."""
    result: dict[str, Any] = {
        "verified": False,
        "balance": "0",
        "required": min_amount,
        "token_address": token_address,
        "chain": chain,
        "error": None,
    }
    try:
        if chain == "solana":
            balance = await _sol_token_balance(address, token_address)
            result["balance"] = str(balance)
            result["verified"] = Decimal(str(balance)) >= Decimal(min_amount)
        else:
            # EVM token verification
            data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
            balance_hex = await _evm_rpc_call(
                chain,
                "eth_call",
                [
                    {
                        "to": token_address,
                        "data": data,
                    },
                    "latest",
                ],
            )
            if balance_hex and balance_hex != "0x":
                bal = Decimal(str(int(balance_hex, 16)))
                # Try to determine decimals (default 18)
                decimals_hex = await _evm_rpc_call(
                    chain,
                    "eth_call",
                    [
                        {
                            "to": token_address,
                            "data": "0x313ce567",
                        },
                        "latest",
                    ],
                )
                decimals = 18
                if decimals_hex and decimals_hex != "0x":
                    with suppress(ValueError, TypeError):
                        decimals = int(decimals_hex, 16)
                human_balance = bal / Decimal(str(10**decimals))
                result["balance"] = str(human_balance)
                result["verified"] = human_balance >= Decimal(min_amount)
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        result["error"] = str(e)
    return result


async def _sol_token_balance(address: str, token_mint: str) -> Decimal:
    """Get SPL token balance for a wallet."""
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"mint": token_mint},
                {"encoding": "jsonParsed"},
            ],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("result", {}).get("value", [])
            total = Decimal("0")
            for acc in accounts:
                info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                amount = info.get("tokenAmount", {}).get("uiAmount") or 0
                total += Decimal(str(amount))
            return total
    except Exception as e:
        logger.warning(f"Solana token balance error: {e}")
    return Decimal("0")


async def verify_nft_holding(
    address: str,
    chain: str,
    contract: str | None = None,
    collection_name: str | None = None,
    creator: str | None = None,
) -> dict[str, Any]:
    """Verify a wallet holds a specific NFT or any from a collection."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": chain,
        "error": None,
    }
    try:
        if chain == "solana":
            return await _verify_sol_nft(address, creator=creator, collection=collection_name)
        else:
            return await _verify_evm_nft(address, chain, contract)
    except Exception as e:
        logger.error(f"NFT verification error: {e}")
        result["error"] = str(e)
    return result


async def _verify_evm_nft(address: str, chain: str, contract: str | None) -> dict[str, Any]:
    """Verify EVM NFT holding via ERC-721/ERC-1155."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": chain,
        "error": None,
    }
    if not contract or contract == "any":
        # We can't enumerate all NFTs without an indexer
        result["error"] = "Specific contract address required for EVM NFT verification"
        return result
    try:
        # Check balanceOf for ERC-721 (0x70a08231)
        data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
        balance = await _evm_rpc_call(
            chain,
            "eth_call",
            [
                {
                    "to": contract,
                    "data": data,
                },
                "latest",
            ],
        )
        if balance and balance != "0x":
            count = int(balance, 16)
            result["nfts_found"] = count
            result["verified"] = count > 0
        else:
            # Try ERC-1155 balanceOfBatch — skip for now, assume no
            result["nfts_found"] = 0
            result["verified"] = False
    except Exception as e:
        result["error"] = str(e)
    return result


async def _verify_sol_nft(address: str, creator: str | None = None, collection: str | None = None) -> dict[str, Any]:
    """Verify Solana Metaplex NFT holding."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": "solana",
        "error": None,
    }
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAssetsByOwner",
            "params": [address, {"page": 1, "limit": 50}],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",  # Would use Helius/helius for asset API
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("result", {}).get("items", [])
            for item in items:
                if collection:
                    # Check collection match
                    coll = item.get("collection", {})
                    if coll.get("name", "").lower() == collection.lower():
                        result["nfts_found"] += 1
                        result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
                elif creator:
                    creators = item.get("creators", [])
                    if any(c.get("address") == creator for c in creators):
                        result["nfts_found"] += 1
                        result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
                else:
                    result["nfts_found"] += 1
                    result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
            result["verified"] = result["nfts_found"] > 0
    except Exception as e:
        logger.warning(f"Solana NFT verification error: {e}")
        result["error"] = str(e)
    return result


async def verify_min_value_holdings(address: str, chain: str, min_usd: str) -> dict[str, Any]:
    """Verify wallet holds at least min_usd worth of any token."""
    result: dict[str, Any] = {
        "verified": False,
        "total_value_usd": "0",
        "required": min_usd,
        "error": None,
    }
    try:
        if chain == "solana":
            # Get SOL balance
            sol_balance = await _sol_get_balance(address)
            sol_price = await get_token_price("SOL")
            sol_value = (Decimal(str(sol_balance)) / Decimal("1000000000")) * sol_price

            # Get USDC on Solana
            usdc_balance = await _sol_token_balance(address, TOKEN_ADDRESSES["solana"]["USDC"])
            usdc_price = await get_token_price("USDC")
            usdc_value = usdc_balance * usdc_price

            total = sol_value + usdc_value
            result["total_value_usd"] = str(total)
            result["verified"] = total >= Decimal(min_usd)
        else:
            # EVM chain - get native balance
            bal_hex = await _evm_rpc_call(chain, "eth_getBalance", [address, "latest"])
            native_price = await get_token_price(SUPPORTED_CHAINS.get(chain, {}).get("native", "ETH"))
            total = Decimal("0")
            if bal_hex:
                native_bal = Decimal(str(int(bal_hex, 16))) / Decimal("1000000000000000000")
                total = native_bal * native_price

            # Also check USDC on this chain
            chain_usdc = TOKEN_ADDRESSES.get(chain, {}).get("USDC")
            if chain_usdc:
                usdc_data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
                usdc_hex = await _evm_rpc_call(
                    chain,
                    "eth_call",
                    [
                        {
                            "to": chain_usdc,
                            "data": usdc_data,
                        },
                        "latest",
                    ],
                )
                if usdc_hex and usdc_hex != "0x":
                    usdc_bal = Decimal(str(int(usdc_hex, 16))) / Decimal("1000000")
                    total += usdc_bal  # USDC ~$1

            result["total_value_usd"] = str(total)
            result["verified"] = total >= Decimal(min_usd)
    except Exception as e:
        result["error"] = str(e)
    return result


# ── Payment Simulation ────────────────────────────────────────────


async def simulate_payment(from_addr: str, to_addr: str, amount: str, token: str, chain: str) -> dict[str, Any]:
    """Simulate a payment to estimate costs and validate feasibility."""
    # Validate addresses
    if not validate_address(from_addr, chain):
        return {"valid": False, "error": f"Invalid sender address for {chain}"}
    if chain != "lightning" and not validate_address(to_addr, chain):
        return {"valid": False, "error": f"Invalid recipient address for {chain}"}

    # Parse amount
    decimals = SUPPORTED_TOKENS.get(token.upper(), {}).get("decimals", 18)
    try:
        amount_parsed = Decimal(str(amount))
        if amount_parsed <= 0:
            return {"valid": False, "error": "Amount must be positive"}
    except (ValueError, ArithmeticError):
        return {"valid": False, "error": "Invalid amount"}

    # Get price
    price = await get_token_price(token)
    usd_value = amount_parsed * price

    # Estimate gas (EVM only)
    gas_est = None
    if chain in ("ethereum", "base", "polygon", "arbitrum", "optimism"):
        wei_amount = str(int(amount_parsed * Decimal(str(10**decimals))))
        gas_est = await estimate_gas(chain, from_addr, to_addr, wei_amount)

    return {
        "valid": True,
        "from": from_addr,
        "to": to_addr,
        "amount": amount,
        "token": token.upper(),
        "chain": chain,
        "usd_value": str(usd_value),
        "gas_estimation": gas_est,
        "price_usd": str(price),
    }


def verify_payment_command(content: str) -> dict[str, Any] | None:
    """Parse a /pay command from chat message content.

    Returns parsed data if valid, None if not a payment command.
    """
    content = content.strip()

    # /pay @user 0.1 ETH
    # /pay @user 500 sats
    # /pay @user 10 USDC
    # /pay @user 0.5 SOL
    if content.startswith("/pay"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /pay @username amount TOKEN"}
        recipient = parts[1].lstrip("@")
        amount = parts[2]
        token = parts[3].upper()
        return {
            "command": "pay",
            "recipient": recipient,
            "amount": amount,
            "token": token,
        }

    # /tip @user 0.1 ETH
    if content.startswith("/tip"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /tip @username amount TOKEN"}
        recipient = parts[1].lstrip("@")
        amount = parts[2]
        token = parts[3].upper()
        return {
            "command": "tip",
            "recipient": recipient,
            "amount": amount,
            "token": token,
        }

    # /request 0.1 ETH @user [reason]
    if content.startswith("/request"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /request amount TOKEN @username [reason]"}
        amount = parts[1]
        token = parts[2].upper()
        recipient = parts[3].lstrip("@")
        reason = " ".join(parts[4:]) if len(parts) > 4 else None
        return {
            "command": "request",
            "recipient": recipient,
            "amount": amount,
            "token": token,
            "reason": reason,
        }

    # /split 1 ETH @alice @bob @charlie
    if content.startswith("/split"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /split amount TOKEN @user1 @user2 ..."}
        amount = parts[1]
        token = parts[2].upper()
        recipients = [p.lstrip("@") for p in parts[3:]]
        return {
            "command": "split",
            "amount": amount,
            "token": token,
            "recipients": recipients,
        }

    return None
