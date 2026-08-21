"""
Free wallet-label provider tiers (chain: ``wallet_labels_free``).

Split out of ``free_security.py`` (degodding phase 3, 2026-08-21).
Each provider returns ``dict | None`` and follows the DataBus provider
contract: return ``None`` to fall through to the next tier in the chain.

Tier order (registered in ``registry/categories/wallet.py`` — the LOCAL
ClickHouse tier 1 lives in ``chains/local.py``):
  2. ``_free_blockscout_labels``      — Blockscout address tags (free, no auth)
  3. ``_free_etherscan_labels``       — Etherscan V2 contract names (freemium, key)
  5. ``_free_wallet_pattern_heuristics`` — local known-entity + role heuristics
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.databus.providers.chains.free_security_common import (
    BLOCKSCOUT_URLS,
    ETHERSCAN_V2_CHAIN_IDS,
    _normalize_chain,
)

logger = get_logger("databus.providers.free_security")


async def _free_blockscout_labels(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 2 — Blockscout address tags (free, no auth).

    https://{chain}.blockscout.com/api/v2/addresses/{address}
    Returns contract name, tags, ENS, and scam flag when available.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    base_url = BLOCKSCOUT_URLS.get(chain)
    if not address or not base_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/api/v2/addresses/{address}")
            if resp.status_code != 200:
                logger.debug("blockscout_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            metadata = data.get("metadata") or {}
            public_tags = [t for t in (data.get("public_tags") or []) if isinstance(t, dict)]
            private_tags = [t for t in (data.get("private_tags") or []) if isinstance(t, dict)]
            tags = [t.get("display_name") or t.get("name") or "" for t in public_tags + private_tags]
            tags = [t for t in tags if t]

            label = data.get("name") or metadata.get("label") or (tags[0] if tags else "")

            result: dict[str, Any] = {
                "labels": [
                    {
                        "label": label,
                        "category": "blockscout_tag",
                        "source": "blockscout",
                        "confidence": 0.8 if label else 0.3,
                    }
                ]
                if label
                else [],
                "address": address,
                "chain": chain,
                "is_contract": data.get("is_contract", False),
                "is_verified": data.get("is_verified", False),
                "is_scam": data.get("is_scam", False),
                "ens_domain_name": data.get("ens_domain_name"),
                "tags": tags,
                "name": data.get("name", ""),
                "source": "blockscout",
                "tier": "free_api",
            }
            if not label and not tags and not data.get("is_contract"):
                # Nothing meaningful to return → fall through
                logger.debug("blockscout no tags for %s", address[:12])
                return None
            return result
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("blockscout_api_failed", chain=chain)
    return None


async def _free_etherscan_labels(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 3 — Etherscan-family labels via V2 API (free tier with key).

    https://api.etherscan.io/v2/api?chainid=1&module=contract&action=getsourcecode
    Returns the verified contract name as a label. Requires ETHERSCAN_API_KEY.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", ""))
    chain_id = ETHERSCAN_V2_CHAIN_IDS.get(chain)
    api_key = kwargs.get("api_key", "")
    if not address or chain_id is None or not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.etherscan.io/v2/api",
                params={
                    "chainid": chain_id,
                    "module": "contract",
                    "action": "getsourcecode",
                    "address": address,
                    "apikey": api_key,
                },
            )
            if resp.status_code != 200:
                logger.debug("etherscan_http_error", status=resp.status_code, chain=chain)
                return None
            data = resp.json()
            result = data.get("result") if isinstance(data, dict) else None
            if not isinstance(result, list) or not result:
                return None
            contract = result[0]
            if not isinstance(contract, dict):
                return None

            contract_name = (contract.get("ContractName") or "").strip()
            if not contract_name:
                return None

            return {
                "labels": [
                    {
                        "label": contract_name,
                        "category": "contract_name",
                        "source": "etherscan",
                        "confidence": 0.9,
                    }
                ],
                "address": address,
                "chain": chain,
                "contract_name": contract_name,
                "is_verified": bool(contract.get("ContractName")),
                "source": "etherscan",
                "tier": "freemium",
            }
    except (httpx.TimeoutException, ConnectionError, OSError, TimeoutError, httpx.HTTPError, ValueError, RuntimeError):
        logger.debug("etherscan_labels_failed", chain=chain)
    return None


async def _free_wallet_pattern_heuristics(**kwargs: Any) -> dict[str, Any] | None:
    """Tier 5 — pattern-based heuristics (whale, bot, DEX router, CEX).

    Uses the local known_entities module (EXCHANGE_WALLETS, EVM_KNOWN_CONTRACTS,
    KNOWN_MARKET_MAKERS) plus transfer-volume heuristics to label an address
    when no external label source has an entry. LOCAL tier, zero cost.
    """
    address = kwargs.get("address", kwargs.get("wallet", "")).lower()
    chain = _normalize_chain(kwargs.get("chain", "ethereum") or "ethereum")
    if not address:
        return None

    labels: list[dict[str, Any]] = []
    try:
        from app.domains.scanner.engines.known_entities import (
            get_exchange_name,
            is_bridge,
            is_cex_wallet,
            is_dex_contract,
            is_lending_protocol,
            is_market_maker,
            is_mixer,
            label_wallet,
        )

        # 1) Known-entity catalog
        known = label_wallet(address, chain)
        if known:
            entity_label = known.get("label") or known.get("name")
            if entity_label:
                labels.append(
                    {
                        "label": entity_label,
                        "category": known.get("category", "known_entity"),
                        "source": "known_entities",
                        "confidence": 0.95,
                    }
                )

        # 2) Role heuristics
        if is_cex_wallet(address, chain):
            ex_name = get_exchange_name(address, chain)
            labels.append(
                {
                    "label": f"CEX: {ex_name}" if ex_name else "CEX wallet",
                    "category": "exchange",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_dex_contract(address, chain):
            labels.append(
                {
                    "label": "DEX router/contract",
                    "category": "dex",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_market_maker(address, chain):
            labels.append(
                {
                    "label": "Market maker",
                    "category": "market_maker",
                    "source": "pattern_heuristic",
                    "confidence": 0.85,
                }
            )
        if is_bridge(address, chain):
            labels.append(
                {
                    "label": "Bridge contract",
                    "category": "bridge",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_lending_protocol(address, chain):
            labels.append(
                {
                    "label": "Lending protocol",
                    "category": "lending",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
        if is_mixer(address, chain):
            labels.append(
                {
                    "label": "Mixer",
                    "category": "mixer",
                    "source": "pattern_heuristic",
                    "confidence": 0.9,
                }
            )
    except ImportError:
        logger.debug("known_entities not available")
    except Exception as exc:
        logger.debug("pattern_heuristics failed: %s", str(exc)[:80])

    if not labels:
        return None

    return {
        "labels": labels,
        "address": address,
        "chain": chain,
        "source": "pattern_heuristics",
        "tier": "local",
        "is_local": True,
    }
