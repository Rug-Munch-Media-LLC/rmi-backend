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

from app.services.web3_chat.balances import get_wallet_balance
from app.services.web3_chat.models import (
    SUPPORTED_CHAINS,
    SUPPORTED_TOKENS,
    TOKEN_ADDRESSES,
    ChainType,
    GateType,
    PaymentRequest,
    PaymentStatus,
    SplitPayment,
    TipRecord,
    TokenGate,
)
from app.services.web3_chat.rpc import (
    _base_get_balance,
    _erc20_balance,
    _eth_get_balance,
    _evm_rpc_call,
    _get_client,
    _sol_get_balance,
    _sol_token_balance,
    estimate_gas,
    get_token_price,
)
from app.services.web3_chat.store import Web3ChatStore, get_store
from app.services.web3_chat.utils import (
    format_tx_link,
    generate_nonce,
    parse_amount,
    validate_address,
    verify_payment_command,
)
from app.services.web3_chat.verification import (
    _verify_evm_nft,
    _verify_sol_nft,
    simulate_payment,
    verify_min_value_holdings,
    verify_nft_holding,
    verify_token_holdings,
)

__all__ = [
    "SUPPORTED_CHAINS",
    "SUPPORTED_TOKENS",
    "TOKEN_ADDRESSES",
    "ChainType",
    "GateType",
    "PaymentRequest",
    "PaymentStatus",
    "SplitPayment",
    "TipRecord",
    "TokenGate",
    "Web3ChatStore",
    "_base_get_balance",
    "_erc20_balance",
    "_eth_get_balance",
    "_evm_rpc_call",
    "_get_client",
    "_sol_get_balance",
    "_sol_token_balance",
    "_verify_evm_nft",
    "_verify_sol_nft",
    "estimate_gas",
    "format_tx_link",
    "generate_nonce",
    "get_store",
    "get_token_price",
    "get_wallet_balance",
    "parse_amount",
    "simulate_payment",
    "validate_address",
    "verify_min_value_holdings",
    "verify_nft_holding",
    "verify_payment_command",
    "verify_token_holdings",
]
