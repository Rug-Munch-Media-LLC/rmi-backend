"""
x402 Middleware — Payment Verification Engine.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import FACILITATOR_CONFIGS

logger = logging.getLogger(__name__)


class PaymentVerifier:
    """
    x402 payment verification with local EIP-712 verification for Base
    and facilitator fallback for other networks.
    """

    def __init__(self) -> None:
        self._facilitators: dict[str, Any] = {}

    def _verify_eip712_local(self, payload_data: dict) -> dict[str, Any]:
        """Verify EIP-712 signed EIP-3009 authorization locally."""
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
            from x402.mechanisms.evm.types import DOMAIN_TYPES, ExactEIP3009Authorization
            from x402.mechanisms.evm.utils import get_evm_chain_id

            accepted = payload_data.get("accepted", {})
            inner = payload_data.get("payload", {})
            auth = inner.get("authorization", {})
            signature = inner.get("signature", "")

            if not all([auth, signature, accepted]):
                return {"verified": False, "reason": "Missing authorization or signature"}

            chain_id = get_evm_chain_id(accepted["network"])
            authorization = ExactEIP3009Authorization(
                from_address=auth["from"], to=auth["to"], value=auth["value"],
                valid_after=auth["validAfter"], valid_before=auth["validBefore"], nonce=auth["nonce"],
            )
            extra = accepted.get("extra", {})
            token_name = extra.get("name", "USD Coin")
            token_version = extra.get("version", "2")

            domain, types, primary_type, message = build_typed_data_for_signing(
                authorization, chain_id, accepted["asset"], token_name, token_version
            )

            now = int(time.time())
            if int(auth["validBefore"]) < now + 6:
                return {"verified": False, "reason": "Authorization expired"}
            if int(auth["validAfter"]) > now:
                return {"verified": False, "reason": "Authorization not yet valid"}

            all_types = {**DOMAIN_TYPES, **types}
            typed_data = {
                "types": {k: [{"name": f["name"], "type": f["type"]} for f in v] for k, v in all_types.items()},
                "primaryType": primary_type,
                "domain": {
                    "name": domain.name, "version": domain.version,
                    "chainId": domain.chain_id, "verifyingContract": domain.verifying_contract,
                },
                "message": {k: ("0x" + v.hex() if isinstance(v, bytes) else str(v)) for k, v in message.items()},
            }

            signable = encode_typed_data(full_message=typed_data)
            recovered = Account.recover_message(signable, signature=signature)

            if recovered.lower() != auth["from"].lower():
                return {"verified": False, "reason": f"Signature mismatch: recovered {recovered}"}

            if auth["to"].lower() != accepted.get("payTo", "").lower():
                return {"verified": False, "reason": "Recipient address mismatch"}
            if str(auth["value"]) != str(accepted.get("amount", "")):
                return {"verified": False, "reason": "Amount mismatch"}

            return {"verified": True, "reason": "EIP-712 signature verified locally",
                    "payer": auth["from"], "tx_hash": None}
        except Exception as e:
            logger.error(f"Local EIP-712 verification error: {e}")
            return {"verified": False, "reason": f"Local verification error: {e!s}"}

    async def _verify_via_payai(self, payload_data: dict, requirements_data: dict | None) -> dict[str, Any]:
        """Fallback: verify via PayAI facilitator HTTP."""
        import aiohttp
        accepted = payload_data.get("accepted", {})
        flat_req = {
            "x402Version": 2, "scheme": accepted.get("scheme", "exact"),
            "network": accepted.get("network", "eip155:8453"),
            "asset": accepted.get("asset", ""), "amount": accepted.get("amount", ""),
            "payTo": accepted.get("payTo", ""), "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 60),
            "extra": accepted.get("extra", {}), "resource": {},
        }
        if requirements_data:
            flat_req["resource"] = requirements_data.get("resource", {})

        body = {"x402Version": 2, "paymentPayload": payload_data, "paymentRequirements": flat_req}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://facilitator.payai.network/verify", json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    if data.get("isValid"):
                        return {"verified": True, "reason": "Verified via PayAI",
                                "payer": data.get("payer"), "tx_hash": None}
                    return {"verified": False, "reason": f"PayAI: {data.get('invalidReason', 'unknown')}",
                            "payer": data.get("payer")}
        except Exception as e:
            logger.error(f"PayAI verification error: {e}")
            return {"verified": False, "reason": f"PayAI connection error: {e!s}"}

    async def verify_payment(
        self, x402_payload: str, network_key: str = "base",
        expected_amount: str | None = None, expected_tool: str | None = None,
    ) -> dict[str, Any]:
        """Verify an x402 payment payload with layered verification."""
        try:
            payload_data = json.loads(x402_payload)
        except json.JSONDecodeError:
            return {"verified": False, "reason": "Invalid JSON in x402 payload",
                    "tx_hash": None, "amount": None, "network": network_key, "settlement_id": None}

        if network_key.startswith("base"):
            local_result = self._verify_eip712_local(payload_data)
            if local_result["verified"]:
                return {"verified": True, "reason": local_result["reason"],
                        "tx_hash": local_result.get("tx_hash"), "amount": expected_amount,
                        "network": network_key, "settlement_id": None, "payer": local_result.get("payer")}
            logger.warning(f"Local verification failed: {local_result['reason']}")

        config = FACILITATOR_CONFIGS.get(network_key, FACILITATOR_CONFIGS.get("base", {}))
        requirements_data = None
        try:
            accepted = payload_data.get("accepted", {})
            resource = payload_data.get("resource", {})
            requirements_data = {
                "x402Version": payload_data.get("x402Version", 2), "resource": resource,
                "accepts": [{"scheme": accepted.get("scheme", "exact"),
                             "network": accepted.get("network", config.get("network", "eip155:8453")),
                             "asset": accepted.get("asset", ""), "amount": accepted.get("amount", ""),
                             "payTo": accepted.get("payTo", ""),
                             "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 60),
                             "extra": accepted.get("extra", {})}],
            }
        except (TypeError, ValueError, KeyError):
            pass
        return await self._verify_via_payai(payload_data, requirements_data)
