"""
AsterPay Facilitator - European x402 with EUR Off-Ramp
========================================================
European x402 Facilitator with EUR off-ramp via SEPA Instant.
MiCA compliant, ERC-8004 ready, ElizaOS plugin.

First European-focused x402 infrastructure - allows users to
pay in EUR or crypto and receive EUR via SEPA.
"""

from typing import Any

import aiohttp

from app.core.logging import get_logger
from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorType,
    NetworkSupport,
    SettlementResult,
    SettlementType,
    VerificationResult,
)
from app.domains.billing.facilitators.config import FacilitatorConfig, get_config

logger = get_logger("facilitator.asterpay")


class AsterPayFacilitator(Facilitator):
    """
    AsterPay - European x402 facilitator with SEPA EUR off-ramp.
    MiCA compliant. Supports EUR and crypto payments.
    """

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "asterpay"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.OFF_RAMP  # Fiat conversion

    @property
    def priority(self) -> int:
        return 25

    @property
    def verify_url(self) -> str | None:
        return self._config.asterpay_verify_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["ethereum", "base", "arbitrum", "sepa"],
            chain_ids=[1, 8453, 42161],
            networks=["eip155:1", "eip155:8453", "eip155:42161", "sepa:eur"],
            tokens=["USDC", "EUR", "USDT"],
            token_addresses={
                "ethereum:USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            },
        )

    @property
    def description(self) -> str:
        return "AsterPay - European x402 with SEPA EUR off-ramp, MiCA compliant"

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        if not self._config.asterpay_api_key:
            return self._format_error("AsterPay API key not configured")

        accepted = payload.get("accepted", {})
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
        }
        if requirements:
            body["paymentRequirements"] = requirements

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.asterpay_api_key}",
            "X-AsterPay-Version": "2026-05",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.asterpay_verify_url,
                    json=body,
                    headers=headers,
                ) as resp,
            ):
                data = await resp.json()

                if resp.status != 200:
                    return self._format_error(
                        f"AsterPay: {data.get('error', f'HTTP {resp.status}')}"
                    )

                if data.get("isValid") or data.get("verified"):
                    return self._format_success(
                        tx_hash=data.get("txHash"),
                        payer=data.get("payer"),
                        amount=accepted.get("amount"),
                        amount_usd=data.get("eurValue"),
                        chain="sepa"
                        if data.get("isEur")
                        else accepted.get("network", "").split(":")[-1],
                        token=data.get("currency", "USDC"),
                        settlement_id=data.get("settlementId"),
                    )

                return self._format_error(f"AsterPay: {data.get('invalidReason', 'Unknown')}")

        except TimeoutError:
            return self._format_error("AsterPay verification timed out")
        except aiohttp.ClientError as e:
            return self._format_error(f"AsterPay connection error: {e}")
        except Exception as e:
            logger.exception(f"AsterPay verify error: {e}")
            return self._format_error(f"AsterPay internal error: {e}")

    async def health(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {"Authorization": f"Bearer {self._config.asterpay_api_key}"}
                async with session.get(
                    self._config.asterpay_verify_url.replace("/verify", "/health"),
                    headers=headers,
                ) as resp:
                    return resp.status < 500
        except Exception as e:
            logger.warning(f"AsterPay health check failed: {e}")
            return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Settle via AsterPay - may trigger SEPA off-ramp for EUR."""
        try:
            body = {"paymentData": payment_data}
            # If EUR settlement requested
            if payment_data.get("currency") == "EUR" and self._config.asterpay_sepa_iban:
                body["offramp"] = {
                    "method": "sepa_instant",
                    "iban": self._config.asterpay_sepa_iban,
                }

            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.asterpay_settle_url,
                    json=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._config.asterpay_api_key}",
                    },
                ) as resp,
            ):
                data = await resp.json()
                if resp.status == 200:
                    return SettlementResult(
                        settled=True,
                        settlement_id=data.get("settlementId"),
                        tx_hash=data.get("txHash"),
                        chain=payment_data.get("chain"),
                        facilitator=self.name,
                        reason=f"Settled via AsterPay{' (SEPA EUR off-ramp)' if body.get('offramp') else ''}",
                        extra=data,
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"AsterPay settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                    facilitator=self.name,
                )
        except Exception as e:
            return SettlementResult(
                settled=False, reason=f"AsterPay settlement error: {e}", facilitator=self.name
            )
