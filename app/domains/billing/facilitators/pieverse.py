"""
BNB Chain Pieverse Facilitator [OFFLINE - NXDOMAIN]
====================================================
STATUS: OFFLINE - api.pieverse.xyz DNS is NXDOMAIN (dead domain).
Do not route payments to this facilitator. Kept for reference only.
BNB Chain x402 facilitator with instant settlement.
Supports USDC and USDT on BSC mainnet.
"""

from __future__ import annotations

from typing import Any

import aiohttp
import httpx

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

logger = get_logger("facilitator.pieverse")


class PieverseFacilitator(Facilitator):
    """BNB Chain Pieverse facilitator - instant settlement for BSC.

    OFFLINE: api.pieverse.xyz DNS is NXDOMAIN. This facilitator is dead.
    """

    # ── DEAD facilitator flag ──
    status = "offline"  # NXDOMAIN: api.pieverse.xyz does not resolve

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "pieverse"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.INSTANT

    @property
    def priority(self) -> int:
        return 999  # Dead facilitator - lowest possible priority

    @property
    def verify_url(self) -> str | None:
        return self._config.pieverse_verify_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["bsc", "bnbchain"],
            chain_ids=[56],
            networks=["eip155:56"],
            tokens=["USDC", "USDT"],
            token_addresses={
                "bsc:USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                "bsc:USDT": "0x55d398326f99059fF775485246999027B3197955",
            },
        )

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        # OFFLINE: api.pieverse.xyz is NXDOMAIN - refuse all verifications
        return self._format_error("Pieverse facilitator is OFFLINE (api.pieverse.xyz NXDOMAIN)")

    async def _verify_live(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        if not self._config.pieverse_api_key:
            return self._format_error("Pieverse API key not configured")

        accepted = payload.get("accepted", {})
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
            "chain": "bsc",
        }
        if requirements:
            body["paymentRequirements"] = requirements

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self._config.pieverse_api_key,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.pieverse_verify_url,
                    json=body,
                    headers=headers,
                ) as resp,
            ):
                data = await resp.json()

                if resp.status != 200:
                    reason = data.get("error", f"HTTP {resp.status}")
                    return self._format_error(f"Pieverse: {reason}")

                if data.get("isValid") or data.get("verified"):
                    return self._format_success(
                        tx_hash=data.get("txHash"),
                        payer=data.get("payer"),
                        amount=accepted.get("amount"),
                        chain="bsc",
                        token=data.get("token", accepted.get("asset", "USDC")),
                        settlement_id=data.get("settlementId"),
                    )

                reason = data.get("invalidReason") or data.get("reason") or "Unknown"
                return self._format_error(f"Pieverse: {reason}")

        except TimeoutError:
            return self._format_error("Pieverse verification timed out")
        except aiohttp.ClientError as e:
            return self._format_error(f"Pieverse connection error: {e}")
        except (ConnectionError, OSError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.exception(f"Pieverse verify error: {e}")
            return self._format_error(f"Pieverse internal error: {e}")

    async def health(self) -> bool:
        # OFFLINE: api.pieverse.xyz is NXDOMAIN - always return False
        return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        # OFFLINE: api.pieverse.xyz is NXDOMAIN - refuse all settlements
        return SettlementResult(
            settled=False,
            reason="Pieverse facilitator is OFFLINE (api.pieverse.xyz NXDOMAIN)",
            facilitator=self.name,
        )

    async def _settle_live(self, payment_data: dict[str, Any]) -> SettlementResult:
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.pieverse_settle_url,
                    json={"paymentData": payment_data},
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": self._config.pieverse_api_key,
                    },
                ) as resp,
            ):
                data = await resp.json()
                if resp.status == 200:
                    return SettlementResult(
                        settled=True,
                        settlement_id=data.get("settlementId"),
                        tx_hash=data.get("txHash"),
                        chain="bsc",
                        facilitator=self.name,
                        reason="Settled via Pieverse",
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"Pieverse settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                    facilitator=self.name,
                )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            return SettlementResult(settled=False, reason=f"Pieverse settlement error: {e}", facilitator=self.name)
