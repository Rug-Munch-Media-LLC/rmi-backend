"""
PayAI x402 Facilitator
=======================
Hosted facilitator that verifies x402 payments via the PayAI API.
Supports Base (mainnet + Sepolia) and Solana (mainnet + devnet)
with USDC token - settlement is deferred (batched by PayAI).

Priority: 20 (second after Coinbase CDP for Base; primary for Solana)
"""

from __future__ import annotations

from typing import Any

import aiohttp

from app.core.logging import get_logger
from app.domains.billing.facilitators.base import (
    Facilitator,
    FacilitatorType,
    NetworkSupport,
    SettlementType,
    VerificationResult,
)
from app.domains.billing.facilitators.config import get_config


import httpx

logger = get_logger("facilitator.payai")

# ── Verify URL ──────────────────────────────────────────────────────
PAYAI_VERIFY_URL = "https://facilitator.payai.network/verify"


class PayAIFacilitator(Facilitator):
    """PayAI hosted payment facilitator.

    Routes Base and Solana USDC payments through PayAI's verification API.
    Settlements are batched by PayAI (DEFERRED), so no explicit settle()
    call is needed - the default base-class no-op handles this.
    """

    # ── Identity ───────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "payai"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.DEFERRED

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["base", "solana"],
            chain_ids=[8453, 84532],
            networks=[
                "eip155:8453",
                "eip155:84532",
                "solana:mainnet",
                "solana:devnet",
            ],
            tokens=["USDC"],
            token_addresses={
                # USDC on Base mainnet
                "USDC:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                # USDC on Base Sepolia
                "USDC:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                # USDC on Solana mainnet
                "USDC:solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            },
        )

    @property
    def priority(self) -> int:
        return 20

    @property
    def verify_url(self) -> str | None:
        return self._verify_url

    # ── Constructor ────────────────────────────────────────────────

    def __init__(self) -> None:
        config = get_config()
        self._verify_url = config.payai_verify_url or PAYAI_VERIFY_URL

    # ── Health Check ───────────────────────────────────────────────

    async def health(self) -> bool:
        """Ping the PayAI verify endpoint to check reachability.

        Sends a lightweight health-check payload. Any non-5xx response
        means the API is reachable.
        """
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    self._verify_url,
                    json={"health_check": True, "x402Version": 2},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp,
            ):
                if resp.status < 500:
                    logger.debug("PayAI health check: HTTP %d", resp.status)
                    return True

                logger.warning("PayAI health check failed: HTTP %d", resp.status)
                return False

        except TimeoutError:
            logger.warning("PayAI health check timed out")
            return False
        except aiohttp.ClientError as e:
            logger.warning("PayAI health check connection error: %s", e)
            return False
        except (httpx.HTTPError, httpx.TimeoutException):
            logger.exception("PayAI health check unexpected error")
            return False

    # ── Verification ───────────────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify an x402 payment via the PayAI API.

        Builds the same body format PayAI expects (mirroring the
        original _verify_via_payai logic from x402_middleware.py)
        and POSTs it to https://facilitator.payai.network/verify.

        Args:
            payload: x402 payment payload (parsed JSON) containing
                     an "accepted" block with scheme, network, asset,
                     amount, payTo, maxTimeoutSeconds, and extra.
            requirements: Optional payment requirements dict whose
                          "resource" key is forwarded to PayAI.

        Returns:
            VerificationResult with verification status and details.
        """
        try:
            # Build flattened requirements format PayAI expects
            accepted = payload.get("accepted", {})
            flat_req: dict[str, Any] = {
                "x402Version": 2,
                "scheme": accepted.get("scheme", "exact"),
                "network": accepted.get("network", "eip155:8453"),
                "asset": accepted.get("asset", ""),
                "amount": accepted.get("amount", ""),
                "payTo": accepted.get("payTo", ""),
                "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 60),
                "extra": accepted.get("extra", {}),
                "resource": {},
            }
            if requirements and requirements.get("resource"):
                flat_req["resource"] = requirements["resource"]

            body = {
                "x402Version": 2,
                "paymentPayload": payload,
                "paymentRequirements": flat_req,
            }

            config = get_config()
            timeout = aiohttp.ClientTimeout(total=config.facilitator_verify_timeout)

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    self._verify_url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=timeout,
                ) as resp,
            ):
                data = await resp.json()

                if data.get("isValid"):
                    logger.info(
                        "PayAI verification succeeded: payer=%s",
                        data.get("payer", "N/A"),
                    )
                    return self._format_success(
                        payer=data.get("payer"),
                        chain=data.get("chain"),
                        token=data.get("token", "USDC"),
                        amount=data.get("amount"),
                        amount_usd=data.get("amount_usd"),
                        tx_hash=data.get("tx_hash"),
                        settlement_id=data.get("settlement_id"),
                        block_number=data.get("block_number"),
                        confirmations=data.get("confirmations"),
                        raw_response=data,
                    )

                reason = f"PayAI: {data.get('invalidReason', 'unknown')} - {data.get('invalidMessage', '')}"
                logger.warning("PayAI verification failed: %s", reason)
                return self._format_error(
                    reason,
                    payer=data.get("payer"),
                    raw_response=data,
                )

        except TimeoutError:
            logger.error("PayAI verify timed out")
            return self._format_error("PayAI API verification timed out")
        except aiohttp.ClientError as e:
            logger.error("PayAI HTTP error during verify: %s", e)
            return self._format_error(f"PayAI connection error: {e}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            logger.exception("PayAI verify unexpected error")
            return self._format_error("Unexpected PayAI verify error")
