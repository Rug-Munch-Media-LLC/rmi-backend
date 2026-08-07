"""
Cloudflare x402 Facilitator
============================
Official x402.org facilitator for Base Sepolia and Ethereum mainnet.
Deferred settlement - payments batched and settled later.

Used as a fallback when Coinbase CDP and PayAI are both unavailable.
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


import httpx

logger = get_logger("facilitator.cloudflare_x402")


class CloudflareX402Facilitator(Facilitator):
    """
    Cloudflare x402 official facilitator.
    Supports Base Sepolia and Ethereum mainnet. Deferred settlement.
    """

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()

    # ── Identity ───────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "cloudflare_x402"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.DEFERRED

    @property
    def priority(self) -> int:
        return 30  # Fallback after CDP (10) and PayAI (20)

    @property
    def verify_url(self) -> str | None:
        return self._config.cloudflare_x402_url

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["base-sepolia", "ethereum"],
            chain_ids=[84532, 1],
            networks=["eip155:84532", "eip155:1"],
            tokens=["USDC"],
            token_addresses={
                "base-sepolia:USDC": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
                "ethereum:USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            },
        )

    # ── Verification ───────────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify payment via Cloudflare x402 facilitator."""
        if not self._config.cloudflare_x402_url:
            return self._format_error("Cloudflare x402 URL not configured")

        accepted = payload.get("accepted", {})

        # Build x402-standard verify body
        body = {
            "x402Version": payload.get("x402Version", 2),
            "paymentPayload": payload,
        }
        if requirements:
            body["paymentRequirements"] = requirements

        headers = {
            "Content-Type": "application/json",
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self._config.cloudflare_x402_url}/verify"
                async with session.post(url, json=body, headers=headers) as resp:
                    data = await resp.json()

                    if resp.status != 200:
                        reason = data.get("error", data.get("message", f"HTTP {resp.status}"))
                        logger.warning(f"Cloudflare x402 returned {resp.status}: {reason}")
                        return self._format_error(f"Cloudflare x402: {reason}")

                    if data.get("isValid") or data.get("verified"):
                        return self._format_success(
                            tx_hash=data.get("txHash") or data.get("transactionHash"),
                            payer=data.get("payer") or data.get("from"),
                            amount=accepted.get("amount"),
                            chain=data.get("chain") or accepted.get("network", "").split(":")[-1],
                            token="USDC",
                            settlement_id=data.get("settlementId") or data.get("id"),
                        )

                    reason = data.get("invalidReason") or data.get("reason") or "Unknown rejection"
                    extra_msg = data.get("invalidMessage") or data.get("message") or ""
                    detail = f"{reason}: {extra_msg}" if extra_msg else reason
                    return self._format_error(detail)

        except TimeoutError:
            logger.error(f"Cloudflare x402 verify timeout after {self._config.facilitator_verify_timeout}s")
            return self._format_error("Cloudflare x402 verification timed out")
        except aiohttp.ClientError as e:
            logger.error(f"Cloudflare x402 HTTP error: {e}")
            return self._format_error(f"Cloudflare x402 connection error: {e}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.exception(f"Unexpected error in Cloudflare x402 verify: {e}")
            return self._format_error(f"Cloudflare x402 internal error: {e}")

    # ── Health ─────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if Cloudflare x402 facilitator is reachable."""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(
                    f"{self._config.cloudflare_x402_url}/health",
                ) as resp,
            ):
                if resp.status < 500:
                    logger.debug("Cloudflare x402 health check passed")
                    return True
                logger.warning(f"Cloudflare x402 health check failed: HTTP {resp.status}")
                return False
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"Cloudflare x402 health check error: {e}")
            return False

    # ── Settlement ────────────────────────────────────────────

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Settle a verified payment via Cloudflare x402 (deferred - may queue)."""
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self._config.cloudflare_x402_url}/settle"
                async with session.post(
                    url,
                    json={
                        "paymentData": payment_data,
                        "settlementType": "deferred",
                    },
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200:
                        return SettlementResult(
                            settled=True,
                            settlement_id=data.get("settlementId"),
                            tx_hash=data.get("txHash"),
                            chain=payment_data.get("chain"),
                            facilitator=self.name,
                            reason="Settled via Cloudflare x402",
                            extra={"deferred": True},
                        )
                    return SettlementResult(
                        settled=False,
                        reason=f"Cloudflare x402 settle failed: {data.get('error', 'HTTP ' + str(resp.status))}",
                        facilitator=self.name,
                        chain=payment_data.get("chain"),
                    )
        except TimeoutError:
            return SettlementResult(
                settled=False,
                reason="Cloudflare x402 settlement timed out",
                facilitator=self.name,
            )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            return SettlementResult(
                settled=False,
                reason=f"Cloudflare x402 settlement error: {e}",
                facilitator=self.name,
            )
