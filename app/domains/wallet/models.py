"""
Wallet Data Models — enums, dataclasses, and chain registry.

Extracted from manager.py during god-file split (2026-07-23).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("wallet_models")

# ── Enums ─────────────────────────────────────────────────────────


class WalletStatus(StrEnum):
    ACTIVE = "active"
    ROTATED = "rotated"
    FROZEN = "frozen"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    COMPROMISED = "compromised"


class WalletTier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    VAULT = "vault"


class WalletPurpose(StrEnum):
    PAYMENTS = "payments"
    SUBSCRIPTIONS = "subscriptions"
    OPERATIONS = "operations"
    TREASURY = "treasury"
    USER_ESCROW = "user_escrow"
    BOT_TRADING = "bot_trading"
    AIRDROPS = "airdrops"
    DEVELOPER = "developer"
    MARKETING = "marketing"
    RESERVE = "reserve"


class PaymentType(StrEnum):
    X402 = "x402"
    SUBSCRIPTION = "subscription"
    ONE_TIME = "one_time"
    MARKETPLACE = "marketplace"
    REFUND = "refund"
    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    FEE = "fee"
    REWARD = "reward"


# ── Chain Registry ────────────────────────────────────────────────


@dataclass
class ChainMeta:
    """Metadata for a single blockchain."""

    key: str
    name: str
    native_symbol: str
    native_decimals: int = 18
    coin_class: Any = None
    derivation: str = "bip44"
    address_prefix: str = ""
    address_pattern: str = ""
    testnet_coin_class: Any = None
    curve: str = "secp256k1"
    hd_path_template: str = "m/44'/{coin_type}'/{account}'/{change}/{index}"
    sub_chains: list[str] = field(default_factory=list)
    icon: str = "₿"
    rpc_endpoint: str = ""
    explorer_url: str = ""
    min_confirmation_blocks: int = 1
    supports_tokens: bool = True
    supports_nfts: bool = False


def _build_registry() -> dict[str, ChainMeta]:
    """Build the comprehensive chain registry."""
    reg: dict[str, ChainMeta] = {}

    # ── EVM Chains (secp256k1, BIP44) ────────────────────────
    evm_def = {
        "derivation": "bip44",
        "curve": "secp256k1",
        "address_pattern": r"^0x[a-fA-F0-9]{40}$",
        "address_prefix": "0x",
    }
    evm_chains: list[tuple[str, str, str, int]] = [
        ("eth", "Ethereum", "ETH", 18),
        ("base", "Base", "ETH", 18),
        ("polygon", "Polygon", "MATIC", 18),
        ("arbitrum", "Arbitrum", "ETH", 18),
        ("optimism", "Optimism", "ETH", 18),
        ("avalanche", "Avalanche C-Chain", "AVAX", 18),
        ("bsc", "BNB Smart Chain", "BNB", 18),
        ("fantom", "Fantom", "FTM", 18),
        ("gnosis", "Gnosis Chain", "xDAI", 18),
    ]
    for key, name, sym, dec in evm_chains:
        reg[key] = ChainMeta(key=key, name=name, native_symbol=sym, native_decimals=dec, **evm_def)  # type: ignore[arg-type]

    # ── Bitcoin Family (secp256k1) ───────────────────────────
    btc_def: dict[str, Any] = {"curve": "secp256k1", "address_prefix": ""}
    for key, name, sym, der in [
        ("btc", "Bitcoin", "BTC", "bip44"),
        ("btc-segwit", "Bitcoin SegWit", "BTC", "bip49"),
        ("btc-native-segwit", "Bitcoin Native SegWit", "BTC", "bip84"),
        ("ltc", "Litecoin", "LTC", "bip44"),
        ("doge", "Dogecoin", "DOGE", "bip44"),
        ("dash", "Dash", "DASH", "bip44"),
        ("bch", "Bitcoin Cash", "BCH", "bip44"),
        ("zec", "Zcash", "ZEC", "bip44"),
    ]:
        reg[key] = ChainMeta(key=key, name=name, native_symbol=sym, derivation=der, **btc_def)  # type: ignore[arg-type]

    # ── Solana ───────────────────────────────────────────────
    reg["sol"] = ChainMeta(
        key="sol",
        name="Solana",
        native_symbol="SOL",
        native_decimals=9,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,44}$",
        icon="◎",
    )

    # ── Cosmos / IBC ─────────────────────────────────────────
    cosmos_def: dict[str, Any] = {
        "derivation": "bip44_cosmos",
        "curve": "secp256k1",
        "address_pattern": r"^(cosmos|osmo|juno|secret|inj)1[a-z0-9]{38}$",
    }
    cosmos: list[tuple[str, str, str]] = [
        ("atom", "Cosmos Hub", "ATOM"),
        ("osmo", "Osmosis", "OSMO"),
        ("juno", "Juno", "JUNO"),
        ("secret", "Secret Network", "SCRT"),
        ("inj", "Injective", "INJ"),
    ]
    for key, name, sym in cosmos:
        reg[key] = ChainMeta(key=key, name=name, native_symbol=sym, **cosmos_def)  # type: ignore[arg-type]

    # ── Polkadot / Kusama (ed25519) ──────────────────────────
    reg["dot"] = ChainMeta(
        key="dot",
        name="Polkadot",
        native_symbol="DOT",
        native_decimals=10,
        derivation="bip44_ed25519",
        curve="ed25519",
    )
    reg["ksm"] = ChainMeta(
        key="ksm",
        name="Kusama",
        native_symbol="KSM",
        native_decimals=12,
        derivation="bip44_ed25519",
        curve="ed25519",
    )

    # ── Cardano (ed25519) ────────────────────────────────────
    reg["ada"] = ChainMeta(
        key="ada",
        name="Cardano",
        native_symbol="ADA",
        native_decimals=6,
        derivation="bip44_ed25519",
        curve="ed25519",
    )

    # ── Algorand (ed25519) ───────────────────────────────────
    reg["algo"] = ChainMeta(
        key="algo",
        name="Algorand",
        native_symbol="ALGO",
        native_decimals=6,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^[A-Z2-7]{58}$",
    )

    # ── Stellar (ed25519) ────────────────────────────────────
    reg["xlm"] = ChainMeta(
        key="xlm",
        name="Stellar",
        native_symbol="XLM",
        native_decimals=7,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^G[A-Z2-7]{55}$",
    )

    # ── Ripple (secp256k1, custom address) ──────────────────
    reg["xrp"] = ChainMeta(
        key="xrp",
        name="Ripple",
        native_symbol="XRP",
        native_decimals=6,
        derivation="bip44_secp256k1",
        curve="secp256k1",
        address_pattern=r"^r[1-9A-HJ-NP-Za-km-z]{25,34}$",
        address_prefix="r",
    )

    # ── Near (ed25519) ───────────────────────────────────────
    reg["near"] = ChainMeta(
        key="near",
        name="NEAR Protocol",
        native_symbol="NEAR",
        native_decimals=24,
        derivation="bip44_ed25519",
        curve="ed25519",
    )

    # ── Aptos (ed25519) ──────────────────────────────────────
    reg["apt"] = ChainMeta(
        key="apt",
        name="Aptos",
        native_symbol="APT",
        native_decimals=8,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^0x[a-fA-F0-9]{64}$",
        address_prefix="0x",
    )

    # ── Sui (ed25519) ────────────────────────────────────────
    reg["sui"] = ChainMeta(
        key="sui",
        name="Sui",
        native_symbol="SUI",
        native_decimals=9,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^0x[a-fA-F0-9]{64}$",
        address_prefix="0x",
    )

    # ── TRON (secp256k1, custom address) ────────────────────
    reg["trx"] = ChainMeta(
        key="trx",
        name="TRON",
        native_symbol="TRX",
        native_decimals=6,
        derivation="bip44_secp256k1",
        curve="secp256k1",
        address_pattern=r"^T[A-Za-z1-9]{33}$",
        address_prefix="T",
    )

    # ── Tezos (ed25519) ──────────────────────────────────────
    reg["xtz"] = ChainMeta(
        key="xtz",
        name="Tezos",
        native_symbol="XTZ",
        native_decimals=6,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_pattern=r"^tz[1-3][A-Za-z0-9]{34}$",
    )

    # ── Monero (ed25519-blake2b) ─────────────────────────────
    reg["xmr"] = ChainMeta(
        key="xmr",
        name="Monero",
        native_symbol="XMR",
        native_decimals=12,
        derivation="monero",
        curve="ed25519_blake2b",
        address_pattern=r"^[48][0-9A-Za-z]{94}$",
        icon="ɱ",
    )

    # ── Nano (ed25519-blake2b) ───────────────────────────────
    reg["nano"] = ChainMeta(
        key="nano",
        name="Nano",
        native_symbol="XNO",
        native_decimals=30,
        derivation="bip44_ed25519_blake2b",
        curve="ed25519_blake2b",
        address_pattern=r"^nano_[13][a-z0-9]{60}$",
    )

    # ── TON (ed25519) ────────────────────────────────────────
    reg["ton"] = ChainMeta(
        key="ton",
        name="TON",
        native_symbol="TON",
        native_decimals=9,
        derivation="bip44_ed25519",
        curve="ed25519",
        address_prefix="EQ",
    )

    # ── BAND (ed25519) ───────────────────────────────────────
    reg["band"] = ChainMeta(
        key="band",
        name="Band Protocol",
        native_symbol="BAND",
        native_decimals=9,
        derivation="bip44_ed25519",
        curve="ed25519",
    )

    # Group EVM sub-chains
    evm_keys = [k for k, v in reg.items() if v.curve == "secp256k1" and v.derivation == "bip44"]
    for k in evm_keys:
        reg[k].sub_chains = [sk for sk in evm_keys if sk != k]

    return reg


CHAIN_REGISTRY: dict[str, ChainMeta] = _build_registry()

# ── Data Models ─────────────────────────────────────────────────


@dataclass
class WalletRecord:
    """Complete wallet record with metadata."""

    wallet_id: str
    chain: str
    address: str
    public_key: str = ""

    # Identity
    name: str = ""
    description: str = ""
    purpose: str = WalletPurpose.OPERATIONS.value
    tier: str = WalletTier.WARM.value
    status: str = WalletStatus.ACTIVE.value

    # Organization
    tags: list[str] = field(default_factory=list)
    group: str = "default"
    labels: dict[str, str] = field(default_factory=dict)

    # Security
    created_at: str = ""
    rotated_at: str | None = None
    expires_at: str | None = None
    last_used: str | None = None
    use_count: int = 0

    # Balance tracking
    balance_raw: str = "0"
    balance_decimal: float = 0.0
    balance_usd: float = 0.0
    token_balances: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Transaction tracking
    total_received: float = 0.0
    total_sent: float = 0.0
    tx_count: int = 0
    last_tx_hash: str = ""
    last_tx_time: str | None = None

    # Payment integration
    x402_enabled: bool = False
    x402_price_usd: float = 0.0
    subscription_enabled: bool = False
    subscription_tiers: list[str] = field(default_factory=list)

    # Verification
    verified_at: str | None = None
    verified_by: str = ""
    chain_registered: bool = False

    # Alerts
    alert_threshold_usd: float = 100.0
    low_balance_threshold: float = 0.0

    # Audit
    created_by: str = ""
    notes: str = ""
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_safe_dict(self) -> dict[str, Any]:
        """Return without sensitive data."""
        d = self.to_dict()
        d.pop("public_key", None)
        return d


@dataclass
class PaymentRecord:
    """Payment transaction record."""

    payment_id: str
    wallet_id: str
    wallet_address: str
    chain: str
    payment_type: str

    amount: float = 0.0
    amount_usd: float = 0.0
    token: str = ""

    from_address: str = ""
    to_address: str = ""
    tx_hash: str = ""
    block_number: int = 0

    status: str = "pending"
    confirmations: int = 0

    user_id: str = ""
    user_email: str = ""
    tool_id: str = ""
    tool_name: str = ""

    x402_resource: str = ""
    x402_facet: str = ""

    created_at: str = ""
    confirmed_at: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WalletRotationSchedule:
    """Schedule for automatic wallet rotation."""

    wallet_id: str
    chain: str
    rotate_every_days: int = 90
    auto_rotate: bool = False
    notify_before_days: int = 7
    last_rotated: str | None = None
    next_rotation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "CHAIN_REGISTRY",
    "ChainMeta",
    "PaymentRecord",
    "PaymentType",
    "WalletPurpose",
    "WalletRecord",
    "WalletRotationSchedule",
    "WalletStatus",
    "WalletTier",
    "_build_registry",
]
