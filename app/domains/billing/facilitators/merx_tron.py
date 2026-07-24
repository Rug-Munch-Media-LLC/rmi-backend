"""
MERX x402 for TRON Facilitator [OFFLINE - NXDOMAIN]
====================================================
STATUS: OFFLINE - api.merx.finance DNS is NXDOMAIN (dead domain).
Do not route payments to this facilitator. Kept for reference only.
First TRON x402 facilitator. Supports USDT, USDC, USDD on TRON mainnet.
Sub-3-second confirmation for micropayments.
Express middleware compatible.
"""

from __future__ import annotations
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

logger = get_logger("facilitator.merx_tron")

class MerxTronFacilitator(Facilitator):
    """MERX x402 - TRON mainnet facilitator with sub-3s confirmation.

    OFFLINE: api.merx.finance DNS is NXDOMAIN. This facilitator is dead.
    """

    # ── DEAD facilitator flag ──
    status = "offline"  # NXDOMAIN: api.merx.finance does not resolve

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "merx_tron"

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
        return self._config.merx_tron_verify_url

    @property
    def supported_networks(self) -> NetworkSupport:
        # TRON mainnet uses a custom network identifier
        return NetworkSupport(
            chains=["tron", "tron-mainnet"],
            chain_ids=[],  # TRON doesn't use EVM chain IDs
            networks=["tron:mainnet", "trx:mainnet"],
            tokens=["USDT", "USDC", "USDD", "TRX"],
            token_addresses={
                "tron:USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",  # USDT on TRC20
                "tron:USDC": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",  # USDC on TRC20
                "tron:USDD": "TPYmHEhy5n8TCEfZGqW2rPbmgh1fGqNBPa",  # USDD on TRC20
            },
        )

    @property
    def description(self) -> str:
        return "MERX x402 - TRON USDT/USDC/USDD micropayments, sub-3s"

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        # OFFLINE: api.merx.finance is NXDOMAIN - refuse all verifications
        return self._format_error("MERX TRON facilitator is OFFLINE (api.merx.finance NXDOMAIN)")

    async def _verify_live(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        if not self._config.merx_tron_api_key:
            return self._format_error("MERX TRON API key not configured")

        accepted = payload.get("accepted", {})
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
            "chain": "tron",
        }
        if requirements:
            body["paymentRequirements"] = requirements

        headers = {
            "Content-Type": "application/json",
            "X-MERX-API-Key": self._config.merx_tron_api_key,
            "X-MERX-Network": "mainnet",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.merx_tron_verify_url,
                    json=body,
                    headers=headers,
                ) as resp,
            ):
                data = await resp.json()

                if resp.status != 200:
                    return self._format_error(
                        f"MERX TRON: {data.get('error', f'HTTP {resp.status}')}"
                    )

                if data.get("isValid") or data.get("verified"):
                    token = data.get("token") or data.get("asset")
                    if not token:
                        # Detect token from payload
                        asset = accepted.get("asset", "")
                        token = (
                            "USDT"
                            if "TR7NH" in asset
                            else "USDC"
                            if "TEkxi" in asset
                            else "USDD"
                            if "TPYm" in asset
                            else "USDT"
                        )

                    return self._format_success(
                        tx_hash=data.get("txHash") or data.get("txid"),
                        payer=data.get("payer") or data.get("from"),
                        amount=accepted.get("amount"),
                        chain="tron",
                        token=token,
                        settlement_id=data.get("settlementId"),
                        block_number=data.get("blockNumber"),
                        confirmations=data.get("confirmations"),
                        confirmation_time_ms=data.get("confirmationTimeMs"),
                    )

                return self._format_error(f"MERX TRON: {data.get('invalidReason', 'Unknown')}")

        except TimeoutError:
            return self._format_error("MERX TRON verification timed out")
        except aiohttp.ClientError as e:
            return self._format_error(f"MERX TRON connection error: {e}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.exception(f"MERX TRON verify error: {e}")
            return self._format_error(f"MERX TRON internal error: {e}")

    async def health(self) -> bool:
        # OFFLINE: api.merx.finance is NXDOMAIN - always return False
        return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        # OFFLINE: api.merx.finance is NXDOMAIN - refuse all settlements
        return SettlementResult(
            settled=False,
            reason="MERX TRON facilitator is OFFLINE (api.merx.finance NXDOMAIN)",
            facilitator=self.name,
        )

    async def _settle_live(self, payment_data: dict[str, Any]) -> SettlementResult:
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.merx_tron_settle_url,
                    json={"paymentData": payment_data},
                    headers={
                        "Content-Type": "application/json",
                        "X-MERX-API-Key": self._config.merx_tron_api_key,
                    },
                ) as resp,
            ):
                data = await resp.json()
                if resp.status == 200:
                    return SettlementResult(
                        settled=True,
                        settlement_id=data.get("settlementId"),
                        tx_hash=data.get("txHash") or data.get("txid"),
                        chain="tron",
                        facilitator=self.name,
                        reason=f"Settled via MERX TRON in {data.get('confirmationTimeMs', '?')}ms",
                        extra={"confirmation_time_ms": data.get("confirmationTimeMs")},
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"MERX TRON settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                    facilitator=self.name,
                )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            return SettlementResult(
                settled=False, reason=f"MERX TRON settlement error: {e}", facilitator=self.name
            )
