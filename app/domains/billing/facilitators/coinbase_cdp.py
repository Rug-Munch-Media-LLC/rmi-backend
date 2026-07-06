"""
Coinbase CDP x402 Facilitator
==============================
Official Coinbase Developer Platform facilitator for x402 payments.
Fee-free USDC settlement on Base, Polygon, Arbitrum, and Solana.
Free tier: 1,000 transactions/month.

Register at: https://portal.cdp.coinbase.com/ → API Keys
    Set CDP_API_KEY_ID and CDP_API_KEY_SECRET in environment.

Docs: https://docs.cdp.coinbase.com/x402/welcome
npm:  @coinbase/x402
"""

import logging
from typing import Any

import aiohttp

from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorType,
    NetworkSupport,
    SettlementResult,
    SettlementType,
    VerificationResult,
)
from app.domains.billing.facilitators.config import get_config

logger = logging.getLogger("facilitator.coinbase_cdp")


class CoinbaseCDPFacilitator(Facilitator):
    """
    Coinbase Developer Platform x402 facilitator.

    Fee-free on Base, Polygon, Arbitrum, and Solana.
    Instant settlement via CDP hosted service.
    Free tier: 1,000 transactions/month.
    """

    def __init__(self) -> None:
        config = get_config()
        # CDP uses CDP_API_KEY_ID + CDP_API_KEY_SECRET (standard CDP creds)
        self._api_key_id = config.coinbase_cdp_api_key or _env("CDP_API_KEY_ID")
        self._api_key_secret = config.coinbase_cdp_api_secret or _env("CDP_API_KEY_SECRET")
        self._network = config.coinbase_cdp_network or "base-mainnet"
        self._verify_url = (
            config.coinbase_cdp_verify_url or "https://api.developer.coinbase.com/platform/v1/x402/verify"
        )
        self._settle_url = _env("COINBASE_CDP_SETTLE_URL") or self._verify_url.replace("/verify", "/settle")

        if not self._api_key_id:
            logger.warning(
                "Coinbase CDP not configured - set CDP_API_KEY_ID + CDP_API_KEY_SECRET. "
                "Register at https://portal.cdp.coinbase.com/"
            )

    # ── Identity ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "coinbase_cdp"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.INSTANT

    @property
    def priority(self) -> int:
        return 10

    @property
    def is_fee_free(self) -> bool:
        return True  # CDP is fee-free on Base

    @property
    def verify_url(self) -> str | None:
        return self._verify_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["base", "polygon", "arbitrum", "solana"],
            chain_ids=[8453, 137, 42161],
            networks=[
                "eip155:8453",  # Base mainnet
                "eip155:137",  # Polygon PoS
                "eip155:42161",  # Arbitrum One
                "solana:mainnet",  # Solana
            ],
            tokens=["USDC"],
            token_addresses={
                "base:USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                "polygon:USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
                "arbitrum:USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                "solana:USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            },
        )

    @property
    def description(self) -> str:
        return "Coinbase CDP - fee-free USDC on Base/Polygon/Arbitrum/Solana, 1K free tx/mo"

    # ── Auth ───────────────────────────────────────────────────

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key_id:
            headers["x-api-key"] = self._api_key_id
        return headers

    # ── Health ─────────────────────────────────────────────────

    async def health(self) -> bool:
        if not self._api_key_id:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._verify_url,
                    headers=self._headers,
                    json={"health_check": True},
                ) as resp,
            ):
                return resp.status < 500
        except Exception as e:
            logger.debug(f"CDP health: {e}")
            return False

    # ── Verification ───────────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        if not self._api_key_id:
            return self._format_error(
                "Coinbase CDP API key not configured. "
                "Register at https://portal.cdp.coinbase.com/ and set CDP_API_KEY_ID + CDP_API_KEY_SECRET"
            )

        accepted = payload.get("accepted", {})
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
        }
        if requirements:
            body["paymentRequirements"] = requirements

        try:
            timeout = aiohttp.ClientTimeout(total=_timeout())
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                self._verify_url,
                json=body,
                headers=self._headers,
            ) as resp:
                data = await resp.json()

                if resp.status == 200:
                    if data.get("isValid") or data.get("verified"):
                        tx = data.get("txHash") or data.get("transactionHash")
                        return self._format_success(
                            tx_hash=tx,
                            payer=data.get("payer") or data.get("from"),
                            amount=accepted.get("amount"),
                            amount_usd=data.get("amountUsd"),
                            chain=data.get("chain") or _chain_from_network(accepted.get("network")),
                            token=data.get("token", "USDC"),
                            settlement_id=data.get("settlementId"),
                            block_number=data.get("blockNumber"),
                            confirmations=data.get("confirmations"),
                        )
                    reason = data.get("invalidReason") or data.get("reason") or "Unknown rejection"
                    msg = data.get("invalidMessage") or data.get("message") or ""
                    return self._format_error(f"{reason}: {msg}" if msg else reason)

                if resp.status in (401, 403):
                    return self._format_error(
                        f"CDP auth failed (HTTP {resp.status}). Verify CDP_API_KEY_ID/SECRET at portal.cdp.coinbase.com"
                    )
                return self._format_error(f"CDP HTTP {resp.status}: {data.get('error', data.get('message', ''))}")

        except TimeoutError:
            return self._format_error("Coinbase CDP verification timed out")
        except aiohttp.ClientError as e:
            return self._format_error(f"Coinbase CDP connection error: {e}")
        except Exception as e:
            logger.exception(f"CDP verify error: {e}")
            return self._format_error(f"Coinbase CDP internal error: {e}")

    # ── Settlement ─────────────────────────────────────────────

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        if not self._api_key_id:
            return SettlementResult(settled=False, reason="CDP not configured", facilitator=self.name)

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._settle_url,
                    json={"paymentData": payment_data},
                    headers=self._headers,
                ) as resp,
            ):
                data = await resp.json()
                if resp.status == 200 and data.get("settled"):
                    return SettlementResult(
                        settled=True,
                        settlement_id=data.get("settlementId"),
                        tx_hash=data.get("txHash"),
                        chain=payment_data.get("chain"),
                        facilitator=self.name,
                        reason="Settled fee-free via Coinbase CDP",
                        extra={"fee_free": True},
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"CDP settle: {data.get('error', f'HTTP {resp.status}')}",
                    facilitator=self.name,
                )
        except Exception as e:
            return SettlementResult(settled=False, reason=f"CDP settlement error: {e}", facilitator=self.name)


# ── Helpers ─────────────────────────────────────────────────


def _env(key: str, default: str = "") -> str:
    import os

    return os.getenv(key, default)


def _timeout() -> int:
    return int(_env("FACILITATOR_VERIFY_TIMEOUT", "15"))


def _chain_from_network(network: str) -> str:
    """Map x402 network string to chain key."""
    mapping = {
        "eip155:8453": "base",
        "eip155:137": "polygon",
        "eip155:42161": "arbitrum",
        "solana:mainnet": "solana",
        "eip155:1": "ethereum",
    }
    for net, chain in mapping.items():
        if net in network:
            return chain
    return "base"
