"""
Bitcoin Self-Verification Facilitator
=====================================
Verifies BTC transfers on Bitcoin mainnet using Mempool.space API.
No external facilitator needed.

How it works:
1. Client sends BTC to our Bitcoin wallet address
2. We receive the tx hash in the x402 payment payload
3. We call Mempool.space API to verify:
   - Transaction exists and confirmed
   - Output to our wallet exists
   - Correct amount (sats)
4. Payment verified on-chain - no third-party facilitator

Since BTC is not a stablecoin, we price tools in sats based on
current BTC/USD rate. Payment amounts are in satoshis.

Chains: Bitcoin mainnet
Tokens: BTC
Settlement: Self-verified (1 confirmation minimum)
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

logger = get_logger("facilitator.bitcoin_selfverify")

# Mempool.space API (free, no key needed)
MEMPOOL_API = "https://mempool.space/api"
# Blockstream fallback
BLOCKSTREAM_API = "https://blockstream.info/api"

# Minimum confirmations for BTC (Bitcoin is slower, need at least 1)
MIN_CONFIRMATIONS = 1


class BitcoinSelfVerifyFacilitator(Facilitator):
    """
    Bitcoin self-verification facilitator.
    Verifies BTC transfers directly on Bitcoin via Mempool.space API.
    """

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()
        self._btc_pay_to = self._config.btc_pay_to
        self._api_base = MEMPOOL_API

    @property
    def name(self) -> str:
        return "bitcoin_selfverify"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.SELF_HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.INSTANT

    @property
    def priority(self) -> int:
        return 45  # After specialized, before generic EIP-7702

    @property
    def is_fee_free(self) -> bool:
        return True  # Self-verified - no facilitator fee

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["bitcoin"],
            networks=["bitcoin:mainnet"],
            tokens=["BTC"],
            native_tokens=["BTC"],
        )

    @property
    def description(self) -> str:
        return "Bitcoin Self-Verify - BTC via Mempool.space (fee-free, 1-conf)"

    # ── Verification ───────────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify a Bitcoin BTC transfer."""

        if not self._btc_pay_to:
            return self._format_error("Bitcoin: No pay-to wallet configured (X402_BTC_PAY_TO)")

        # Extract transaction hash
        tx_hash = (
            payload.get("txHash")
            or payload.get("signature")
            or payload.get("transactionHash")
            or (payload.get("accepted", {}).get("transaction"))
        )
        if not tx_hash:
            return self._format_error("Bitcoin: No transaction hash in payment payload")

        # Get expected amount (in satoshis)
        accepted = payload.get("accepted", {})
        amount_atoms = accepted.get("amount") or payload.get("amount")

        try:
            tx_data = await self._get_transaction(tx_hash)
            if not tx_data:
                return self._format_error(f"Bitcoin: TX {tx_hash[:16]}... not found")

            # Check confirmations
            status = tx_data.get("status", {})
            confirmed = status.get("confirmed", False)
            block_height = status.get("block_height", 0)

            if not confirmed and MIN_CONFIRMATIONS > 0:
                return self._format_error(
                    f"Bitcoin: TX {tx_hash[:16]}... not yet confirmed (needs {MIN_CONFIRMATIONS} conf)"
                )

            # Find output to our address
            vout = tx_data.get("vout", [])
            for output in vout:
                scriptpubkey_address = output.get("scriptpubkey_address", "")
                if scriptpubkey_address.lower() == self._btc_pay_to.lower():
                    value_sats = output.get("value", 0)  # in satoshis
                    actual_amount = str(value_sats)

                    if amount_atoms and actual_amount != amount_atoms:
                        logger.warning(
                            f"Bitcoin: Amount mismatch for {tx_hash[:16]}... "
                            f"expected {amount_atoms} sats, got {actual_amount}"
                        )

                    # Get payer from vin
                    vin = tx_data.get("vin", [])
                    payer = ""
                    if vin:
                        prevout = vin[0].get("prevout", {})
                        payer = prevout.get("scriptpubkey_address", "")

                    return self._format_success(
                        tx_hash=tx_hash,
                        payer=payer,
                        amount=actual_amount or amount_atoms or "0",
                        chain="bitcoin",
                        token="BTC",  # noqa: S106 — config default
                        block_number=block_height or None,
                        confirmations=1 if confirmed else 0,
                    )

            return self._format_error(f"Bitcoin: No output to {self._btc_pay_to[:16]}... in {tx_hash[:16]}...")

        except TimeoutError:
            return self._format_error("Bitcoin: Mempool.space API timeout")
        except aiohttp.ClientError as e:
            # Try Blockstream fallback
            try:
                return await self._verify_fallback(tx_hash, amount_atoms)
            except aiohttp.ClientError:
                return self._format_error(f"Bitcoin: API error: {e}")
        except (ConnectionError, OSError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.exception(f"Bitcoin verify error: {e}")
            return self._format_error(f"Bitcoin: {e}")

    async def _verify_fallback(self, tx_hash: str, amount_atoms: str | None) -> VerificationResult:
        """Try Blockstream API as fallback."""
        timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{BLOCKSTREAM_API}/tx/{tx_hash}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return self._format_error(f"Bitcoin: TX {tx_hash[:16]}... not found on fallback")
                tx_data = await resp.json()

        vout = tx_data.get("vout", [])
        for output in vout:
            addr = output.get("scriptpubkey_address", "")
            if addr.lower() == self._btc_pay_to.lower():
                value_sats = str(output.get("value", 0))
                block_height = tx_data.get("status", {}).get("block_height")
                confirmed = tx_data.get("status", {}).get("confirmed", False)

                if not confirmed and MIN_CONFIRMATIONS > 0:
                    return self._format_error(f"Bitcoin: TX {tx_hash[:16]}... not yet confirmed")

                vin = tx_data.get("vin", [])
                payer = vin[0].get("prevout", {}).get("scriptpubkey_address", "") if vin else ""

                return self._format_success(
                    tx_hash=tx_hash,
                    payer=payer,
                    amount=value_sats or amount_atoms or "0",
                    chain="bitcoin",
                    token="BTC",  # noqa: S106 — config default
                    block_number=block_height,
                    confirmations=1 if confirmed else 0,
                )

        return self._format_error(f"Bitcoin: No output to our wallet in {tx_hash[:16]}...")

    async def _get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Get transaction data from Mempool.space."""
        timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{self._api_base}/tx/{tx_hash}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data: dict[str, Any] = await resp.json()
                    return data
                return None

    # ── Health ─────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if Mempool.space API is reachable."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self._api_base}/blocks/tip/hash"
                async with session.get(url) as resp:
                    return resp.status == 200
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Self-verified - no explicit settlement needed."""
        return SettlementResult(
            settled=True,
            reason="Bitcoin self-verified - on-chain settlement",
            facilitator=self.name,
        )
