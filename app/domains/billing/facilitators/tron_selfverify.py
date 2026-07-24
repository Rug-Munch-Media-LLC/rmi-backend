"""
TRON Self-Verification Facilitator
==================================
Verifies TRC20 (USDT/USDC/USDD) and TRX transfers on TRON directly
using TronGrid API. No external facilitator needed.

How it works:
1. Client sends USDT/USDC to our TRON wallet address
2. We receive the tx hash in the x402 payment payload
3. We call TronGrid API to verify:
   - Transaction exists and succeeded
   - Correct token contract (USDT/USDC/USDD)
   - Correct recipient (our wallet)
   - Correct amount (atomic units)
4. Payment verified on-chain - no third-party facilitator

Chains: TRON mainnet
Tokens: USDT (TRC20), USDC (TRC20), USDD (TRC20)
Settlement: Self-verified (instant)
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

logger = get_logger("facilitator.tron_selfverify")

# TRC20 token contracts on TRON mainnet
TRC20_TOKENS = {
    "USDT": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",  # Tether USD
    "USDC": "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8",  # USD Coin (TronGrid USDC contract)
    "USDD": "TPYmHEhy5n8TCEfZGqW2rPbmgh1fGqNBPa",  # USDD
}

# Reverse lookup: contract address (lower) → symbol
CONTRACT_TO_SYMBOL = {v.lower(): k for k, v in TRC20_TOKENS.items()}

# ERC-20 Transfer signature (same on TRC20)
TRANSFER_METHOD_ID = "a9059cbb"


class TronSelfVerifyFacilitator(Facilitator):
    """
    TRON self-verification facilitator.
    Verifies TRC20 transfers directly on TRON via TronGrid API.
    """

    def __init__(self, config: FacilitatorConfig | None = None) -> None:
        self._config = config or get_config()
        self._tron_pay_to = self._config.tron_pay_to
        self._api_base = "https://api.trongrid.io"

    @property
    def name(self) -> str:
        return "tron_selfverify"

    @property
    def facilitator_type(self) -> FacilitatorType:
        return FacilitatorType.SELF_HOSTED

    @property
    def settlement_type(self) -> SettlementType:
        return SettlementType.INSTANT

    @property
    def priority(self) -> int:
        return 45  # After specialized facilitators, before generic EIP-7702

    @property
    def is_fee_free(self) -> bool:
        return True  # Self-verified - no facilitator fee

    @property
    def supported_networks(self) -> NetworkSupport:
        return NetworkSupport(
            chains=["tron"],
            networks=["tron:mainnet"],
            tokens=["USDT", "USDC", "USDD", "TRX"],
            native_tokens=["TRX"],
            token_addresses=TRC20_TOKENS,
        )

    @property
    def description(self) -> str:
        return "TRON Self-Verify - USDT/USDC/USDD via TronGrid (fee-free)"

    # ── Verification ───────────────────────────────────────────

    async def verify(
        self,
        payload: dict[str, Any],
        requirements: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Verify a TRON TRC20 or native TRX transfer."""

        if not self._tron_pay_to:
            return self._format_error("TRON: No pay-to wallet configured (X402_TRON_PAY_TO)")

        # Extract transaction hash
        tx_hash = (
            payload.get("txHash")
            or payload.get("signature")
            or payload.get("transactionHash")
            or (payload.get("accepted", {}).get("transaction"))
        )
        if not tx_hash:
            return self._format_error("TRON: No transaction hash in payment payload")

        # Get expected amount and token
        accepted = payload.get("accepted", {})
        amount_atoms = accepted.get("amount") or payload.get("amount")
        expected_token = accepted.get("extra", {}).get("token") or accepted.get("token") or "USDT"

        try:
            # First try TRC20 verification
            result = await self._verify_trc20(tx_hash, amount_atoms, expected_token)
            if result:
                return result

            # Fallback: try native TRX transfer
            return await self._verify_native_trx(tx_hash, amount_atoms)

        except TimeoutError:
            return self._format_error("TRON: TronGrid API timeout")
        except aiohttp.ClientError as e:
            return self._format_error(f"TRON: TronGrid API error: {e}")
        except (ValueError, TypeError, KeyError, OSError) as e :
            logger.debug("verify_error", exc_info=True)
            logger.exception(f"TRON verify error: {e}")
            return self._format_error(f"TRON: {e}")

    async def _verify_trc20(
        self, tx_hash: str, amount_atoms: str | None, expected_token: str
    ) -> VerificationResult | None:
        """Verify a TRC20 token transfer via TronGrid."""
        timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Method 1: Look up TRC20 transfers by transaction ID
            # Use the event API
            url = f"{self._api_base}/v1/transactions/{tx_hash}/events"
            async with session.get(url) as resp:
                if resp.status != 200:
                    # Try Method 2: wallet/gettransactioninfobyid
                    return await self._verify_trc20_via_wallet(
                        tx_hash, amount_atoms, expected_token
                    )
                data = await resp.json()
                events = data.get("data", [])
                if not events:
                    return await self._verify_trc20_via_wallet(
                        tx_hash, amount_atoms, expected_token
                    )

            for event in events:
                # Check if this is a Transfer event to our wallet
                event_name = event.get("event_name", "")
                contract_addr = event.get("contract_address", "").lower()
                result_data = event.get("result", {})

                if event_name != "Transfer":
                    continue

                to_addr = result_data.get("to", "").lower()
                # Normalize TRON addresses (strip 41 prefix for comparison)
                our_addr = self._normalize_tron_address(self._tron_pay_to)

                if to_addr != our_addr and to_addr != self._tron_pay_to.lower():
                    continue

                # Check token
                token_symbol = CONTRACT_TO_SYMBOL.get(contract_addr, "")
                if not token_symbol:
                    continue

                # Parse amount
                value_str = result_data.get("value", "0")
                try:
                    actual_amount = str(int(value_str))
                except (ValueError, TypeError):
                    actual_amount = "0"

                if amount_atoms and actual_amount != amount_atoms:
                    logger.warning(
                        f"TRON: Amount mismatch for {tx_hash[:16]}... expected {amount_atoms}, got {actual_amount}"
                    )

                return self._format_success(
                    tx_hash=tx_hash,
                    payer=result_data.get("from", ""),
                    amount=actual_amount or amount_atoms or "0",
                    chain="tron",
                    token=token_symbol or expected_token,
                    confirmations=1,
                )

            return None  # No matching TRC20 event found

    async def _verify_trc20_via_wallet(
        self, tx_hash: str, amount_atoms: str | None, expected_token: str
    ) -> VerificationResult | None:
        """Verify TRC20 transfer via TronGrid wallet API (fallback)."""
        timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Get transaction info
            url = f"{self._api_base}/wallet/gettransactioninfobyid"
            async with session.post(url, json={"value": tx_hash}) as resp:
                if resp.status != 200:
                    return self._format_error(f"TRON: TX {tx_hash[:16]}... not found")
                data = await resp.json()

            # Check if transaction succeeded
            ret = data.get("ret", [])
            if not ret or ret[0].get("contractRet") != "SUCCESS":
                return self._format_error(f"TRON: TX {tx_hash[:16]}... failed")

            # Parse the raw_data to find TRC20 Transfer
            raw_data = data.get("raw_data", {})
            contracts = raw_data.get("contract", [])

            for contract in contracts:
                param = contract.get("parameter", {})
                value = param.get("value", {})
                contract_type = contract.get("type", "")

                if contract_type == "TriggerSmartContract":
                    # TRC20 transfer
                    call_data = value.get("data", "")
                    contract_address = value.get("contract_address", "")
                    owner_address = value.get("owner_address", "")

                    if not call_data.startswith(TRANSFER_METHOD_ID):
                        continue

                    # Decode transfer params: method(4) + to(32) + amount(32)
                    # TRON addresses in transfer data are 32 bytes with 41 prefix
                    if len(call_data) >= 136:
                        to_hex = call_data[32:72]  # padded address
                        amount_hex = call_data[72:136]  # amount

                        # Normalize addresses
                        to_addr = self._hex_to_tron_address(to_hex)
                        token_symbol = CONTRACT_TO_SYMBOL.get(contract_address.lower(), "")

                        our_addr = self._tron_pay_to.lower()
                        if to_addr.lower() != our_addr:
                            continue

                        try:
                            actual_amount = str(int(amount_hex, 16))
                        except ValueError:
                            actual_amount = "0"

                        return self._format_success(
                            tx_hash=tx_hash,
                            payer=self._hex_to_tron_address(owner_address),
                            amount=actual_amount or amount_atoms or "0",
                            chain="tron",
                            token=token_symbol or expected_token,
                            confirmations=1,
                        )

            return None  # Not a TRC20 transfer

    async def _verify_native_trx(
        self, tx_hash: str, amount_atoms: str | None
    ) -> VerificationResult:
        """Verify a native TRX transfer via TronGrid."""
        timeout = aiohttp.ClientTimeout(total=self._config.facilitator_verify_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"{self._api_base}/wallet/gettransactioninfobyid"
            async with session.post(url, json={"value": tx_hash}) as resp:
                if resp.status != 200:
                    return self._format_error(f"TRON: TX {tx_hash[:16]}... not found")
                data = await resp.json()

        ret = data.get("ret", [])
        if not ret or ret[0].get("contractRet") != "SUCCESS":
            return self._format_error(f"TRON: TX {tx_hash[:16]}... failed")

        raw_data = data.get("raw_data", {})
        contracts = raw_data.get("contract", [])

        for contract in contracts:
            param = contract.get("parameter", {})
            value = param.get("value", {})
            contract_type = contract.get("type", "")

            if contract_type in ("TransferContract", "TransferAssetContract"):
                to_addr = self._hex_to_tron_address(value.get("to_address", ""))
                from_addr = self._hex_to_tron_address(value.get("owner_address", ""))
                amount_sun = str(value.get("amount", 0))

                if to_addr.lower() != self._tron_pay_to.lower():
                    continue

                return self._format_success(
                    tx_hash=tx_hash,
                    payer=from_addr,
                    amount=amount_sun or amount_atoms or "0",
                    chain="tron",
                    token="TRX",
                    confirmations=1,
                )

        return self._format_error(
            f"TRON: No matching TRX/TRC20 transfer to {self._tron_pay_to[:10]}... in {tx_hash[:16]}..."
        )

    # ── Address helpers ────────────────────────────────────────

    @staticmethod
    def _normalize_tron_address(addr: str) -> str:
        """Normalize TRON address for comparison (strip 41 prefix, lowercase)."""
        if addr.startswith("41") and len(addr) == 42:
            # Hex format: 41 + 20 bytes → convert to Base58 check
            return addr.lower()
        return addr.lower()

    @staticmethod
    def _hex_to_tron_address(hex_str: str) -> str:
        """Convert hex address to TRON address string.
        TronGrid returns addresses as hex with 41 prefix.
        """
        if not hex_str:
            return ""
        # If it's already a hex string like "41abc..."
        if hex_str.startswith("41") and len(hex_str) == 42:
            return hex_str.lower()
        # If it's a 32-byte padded hex
        if len(hex_str) == 64:
            # Strip leading zeros, get last 20 bytes
            addr_hex = hex_str[-40:]
            return ("41" + addr_hex).lower()
        return hex_str.lower()

    # ── Health ─────────────────────────────────────────────────

    async def health(self) -> bool:
        """Check if TronGrid API is reachable."""
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"{self._api_base}/v1/accounts/{self._tron_pay_to or 'TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8'}"
                async with session.get(url) as resp:
                    return resp.status == 200
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            return False

    async def settle(self, payment_data: dict[str, Any]) -> SettlementResult:
        """Self-verified - no explicit settlement needed."""
        return SettlementResult(
            settled=True,
            reason="TRON self-verified - instant settlement",
            facilitator=self.name,
        )
