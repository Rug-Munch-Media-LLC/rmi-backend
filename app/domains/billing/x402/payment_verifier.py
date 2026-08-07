"""Payment verifier — validates x402 payment payloads and headers.

Extracted from enforcement.py (P6 - complexity refactor).

Responsibility:
- Parse v1/v2 payment headers (x-pay, PAYMENT-SIGNATURE)
- Route verification through FacilitatorRouter or fallback PaymentVerifier
- Self-verify EVM USDC transfers via Blockscout on-chain checking
- Idempotency checking (prevent double-spend)
"""

from __future__ import annotations

import base64 as _base64
import json
import time
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.redis import get_redis
from app.domains.billing.x402.pricing_catalog import (
    CHAIN_USDC,
    EVM_PAY_TO,
    TOOL_PRICES,
    _ensure_pay_addresses,
    _ensure_tool_prices,
)

logger = get_logger("x402_enforcement")

VERIFIER = None
_FACILITATOR_CONFIGS = None
_TOKEN_METADATA = None
_verifier_loaded = False


def _ensure_verifier() -> None:
    global VERIFIER, _FACILITATOR_CONFIGS, _TOKEN_METADATA, _verifier_loaded
    if _verifier_loaded:
        return
    _verifier_loaded = True
    try:
        from app.routers.x402_middleware import FACILITATOR_CONFIGS, TOKEN_METADATA, PaymentVerifier  # type: ignore[attr-defined]

        VERIFIER = PaymentVerifier()
        _FACILITATOR_CONFIGS = FACILITATOR_CONFIGS
        _TOKEN_METADATA = TOKEN_METADATA
        logger.info("x402 enforcement: using existing PaymentVerifier")
    except ImportError as e:
        VERIFIER = None
        logger.warning(f"x402 enforcement: PaymentVerifier not available: {e}")


def check_idempotency(key: str, ttl: int = 86400) -> bool:
    """Atomic SET NX check. Returns True if first call with this key."""
    try:
        r = get_redis()
        if not r:
            return True
        return bool(r.set(key, "1", ex=ttl, nx=True))
    except (ConnectionError, OSError, TimeoutError):
        return True


def parse_x_pay_header(header_value: str) -> dict[str, Any] | None:
    """Parse payment payload from x-pay / PAYMENT-SIGNATURE header.

    Supports: v2 spec base64-encoded JSON, v1 "x402 <json>", plain JSON.
    """
    if not header_value or not header_value.strip():
        return None
    try:
        stripped = header_value.strip()

        try:
            decoded = _base64.b64decode(stripped).decode("utf-8")
            if decoded.startswith("{"):
                payload = json.loads(decoded)
                if "x402Version" in payload:
                    return payload  # type: ignore[no-any-return]
                return payload  # type: ignore[no-any-return]
        except (MemoryError, KeyboardInterrupt, SystemExit):
            raise
        except json.JSONDecodeError:
            pass

        if stripped.startswith("x402 "):
            payload_str = stripped[5:].strip()
        elif stripped.startswith("X-Pay: "):
            payload_str = stripped[7:].strip()
        else:
            payload_str = stripped

        if payload_str.startswith("{"):
            return json.loads(payload_str)  # type: ignore[no-any-return]

        return None
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError) as e:
        logger.debug("parse_x_pay_header_error", exc_info=True)
        logger.warning(f"Failed to parse payment header: {e}")
        return None


def _detect_token_from_asset(asset_address: str, chain_cfg: dict[str, Any]) -> str:
    """Detect token symbol from asset address using chain config."""
    asset_lower = asset_address.lower()

    if chain_cfg.get("usdc", "").lower() == asset_lower:
        return "USDC"

    tokens = chain_cfg.get("tokens", {})
    for symbol, addr in tokens.items():
        if isinstance(addr, str) and addr.lower() == asset_lower:
            return symbol  # type: ignore[no-any-return]

    if "TR7NH" in asset_lower:
        return "USDT"
    if "TEkxi" in asset_lower:
        return "USDC"
    if "TPYm" in asset_lower:
        return "USDD"
    if asset_lower == "native" or asset_address == "BTC":
        return "BTC"

    return "USDC"


