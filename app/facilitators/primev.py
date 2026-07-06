"""
Primev FastRPC Facilitator
===========================
Fee-free Ethereum mainnet facilitator with sub-200ms settlement
via mev-commit preconfirmations.
ERC-8004 registered (Agent #23175).
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

logger = logging.getLogger("facilitator.primev")


class PrimevFacilitator(Facilitator):
    """
    Primev FastRPC - fee-free Ethereum settlement with mev-commit preconfirmations.
    Sub-200ms settlement. ERC-8004 Agent #23175.
    """

    def __init__(self, config: FacilitatorConfig | None = None):
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "primev"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.FEE_FREE

    @property
    def priority(self) -> int:
        return 5  # Highest priority for Ethereum - fee-free!

    @property
    def is_fee_free(self) -> bool:
        return True

    @property
    def verify_url(self) -> str | None:
        return self._config.primev_rpc_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["ethereum"],
            chain_ids=[1],
            networks=["eip155:1"],
            tokens=["USDC", "USDT", "ETH", "DAI"],
            native_tokens=["ETH"],
            token_addresses={
                "ethereum:USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "ethereum:USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "ethereum:DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
            },
        )

    @property
    def description(self) -> str:
        return "Primev FastRPC - fee-free Ethereum, sub-200ms mev-commit, ERC-8004 #23175"

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify payment via Primev FastRPC with mev-commit preconfirmations."""
        accepted = payload.get("accepted", {})

        # Build Primev-specific verification body
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
            "agentId": self._config.primev_agent_id,
            "preconfirmation": True,  # Use mev-commit for instant verification
        }
        if requirements:
            body["paymentRequirements"] = requirements

        headers = {
            "Content-Type": "application/json",
        }
        if self._config.primev_api_key:
            headers["X-Primev-API-Key"] = self._config.primev_api_key

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self._config.primev_rpc_url}/x402/verify"
                async with session.post(url, json=body, headers=headers) as resp:
                    data = await resp.json()

                    if resp.status != 200:
                        return self._format_error(f"Primev: {data.get('error', f'HTTP {resp.status}')}")

                    if data.get("isValid") or data.get("verified") or data.get("preconfirmed"):
                        return self._format_success(
                            tx_hash=data.get("txHash") or data.get("commitmentHash"),
                            payer=data.get("payer") or data.get("from"),
                            amount=accepted.get("amount"),
                            chain="ethereum",
                            token=data.get("token", "USDC"),
                            settlement_id=data.get("settlementId"),
                            block_number=data.get("blockNumber"),
                            confirmations=data.get("confirmations", 1),
                            preconfirmation_time_ms=data.get("preconfirmationTimeMs"),
                            fee_free=True,
                        )

                    return self._format_error(f"Primev: {data.get('invalidReason', 'Unknown')}")

        except TimeoutError:
            return self._format_error("Primev verification timed out")
        except aiohttp.ClientError as e:
            return self._format_error(f"Primev connection error: {e}")
        except Exception as e:
            logger.exception(f"Primev verify error: {e}")
            return self._format_error(f"Primev internal error: {e}")

    async def health(self) -> bool:
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {}
                if self._config.primev_api_key:
                    headers["X-Primev-API-Key"] = self._config.primev_api_key
                async with session.get(
                    f"{self._config.primev_rpc_url}/health",
                    headers=headers,
                ) as resp:
                    return resp.status < 500
        except Exception as e:
            logger.warning(f"Primev health check failed: {e}")
            return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Settle via Primev (fee-free, mev-commit preconfirmation)."""
        try:
            body = {
                "paymentData": payment_data,
                "agentId": self._config.primev_agent_id,
                "preconfirmation": True,
            }
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                headers = {"Content-Type": "application/json"}
                if self._config.primev_api_key:
                    headers["X-Primev-API-Key"] = self._config.primev_api_key

                async with session.post(
                    f"{self._config.primev_rpc_url}/x402/settle",
                    json=body,
                    headers=headers,
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        return SettlementResult(
                            settled=True,
                            settlement_id=data.get("settlementId"),
                            tx_hash=data.get("txHash"),
                            chain="ethereum",
                            facilitator=self.name,
                            reason=f"Settled fee-free via Primev mev-commit in {data.get('preconfirmationTimeMs', '?')}ms",
                            extra={
                                "fee_free": True,
                                "preconfirmation_time_ms": data.get("preconfirmationTimeMs"),
                            },
                        )
                    return SettlementResult(
                        settled=False,
                        reason=f"Primev settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                        facilitator=self.name,
                    )
        except Exception as e:
            return SettlementResult(
                settled=False,
                reason=f"Primev settlement error: {e}",
                facilitator=self.name,
            )
