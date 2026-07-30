"""
Web3 Chat Router - Pydantic Models
====================================
Request/response models for Web3 chat endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PayRequest(BaseModel):
    recipient: str = Field(..., description="Recipient username or wallet address")
    amount: str = Field(..., description="Amount to send")
    token: str = Field(default="ETH", description="Token symbol: ETH, SOL, USDC, USDT, DAI, BTC")
    chain: str = Field(
        default="ethereum", description="Blockchain: ethereum, solana, base, polygon, optimism, arbitrum, lightning"
    )
    room_id: str | None = Field(default=None, description="Room context for the payment")
    message: str | None = Field(default=None, max_length=200, description="Optional payment message")
    wallet_address: str = Field(..., description="Sender wallet address")

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        token = v.upper()
        valid_tokens = {"ETH", "SOL", "USDC", "USDT", "DAI", "BTC"}
        if token not in valid_tokens:
            raise ValueError(f"Unsupported token: {token}. Supported: {', '.join(valid_tokens)}")
        return token

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        chain = v.lower()
        valid_chains = {"ethereum", "solana", "base", "polygon", "optimism", "arbitrum", "lightning"}
        if chain not in valid_chains:
            raise ValueError(f"Unsupported chain: {chain}")
        return chain


class TipRequest(BaseModel):
    recipient: str = Field(..., description="Recipient username")
    amount: str = Field(..., description="Tip amount")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    room_id: str | None = None
    message: str | None = Field(default=None, max_length=200)


class SplitRequest(BaseModel):
    recipients: list[str] = Field(..., min_length=1, max_length=50, description="Recipient usernames")
    total_amount: str = Field(..., description="Total amount to split")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    room_id: str | None = None


class RequestPayment(BaseModel):
    recipient: str = Field(..., description="User to request payment from")
    amount: str = Field(..., description="Amount to request")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    reason: str | None = Field(default=None, max_length=300, description="Reason for payment request")
    room_id: str | None = None


class ConnectWalletRequest(BaseModel):
    address: str = Field(..., description="Wallet address")
    chain: str = Field(default="ethereum")
    signature: str | None = Field(default=None, description="Signed message for verification")
    message: str | None = Field(default=None, description="Message that was signed")
    wallet_type: str = Field(default="evm", description="wallet type: metamask, phantom, rainbow, walletconnect, okx")


class TokenGateRequest(BaseModel):
    token_address: str | None = Field(default=None, description="Token contract/mint address")
    token_symbol: str | None = Field(default=None, description="Token symbol (e.g., $TICKER)")
    chain: str = Field(default="ethereum")
    min_amount: str = Field(..., description="Minimum token amount required")
    enabled: bool = True


class NFTGateRequest(BaseModel):
    contract_address: str | None = Field(default=None, description="NFT contract address (any = any NFT)")
    collection_name: str | None = Field(default=None, description="Collection name for Solana/verification")
    creator_address: str | None = Field(default=None, description="NFT creator address")
    chain: str = Field(default="ethereum")


class SwapQuoteRequest(BaseModel):
    from_token: str = Field(..., description="Token to swap from")
    to_token: str = Field(..., description="Token to swap to")
    amount: str = Field(..., description="Amount to swap")
    chain: str = Field(default="ethereum")
    wallet_address: str = Field(..., description="Wallet address for quote")


class VerifyGateRequest(BaseModel):
    address: str = Field(..., description="Wallet address to verify")
    chain: str | None = None
