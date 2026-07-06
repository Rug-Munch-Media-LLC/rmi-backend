"""
Satoshi Facilitator - Bitcoin-Focused x402 [OFFLINE - NXDOMAIN]
===============================================================
STATUS: OFFLINE - api.satoshi.dev DNS is NXDOMAIN (dead domain).
Do not route payments to this facilitator. Kept for reference only.
Independent x402 facilitator for Bitcoin-focused pay-per-call services.
Supports BTC payment with settlement on Base, Base Sepolia,
Solana Mainnet, and Solana Devnet.
"""

import logging
from typing import Any

import aiohttp

from app.facilitators.base import (
    Facilitator,
    FacilitatorType,
    NetworkSupport,
    SettlementResult,
    SettlementType,
    VerificationResult,
)
from app.facilitators.config import FacilitatorConfig, get_config

logger = logging.getLogger("facilitator.satoshi")


class SatoshiFacilitator(Facilitator):
    """
    Satoshi Facilitator - Bitcoin x402 pay-per-call.
    Users pay in BTC, settled on Base/Solana.

    OFFLINE: api.satoshi.dev DNS is NXDOMAIN. This facilitator is dead.
    """

    # ── DEAD facilitator flag ──
    status = "offline"  # NXDOMAIN: api.satoshi.dev does not resolve

    def __init__(self, config: FacilitatorConfig | None = None):
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "satoshi"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.DEFERRED  # BTC → Base/Solana cross-chain

    @property
    def priority(self) -> int:
        return 999  # Dead facilitator - lowest possible priority

    @property
    def verify_url(self) -> str | None:
        return self._config.satoshi_verify_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["bitcoin", "base", "base-sepolia", "solana", "solana-devnet"],
            chain_ids=[8453, 84532],
            networks=[
                "bitcoin:mainnet",
                "eip155:8453",
                "eip155:84532",
                "solana:mainnet",
                "solana:devnet",
            ],
            tokens=["BTC", "USDC", "USDT"],
            native_tokens=["BTC"],
            token_addresses={
                "base:USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                "solana:USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            },
        )

    @property
    def description(self) -> str:
        return "Satoshi Facilitator - Bitcoin pay-per-call, cross-chain settlement"

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        # OFFLINE: api.satoshi.dev is NXDOMAIN - refuse all verifications
        return self._format_error("Satoshi facilitator is OFFLINE (api.satoshi.dev NXDOMAIN)")

    async def _verify_live(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        if not self._config.satoshi_api_key:
            return self._format_error("Satoshi API key not configured")

        accepted = payload.get("accepted", {})
        network = accepted.get("network", "bitcoin:mainnet")
        is_btc = "bitcoin" in network.lower()

        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
        }
        if requirements:
            body["paymentRequirements"] = requirements
        # If BTC payment, include BTC-specific verification data
        if is_btc:
            body["paymentType"] = "bitcoin"
            body["settlementChain"] = accepted.get("settlementChain", "base")

        headers = {
            "Content-Type": "application/json",
            "X-Satoshi-API-Key": self._config.satoshi_api_key,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=max(self._config.facilitator_verify_timeout, 30))
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                self._config.satoshi_verify_url,
                json=body,
                headers=headers,
            ) as resp:
                data = await resp.json()

                if resp.status != 200:
                    return self._format_error(f"Satoshi: {data.get('error', f'HTTP {resp.status}')}")

                if data.get("isValid") or data.get("verified"):
                    settlement_chain = data.get("settlementChain", "base" if not is_btc else "base")  # noqa: RUF034
                    return self._format_success(
                        tx_hash=data.get("txHash") or data.get("btcTxid"),
                        payer=data.get("payer") or data.get("btcAddress"),
                        amount=accepted.get("amount"),
                        chain="bitcoin" if is_btc else settlement_chain,
                        token="BTC" if is_btc else data.get("token", "USDC"),
                        settlement_id=data.get("settlementId"),
                        block_number=data.get("blockNumber"),
                        confirmations=data.get("confirmations"),
                        settlement_chain=settlement_chain,
                    )

                return self._format_error(f"Satoshi: {data.get('invalidReason', 'Unknown')}")

        except TimeoutError:
            return self._format_error("Satoshi verification timed out (BTC takes longer)")
        except aiohttp.ClientError as e:
            return self._format_error(f"Satoshi connection error: {e}")
        except Exception as e:
            logger.exception(f"Satoshi verify error: {e}")
            return self._format_error(f"Satoshi internal error: {e}")

    async def health(self) -> bool:
        # OFFLINE: api.satoshi.dev is NXDOMAIN - always return False
        return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        # OFFLINE: api.satoshi.dev is NXDOMAIN - refuse all settlements
        return SettlementResult(
            settled=False,
            reason="Satoshi facilitator is OFFLINE (api.satoshi.dev NXDOMAIN)",
            facilitator=self.name,
        )

    async def _settle_live(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Settle BTC payment on target chain (Base or Solana)."""
        try:
            is_btc = payment_data.get("token", "").upper() == "BTC"
            settle_url = self._config.satoshi_btc_settle_url if is_btc else self._config.satoshi_settle_url

            body = {
                "paymentData": payment_data,
            }
            if is_btc:
                body["settlementChain"] = payment_data.get("settlement_chain", "base")

            timeout = aiohttp.ClientTimeout(total=60)  # BTC cross-chain takes longer
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                settle_url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Satoshi-API-Key": self._config.satoshi_api_key,
                },
            ) as resp:
                data = await resp.json()
                if resp.status == 200:
                    target_chain = payment_data.get("settlement_chain", payment_data.get("chain", "base"))
                    return SettlementResult(
                        settled=True,
                        settlement_id=data.get("settlementId"),
                        tx_hash=data.get("txHash"),
                        chain=target_chain,
                        facilitator=self.name,
                        reason=f"Settled BTC → {target_chain} via Satoshi",
                        extra={
                            "cross_chain": is_btc,
                            "source_chain": "bitcoin" if is_btc else payment_data.get("chain"),
                            "target_chain": target_chain,
                        },
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"Satoshi settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                    facilitator=self.name,
                )
        except Exception as e:
            return SettlementResult(
                settled=False,
                reason=f"Satoshi settlement error: {e}",
                facilitator=self.name,
            )
