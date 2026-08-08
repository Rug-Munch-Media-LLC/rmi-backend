"""Enhanced Transaction provider — full decoded transactions (Helius-style).

Emits ``transaction.enhanced`` events: complete decoded transactions with
instruction-level detail (token transfers, native SOL/ETH transfers, swap
classification, account balance deltas) instead of the aggregated rows our
poll providers emit from ClickHouse.

Data sources (free-first, fail-open):
  1. Solana — Helius RPC ``getParsedTransaction`` (jsonParsed) when
     ``HELIUS_API_KEY`` is configured, else the free public
     ``api.mainnet-beta.solana.com`` RPC. Signatures are discovered with
     ``getSignaturesForAddress`` for each watched wallet/token.
  2. EVM — Alchemy ``alchemy_getAssetTransfers`` (erc20/erc721/erc1155)
     when ``ALCHEMY_API_KEY`` is configured. No EVM events are emitted when
     the key is absent (fail-open).

Watch addresses are injected into the cursor by ``WebhookEngine._poll_loop``
from active subscriptions (``filters.wallet_addresses`` /
``filters.token_addresses``). The provider dedupes by transaction signature
via ``cursor["seen_sigs"]`` (bounded ring).

Deliberately mirror of Helius webhook semantics: the payload shape matches
the Helius "enhanced transaction" webhook body — ``description``, ``type``,
``tokenTransfers``, ``nativeTransfers``, ``accountData``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.tx_decoder import (
    decode_evm_receipt,
    decode_solana_transaction,
)

log = get_logger(__name__)

# Free public Solana RPC fallback (same endpoint as new_block provider)
PUBLIC_SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# Per-poll signature discovery limits (keeps polling cheap + bounded)
SIG_LIMIT_PER_ADDRESS = 10
MAX_NEW_SIGS_PER_POLL = 30
MAX_SEEN_SIGS = 200

# EVM networks we poll via Alchemy when a key is present
EVM_NETWORKS: dict[str, str] = {
    "ethereum": "eth",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "base": "base",
}


class EnhancedTransactionProvider:
    """Polls Solana + EVM RPCs for full decoded transactions on watched accounts."""

    event_type: WebhookEventType = WebhookEventType.TRANSACTION_ENHANCED
    poll_interval: int = 30  # seconds — keep up with blocks without hammering RPCs

    def __init__(self) -> None:
        self._helius_key = os.getenv("HELIUS_API_KEY", "").strip()
        self._alchemy_key = os.getenv("ALCHEMY_API_KEY", "").strip()
        self._solana_rpc = (
            f"https://mainnet.helius-rpc.com/?api-key={self._helius_key}" if self._helius_key else PUBLIC_SOLANA_RPC
        )

    # ── Poll interface ─────────────────────────────────────────────────

    async def poll(self, cursor: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch + decode recent transactions for watched wallets/tokens.

        Watch addresses come from the cursor (injected by the engine from
        active subscriptions). Fails open: any RPC error skips that address
        and the poll continues.
        """
        cursor = cursor or {}
        wallet_addresses: list[str] = [a.lower() for a in (cursor.get("wallet_addresses") or [])]
        token_addresses: list[str] = [a.lower() for a in (cursor.get("token_addresses") or [])]
        seen_sigs: set[str] = set(cursor.get("seen_sigs") or [])

        if not wallet_addresses and not token_addresses:
            log.debug("enhanced_tx_no_watch_targets")
            return []

        events: list[dict[str, Any]] = []

        # Solana: watched wallets + token mints
        sol_watches = [w for w in wallet_addresses if w and not w.startswith("0x")]
        sol_mints = [t for t in token_addresses if t and not t.startswith("0x")]
        events.extend(await self._poll_solana(sol_watches, sol_mints, seen_sigs))

        # EVM: watched wallets (0x addresses)
        evm_wallets = [w for w in wallet_addresses if w.startswith("0x")]
        if self._alchemy_key:
            events.extend(await self._poll_evm(evm_wallets, seen_sigs))
        elif evm_wallets:
            log.debug("enhanced_tx_evm_skipped_no_alchemy_key")

        # Persist bounded seen-signature ring through the engine cursor
        if seen_sigs:
            cursor["seen_sigs"] = list(seen_sigs)[-MAX_SEEN_SIGS:]

        return events

    # ── Solana path ────────────────────────────────────────────────────

    async def _poll_solana(
        self,
        wallets: list[str],
        mints: list[str],
        seen_sigs: set[str],
    ) -> list[dict[str, Any]]:
        """Discover + decode recent transactions for Solana watches."""
        events: list[dict[str, Any]] = []
        addresses = [*wallets, *mints]
        new_sig_count = 0

        async with httpx.AsyncClient(timeout=12.0) as client:
            for address in addresses:
                if new_sig_count >= MAX_NEW_SIGS_PER_POLL:
                    break
                try:
                    sigs = await self._sol_get_signatures(client, address)
                except (ValueError, ConnectionError, RuntimeError, httpx.HTTPError) as exc:
                    log.debug("enhanced_tx_sigs_failed addr=%s err=%s", address, exc)
                    continue

                for sig in sigs:
                    if sig in seen_sigs or new_sig_count >= MAX_NEW_SIGS_PER_POLL:
                        continue
                    try:
                        raw_tx = await self._sol_get_parsed_tx(client, sig)
                    except (ValueError, ConnectionError, RuntimeError, httpx.HTTPError) as exc:
                        log.debug("enhanced_tx_fetch_failed sig=%s err=%s", sig, exc)
                        continue

                    decoded = decode_solana_transaction(raw_tx)
                    if decoded is None:
                        continue

                    seen_sigs.add(sig)
                    new_sig_count += 1

                    event = self._build_event(decoded, address)
                    if event is not None:
                        events.append(event)

        return events

    async def _sol_get_signatures(self, client: httpx.AsyncClient, address: str) -> list[str]:
        """Get recent signatures for an address (limit, newest first)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": SIG_LIMIT_PER_ADDRESS}],
        }
        resp = await client.post(self._solana_rpc, json=payload)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or []
        return [str(item.get("signature", "")) for item in result if item.get("signature")]

    async def _sol_get_parsed_tx(self, client: httpx.AsyncClient, signature: str) -> dict[str, Any] | None:
        """Fetch a jsonParsed transaction (Helius-style decoded)."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
            ],
        }
        resp = await client.post(self._solana_rpc, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise RuntimeError(f"solana rpc error: {data['error']}")
        result = data.get("result")
        return result if isinstance(result, dict) else None

    # ── EVM path ───────────────────────────────────────────────────────

    async def _poll_evm(self, wallets: list[str], seen_sigs: set[str]) -> list[dict[str, Any]]:
        """Fetch + decode recent asset transfers for EVM wallets via Alchemy.

        Uses ``alchemy_getAssetTransfers`` (erc20/erc721/erc1155) per watched
        wallet, then fetches the receipt for each new tx hash to build the
        enhanced payload. Fails open per wallet.
        """
        events: list[dict[str, Any]] = []
        if not wallets:
            return events

        try:
            from app.alchemy_connector import get_alchemy_connector

            connector = get_alchemy_connector()
        except (ImportError, AttributeError) as exc:
            log.debug("enhanced_tx_alchemy_unavailable err=%s", exc)
            return events

        new_count = 0
        for wallet in wallets:
            if new_count >= MAX_NEW_SIGS_PER_POLL:
                break
            # Alchemy returns transfers newest-first with blockNum + hash.
            for chain, network in EVM_NETWORKS.items():
                if new_count >= MAX_NEW_SIGS_PER_POLL:
                    break
                try:
                    transfers = await connector.get_asset_transfers(
                        from_address=wallet,
                        network=network,
                        category=["erc20", "erc721", "erc1155"],
                        max_count=SIG_LIMIT_PER_ADDRESS,
                    )
                except (ValueError, ConnectionError, RuntimeError) as exc:
                    log.debug("enhanced_tx_alchemy_transfers_failed chain=%s err=%s", chain, exc)
                    continue

                for item in transfers.get("transfers") or []:
                    tx_hash = str(item.get("hash", ""))
                    if not tx_hash or tx_hash in seen_sigs or new_count >= MAX_NEW_SIGS_PER_POLL:
                        continue
                    try:
                        receipt = await connector.get_transaction_receipt(tx_hash, network=network)
                    except (ValueError, ConnectionError, RuntimeError) as exc:
                        log.debug("enhanced_tx_receipt_failed tx=%s err=%s", tx_hash, exc)
                        continue

                    decoded = decode_evm_receipt(receipt, chain=chain)
                    if decoded is None:
                        continue

                    seen_sigs.add(tx_hash)
                    new_count += 1
                    event = self._build_event(decoded, wallet)
                    if event is not None:
                        events.append(event)

        return events

    # ── Shared helpers ─────────────────────────────────────────────────

    def _build_event(self, decoded: dict[str, Any], watch_address: str) -> dict[str, Any] | None:
        """Shape a decoded transaction into the engine's event payload.

        Adds ``token_address`` (first transfer mint or watch address) and
        ``wallet_address`` so the engine's subscription routing + filter
        matching can dispatch by wallet or token.
        """
        if not decoded:
            return None

        token_transfers = decoded.get("tokenTransfers") or []
        token_address = ""
        if token_transfers:
            token_address = str(token_transfers[0].get("mint", "") or "").lower()
        if not token_address:
            token_address = watch_address

        # Only surface meaningful events — skip pure UNKNOWN/no-op txs
        if decoded.get("type") == "UNKNOWN" and not token_transfers and not decoded.get("nativeTransfers"):
            return None

        payload = dict(decoded)
        payload["token_address"] = token_address
        payload["wallet_address"] = watch_address
        return payload

    async def cursor_key(self) -> str:
        return "webhook:cursor:enhanced_transaction"


__all__ = [
    "MAX_NEW_SIGS_PER_POLL",
    "MAX_SEEN_SIGS",
    "PUBLIC_SOLANA_RPC",
    "SIG_LIMIT_PER_ADDRESS",
    "EnhancedTransactionProvider",
]