async def verify_payment(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify x402 payment payload against all supported chains (fallback)."""
    if not VERIFIER:
        return {"verified": False, "reason": "PaymentVerifier not initialized"}

    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")

    chain_key = None
    chain_cfg = None
    for ck, cc in CHAIN_USDC.items():
        if cc["network"] == network:
            chain_key = ck
            chain_cfg = cc
            break

    if not chain_cfg:
        return {"verified": False, "reason": f"Unsupported network: {network}"}

    verify_method = chain_cfg.get("verify", "facilitator")

    if verify_method == "facilitator":
        if chain_cfg["method"] == "local_eip712":
            return VERIFIER._verify_eip712_local(payload)  # type: ignore[no-any-return]
        elif chain_cfg["method"] == "payai":
            return await VERIFIER._verify_via_payai(payload, None)  # type: ignore[no-any-return]

    if verify_method == "self":
        return await self_verify_evm_usdc(payload, chain_key or "", chain_cfg)

    return {"verified": False, "reason": f"Unknown verify method: {verify_method}"}


async def verify_payment_via_router(payload: dict[str, Any]) -> dict[str, Any]:
    """Verify x402 payment payload through the multi-facilitator smart router."""
    accepted = payload.get("accepted", {})
    network = accepted.get("network", "")
    asset_address = accepted.get("asset", "")

    chain_key = None
    token_symbol = "USDC"

    for ck, cfg in CHAIN_USDC.items():
        if cfg["network"] == network:
            chain_key = ck
            if asset_address:
                token_symbol = _detect_token_from_asset(asset_address, cfg)
            break

    if not chain_key:
        try:
            from app.routers.x402_middleware import PaymentVerifier  # type: ignore[attr-defined]

            verifier = PaymentVerifier()
            return await verifier.verify_payment(  # type: ignore[no-any-return]
                json.dumps(payload),
                network_key="base",
            )
        except ImportError as e:
            logger.error(f"Fallback verifier import failed: {e}")
            return {"verified": False, "reason": "No verifier available for unknown network"}

    try:
        from app.domains.billing.facilitators.router import get_facilitator_router

        router = get_facilitator_router()

        requirements = {
            "x402Version": payload.get("x402Version", 2),
            "resource": payload.get("resource", {}),
            "accepts": [
                {
                    "scheme": accepted.get("scheme", "exact"),
                    "network": network,
                    "asset": asset_address,
                    "amount": accepted.get("amount", ""),
                    "payTo": accepted.get("payTo", ""),
                    "maxTimeoutSeconds": accepted.get("maxTimeoutSeconds", 180),
                    "extra": accepted.get("extra", {}),
                }
            ],
        }

        result = await router.verify(
            payload=payload,
            chain_key=chain_key,
            token_symbol=token_symbol,
            requirements=requirements,
        )

        if result.verified:
            logger.info(
                f"Router verified payment via {result.facilitator}: "
                f"chain={chain_key} token={token_symbol} amount={result.amount}"
            )
            return {
                "verified": True,
                "reason": result.reason,
                "tx_hash": result.tx_hash,
                "payer": result.payer,
                "amount": result.amount,
                "chain": chain_key,
                "token": token_symbol,
                "facilitator": result.facilitator,
                "method": f"router:{result.facilitator}",
            }
        else:
            logger.warning(f"Router rejected payment for {chain_key}/{token_symbol}: {result.reason}")
            return {
                "verified": False,
                "reason": result.reason,
                "chain": chain_key,
                "token": token_symbol,
            }

    except ImportError:
        logger.debug("Facilitator router not available - falling back to old verifier")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Router verification error: {e} - falling back to old verifier")

    return await verify_payment(payload)


async def self_verify_evm_usdc(payload: dict[str, Any], chain_key: str, chain_cfg: dict[str, Any]) -> dict[str, Any]:
    """Self-verify a USDC transfer on EVM chains without a facilitator.

    Checks transfer receipt via Blockscout API, validates USDC contract
    and amount, and prevents double-spend with Redis atomic lock.
    """
    TRANSFER_EVENT_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    try:
        accepted = payload.get("accepted", {})
        tx_hash = payload.get("txHash") or payload.get("signature") or accepted.get("transaction")
        payer = payload.get("payer") or payload.get("from") or accepted.get("payer")
        amount_atoms = accepted.get("amount") or payload.get("amount")

        if not tx_hash:
            return {"verified": False, "reason": "No tx hash in payment payload"}

        r = get_redis()
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            spent = r.set(spent_key, "pending", ex=86400, nx=True)
            if not spent:
                return {"verified": False, "reason": f"Transaction {tx_hash[:16]}... already used"}

        tool_id = (
            accepted.get("extra", {}).get("tool") or payload.get("tool") or accepted.get("resource", "").split("/")[-1]
            if accepted.get("resource")
            else None
        )
        _ensure_tool_prices()
        if not amount_atoms and tool_id and tool_id in TOOL_PRICES:
            amount_atoms = TOOL_PRICES[tool_id].get("price_atoms")

        chain_id = chain_cfg.get("chain_id")
        blockscout_urls = {
            1: "https://eth.blockscout.com",
            42161: "https://arbitrum.blockscout.com",
            10: "https://optimism.blockscout.com",
            137: "https://polygon.blockscout.com",
            8453: "https://base.blockscout.com",
            100: "https://gnosis.blockscout.com",
            43114: "https://avalanche.blockscout.com",
            250: "https://fantom.blockscout.com",
            56: "https://bsc.blockscout.com",
        }

        blockscout_base = blockscout_urls.get(chain_id)  # type: ignore[arg-type]
        if not blockscout_base:
            return {"verified": False, "reason": f"No Blockscout URL for chain_id {chain_id}"}

        import httpx

        tx_url = f"{blockscout_base}/api/v2/transactions/{tx_hash}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(tx_url)
            if resp.status_code != 200:
                return {
                    "verified": False,
                    "reason": f"TX {tx_hash[:16]}... not found on {chain_key} (HTTP {resp.status_code})",
                }
            tx_data = resp.json()

        tx_status = tx_data.get("status") or tx_data.get("result")
        if tx_status != "ok" and tx_status != "1":
            return {
                "verified": False,
                "reason": f"TX {tx_hash[:16]}... failed on-chain (status={tx_status})",
            }

        _ensure_pay_addresses()
        our_address = (EVM_PAY_TO or "").lower()
        usdc_address = chain_cfg["usdc"].lower()
        expected_amount = str(amount_atoms) if amount_atoms else None

        logs = tx_data.get("decoded_input", {}).get("logs") or tx_data.get("logs") or []
        if not logs:
            receipt_url = f"{blockscout_base}/api?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}"
            async with httpx.AsyncClient(timeout=10) as client2:
                r2 = await client2.get(receipt_url)
                if r2.status_code == 200:
                    receipt_data = r2.json()
                    logs = receipt_data.get("result", {}).get("logs", []) or receipt_data.get("logs", [])

        transfer_found = False
        amount_match = False
        actual_amount = None

        for log in logs:
            topics = log.get("topics", [])

            if len(topics) < 3:
                continue
            if topics[0].lower() != TRANSFER_EVENT_TOPIC0:
                continue
            if log.get("address", "").lower() != usdc_address:
                continue

            try:
                to_address = "0x" + topics[2][-40:].lower()
            except (IndexError, ValueError):
                continue

            if to_address != our_address:
                continue

            transfer_found = True
            log_data = log.get("data", "0x")
            try:
                if log_data and log_data.startswith("0x") and len(log_data) >= 66:
                    actual_amount = str(int(log_data[:66], 16))
                elif log_data:
                    actual_amount = str(int(log_data, 16))
            except (ValueError, TypeError):
                logger.warning(f"Could not decode Transfer data for {tx_hash[:16]}...: {str(log_data)[:20]}")

            if expected_amount and actual_amount:
                if actual_amount == expected_amount:
                    amount_match = True
                else:
                    logger.warning(
                        f"Amount mismatch for {tx_hash[:16]}...: "
                        f"expected {expected_amount} atoms, got {actual_amount} atoms on {chain_key}"
                    )
            elif actual_amount:
                for _tid, pricing in TOOL_PRICES.items():
                    if str(pricing.get("price_atoms")) == actual_amount:
                        amount_match = True
                        break

            break

        if not transfer_found:
            return {
                "verified": False,
                "reason": f"No USDC Transfer to {our_address[:10]}... found in {tx_hash[:16]}...",
            }

        if not amount_match and expected_amount and actual_amount:
            return {
                "verified": False,
                "reason": f"Amount mismatch: expected {expected_amount} atoms, got {actual_amount} atoms",
            }

        tool_name = tool_id or "unknown"
        if r:
            spent_key = f"x402:spent_tx:{tx_hash}"
            payment_data = {
                "chain": chain_key,
                "payer": payer or "unknown",
                "amount": actual_amount or amount_atoms or "0",
                "tool": tool_name,
                "timestamp": time.time(),
            }
            r.setex(spent_key, 86400, json.dumps(payment_data))

        try:
            import asyncio

            from app.routers.x402_dashboard import _persist_payment_to_supabase

            asyncio.create_task(
                _persist_payment_to_supabase(
                    tool=tool_name,
                    amount_atoms=str(actual_amount or amount_atoms or "0"),
                    chain=chain_key,
                    payer=payer or "unknown",
                    tx_hash=tx_hash,
                    status="fulfilled",
                )
            )
        except (AttributeError, ImportError):
            pass

        logger.info(
            f"Verified USDC payment: {tx_hash[:16]}... on {chain_key} "
            f"from {payer[:10] if payer else 'unknown'}... amount={actual_amount or 'unknown'} tool={tool_name}"
        )
        return {
            "verified": True,
            "chain": chain_key,
            "tx_hash": tx_hash,
            "payer": payer,
            "amount": actual_amount or amount_atoms,
            "method": "blockscout-verify",
        }

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Self-verify error: {e}")
        return {"verified": False, "reason": f"Verification error: {str(e)[:100]}"}
