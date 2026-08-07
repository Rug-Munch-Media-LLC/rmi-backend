"""
Web3 Chat Router - Package
============================
Web3-native chat features:
- /pay, /tip, /split, /request — send crypto in chat
- Wallet connect/disconnect, balances, swap
- Token-gated rooms — require minimum token holdings
- NFT-gated rooms — require specific NFTs
- Transaction history, gas estimation, payment notifications

All routes: /api/v1/chat/web3/*
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.web3_chat import gates, misc, payments, wallet

router = APIRouter(prefix="/api/v1/chat/web3", tags=["chat-web3"])

# Include sub-routers (each contains its own route handlers)
router.include_router(payments.router)
router.include_router(wallet.router)
router.include_router(gates.router)
router.include_router(misc.router)

# Re-export models for convenience
from app.routers.web3_chat.models import (  # isort:skip  # noqa: E402 — module docstring precedes import block
    ConnectWalletRequest,
    NFTGateRequest,
    PayRequest,
    RequestPayment,
    SplitRequest,
    SwapQuoteRequest,
    TipRequest,
    TokenGateRequest,
    VerifyGateRequest,
)
