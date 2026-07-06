"""
x402-rs Self-Hosted Facilitator Client
=======================================
Python client for the self-hosted x402-rs Rust facilitator.
Communicates with the x402-rs REST API (/verify, /settle, /health).

The x402-rs facilitator runs as a Docker container alongside the backend,
providing independent payment verification and settlement.
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
from app.domains.billing.facilitators.config import FacilitatorConfig, get_config

logger = logging.getLogger("facilitator.x402_rs")


class X402RsFacilitator(Facilitator):
    """
    Self-hosted x402-rs facilitator - production-grade Rust implementation.
    Runs locally in Docker. Multi-chain support.

    Provides:
    - /verify - Payment verification
    - /settle - On-chain settlement
    - /health - Health check
    """

    def __init__(self, config: FacilitatorConfig | None = None):
        self._config = config or get_config()

    @property
    def name(self) -> str:
        return "x402_rs"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.SELF_HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.INSTANT

    @property
    def priority(self) -> int:
        return 40  # Self-hosted - use after hosted facilitators

    @property
    def verify_url(self) -> str | None:
        return self._config.x402_rs_verify_url

    @property
    def settle_url(self) -> str | None:
        return self._config.x402_rs_settle_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=[
                "base",
                "ethereum",
                "bsc",
                "arbitrum",
                "optimism",
                "polygon",
                "avalanche",
                "fantom",
                "gnosis",
            ],
            chain_ids=[8453, 1, 56, 42161, 10, 137, 43114, 250, 100],
            networks=[
                "eip155:8453",
                "eip155:1",
                "eip155:56",
                "eip155:42161",
                "eip155:10",
                "eip155:137",
                "eip155:43114",
                "eip155:250",
                "eip155:100",
            ],
            tokens=["USDC", "USDT", "DAI", "WETH", "ETH"],
            native_tokens=["ETH", "AVAX", "FTM", "POL", "BNB", "XDAI"],
            token_addresses={
                "base:USDC": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                "ethereum:USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "bsc:USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                "arbitrum:USDC": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                "optimism:USDC": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
                "polygon:USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
            },
        )

    @property
    def description(self) -> str:
        return "x402-rs - self-hosted Rust facilitator, multi-chain"

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify payment via self-hosted x402-rs."""
        accepted = payload.get("accepted", {})

        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
        }
        if requirements:
            body["paymentRequirements"] = requirements

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.x402_rs_verify_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                ) as resp,
            ):
                data = await resp.json()

                if resp.status == 200:
                    if data.get("isValid") or data.get("verified"):
                        return self._format_success(
                            tx_hash=data.get("txHash"),
                            payer=data.get("payer"),
                            amount=accepted.get("amount"),
                            chain=data.get("chain") or accepted.get("network", "").split(":")[-1],
                            token=data.get("token", "USDC"),
                            settlement_id=data.get("settlementId"),
                            block_number=data.get("blockNumber"),
                            confirmations=data.get("confirmations"),
                        )
                    return self._format_error(f"x402-rs: {data.get('invalidReason', 'Unknown')}")
                return self._format_error(f"x402-rs HTTP {resp.status}: {data.get('error', '')}")

        except TimeoutError:
            return self._format_error("x402-rs verification timed out (is container running?)")
        except aiohttp.ClientConnectionError:
            return self._format_error("x402-rs not reachable - check docker container")
        except aiohttp.ClientError as e:
            return self._format_error(f"x402-rs connection error: {e}")
        except Exception as e:
            logger.exception(f"x402-rs verify error: {e}")
            return self._format_error(f"x402-rs internal error: {e}")

    async def health(self) -> bool:
        """Check if x402-rs container is running."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    self._config.x402_rs_settle_url.replace("/settle", "/health"),
                ) as resp,
            ):
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug(f"x402-rs health: {data.get('status', 'ok')}")
                    return True
                return False
        except aiohttp.ClientConnectionError:
            logger.debug("x402-rs container not running")
            return False
        except Exception as e:
            logger.warning(f"x402-rs health check error: {e}")
            return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Settle payment via self-hosted x402-rs."""
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(
                    self._config.x402_rs_settle_url,
                    json={"paymentData": payment_data},
                    headers={"Content-Type": "application/json"},
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
                        reason="Settled via self-hosted x402-rs",
                        extra={"self_hosted": True},
                    )
                return SettlementResult(
                    settled=False,
                    reason=f"x402-rs settle failed: {data.get('error', f'HTTP {resp.status}')}",
                    facilitator=self.name,
                )
        except aiohttp.ClientConnectionError:
            return SettlementResult(
                settled=False,
                reason="x402-rs not reachable for settlement",
                facilitator=self.name,
            )
        except Exception as e:
            return SettlementResult(
                settled=False,
                reason=f"x402-rs settlement error: {e}",
                facilitator=self.name,
            )
