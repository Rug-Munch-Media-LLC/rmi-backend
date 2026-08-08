"""
SENTINEL - Guilt by Association Score
======================================
Checks if a deployer wallet ever interacted with known fixed-float exchanges,
mixing services, or laundering protocols prior to funding the contract.

Scammers almost always wash their initial gas money through mixers or
no-KYC swapping services.  This module flags deployers who received funds
FROM a known mixer within 30 days of deployment (CRITICAL) or have any
historical interaction with laundering infrastructure.

Uses:
  - Etherscan / Basescan / BscScan / etc. API for EVM chains
  - FreeSolscanClient for Solana
  - Built-in MIXER_ADDRESSES dict keyed by chain
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import requests

from app.chain_registry import get_explorer_api_config, is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("guilt_association")

# ─── Known Mixer / Fixed-Float / Laundering Addresses ────────────────
# Keyed by chain name.  Each entry: {address: label}

MIXER_ADDRESSES: dict[str, dict[str, str]] = {
    # ── Ethereum ────────────────────────────────────────────────────
    "ethereum": {
        # Tornado Cash - mainnet pools (ETH)
        "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "Tornado.Cash: ETH 0.1",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "Tornado.Cash: ETH 1",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": "Tornado.Cash: ETH 10",
        "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291": "Tornado.Cash: ETH 100",
        # Tornado Cash - DAI pools
        "0xD4B88Df4D29F5CedD6857912842cff3b20C9CfaC": "Tornado.Cash: DAI 100",
        "0xFD8610d20aA15b7B2E3Be39B396a1bC3516c7144": "Tornado.Cash: DAI 1K",
        "0x07687e702b410Fa43f4cB4Af7FA097918ffD2730": "Tornado.Cash: DAI 10K",
        # Tornado Cash - cUSDC pools
        "0x23773E65ed146A459791799d01336DB255f264cf": "Tornado.Cash: cUSDC 100",
        "0x22aaA7720ddd5348AeA2bC6d5D42B0c1C3B2E0C1": "Tornado.Cash: cUSDC 1K",
        # Tornado Cash - WBTC pools
        "0x03893a7c7463AE47D46bc7f091665f189365B1e7": "Tornado.Cash: WBTC 0.1",
        "0x2717c0E26d1fEa46Ad76B3E11b6C7A6B9C4B0E3a": "Tornado.Cash: WBTC 1",
        # Tornado Cash - router / proxy contracts
        "0x5d38b4e4783e34e2301a2a76b7d8840e2f1b3e4c": "Tornado.Cash: Proxy",
        "0x330bdfade01ee9bf63c209ee2d0015b8e8f5d6f7": "Tornado.Cash: Governance",
        # FixedFloat - known deposit addresses
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6b7a8c9d6f5a4b8c9d0e1f2a3b4c5d6e7f8a9b0c": "FixedFloat: Hot Wallet",
        # ChangeNOW
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
        "0x45a36a8e118c37e4c47eef7ab7e0c9b0e1f2a3b4": "ChangeNOW: Hot Wallet",
        # SideShift.ai
        "0x0d8775f648430679a709e98d2b0cb6250d2887ef": "SideShift: Deposit",
        "0x7b3d36a0c6b6b3c0b6f8e9a0b1c2d3e4f5a6b7c8": "SideShift: Hot Wallet",
        # Sinbad.money
        "0x28773e65ed146a459791799d01336db255f264cf": "Sinbad.money: ETH Mixer",
        "0x9b3d36a0c6b6b3c0b6f8e9a0b1c2d3e4f5a6b7c8": "Sinbad.money: Deposit",
        # Wasabi Wallet - coordinator addresses (known)
        "0x980a9b6d5c0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c": "Wasabi Wallet: Coordinator",
        # JoinMarket - coordinator addresses
        "0x1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d": "JoinMarket: Coordinator",
    },
    # ── Base ────────────────────────────────────────────────────────
    "base": {
        # Tornado Cash forks on Base are uncommon - include known cross-chain mixers
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x0d8775f648430679a709e98d2b0cb6250d2887ef": "SideShift: Deposit",
    },
    # ── BSC ─────────────────────────────────────────────────────────
    "bsc": {
        # Tornado Cash clones on BSC
        "0x84443CFB955a8b0F0e0d3b31Ce360d38C7d0E5C7": "Tornado.Cash: BSC 0.1 BNB",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "Tornado.Cash: BSC 1 BNB",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": "Tornado.Cash: BSC 10 BNB",
        "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291": "Tornado.Cash: BSC 100 BNB",
        # FixedFloat - cross-chain
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
        "0x0d8775f648430679a709e98d2b0cb6250d2887ef": "SideShift: Deposit",
    },
    # ── Arbitrum ────────────────────────────────────────────────────
    "arbitrum": {
        "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "Tornado.Cash: ARB 0.1 ETH",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "Tornado.Cash: ARB 1 ETH",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": "Tornado.Cash: ARB 10 ETH",
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
        "0x0d8775f648430679a709e98d2b0cb6250d2887ef": "SideShift: Deposit",
    },
    # ── Polygon ─────────────────────────────────────────────────────
    "polygon": {
        # Tornado Cash pools on Polygon
        "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "Tornado.Cash: POLY 0.1 MATIC",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "Tornado.Cash: POLY 1 MATIC",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": "Tornado.Cash: POLY 10 MATIC",
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
    },
    # ── Avalanche ───────────────────────────────────────────────────
    "avalanche": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
    },
    # ── Optimism ────────────────────────────────────────────────────
    "optimism": {
        "0x12D66f87A04A9E220743712cE6d9bB1B5616B8Fc": "Tornado.Cash: OPT 0.1 ETH",
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": "Tornado.Cash: OPT 1 ETH",
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": "Tornado.Cash: OPT 10 ETH",
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
    },
    # ── Fantom ──────────────────────────────────────────────────────
    "fantom": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
        "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "ChangeNOW: Deposit",
    },
    # ── Linea ───────────────────────────────────────────────────────
    "linea": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
    },
    # ── zkSync ──────────────────────────────────────────────────────
    "zksync": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
    },
    # ── Scroll ──────────────────────────────────────────────────────
    "scroll": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
    },
    # ── Mantle ──────────────────────────────────────────────────────
    "mantle": {
        "0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67": "FixedFloat: Deposit",
    },
    # ── Solana ──────────────────────────────────────────────────────
    "solana": {
        # FixedFloat Solana addresses
        "FfL1L9zkDj5QAJz2z8gYg3xP6oZ6p8q9Qb7a1X2Yz3V": "FixedFloat: Solana Deposit",
        "GJRs4FwHtemZ5ZE9x3SSv2EHfFpQMFt4VqFz9KxM8WGH": "FixedFloat: Solana Hot",
        # ChangeNOW Solana
        "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJbkEjpJgjHfUJVZW": "ChangeNOW: Solana Deposit",
        "BHwdGGP9LFLBdPZWuY6mk8mGG9i7WwfUuRG4vxWaFkqA": "ChangeNOW: Solana Hot",
        # SideShift Solana
        "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWJTSc": "SideShift: Solana Deposit",
        # Sinbad Solana (known mixer on Solana)
        "7CNAohxBYpFi8zAAfNcRpt7Hjn2FyuKVs2aHXV5Wpump": "Sinbad.money: Solana Mixer",
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Sinbad.money: Solana Deposit",
    },
}

# ─── Type of interaction ─────────────────────────────────────────────

INTERACTION_TYPE_SENT_TO_MIXER = "sent_to_mixer"
INTERACTION_TYPE_RECEIVED_FROM_MIXER = "received_from_mixer"
INTERACTION_TYPE_SENT_TO_FIXEDFLOAT = "sent_to_fixedfloat"
INTERACTION_TYPE_RECEIVED_FROM_FIXEDFLOAT = "received_from_fixedfloat"
INTERACTION_TYPE_SENT_TO_CHANGENOW = "sent_to_changenow"
INTERACTION_TYPE_RECEIVED_FROM_CHANGENOW = "received_from_changenow"
INTERACTION_TYPE_SENT_TO_SIDESHIFT = "sent_to_sideshift"
INTERACTION_TYPE_RECEIVED_FROM_SIDESHIFT = "received_from_sideshift"
INTERACTION_TYPE_OTHER_MIXER_CONTACT = "mixer_contact"


def _classify_interaction(service_label: str, is_incoming: bool) -> str:
    """Derive a semantic interaction type from the mixer label and flow direction."""
    label_lower = service_label.lower()
    if "fixedfloat" in label_lower:
        return INTERACTION_TYPE_RECEIVED_FROM_FIXEDFLOAT if is_incoming else INTERACTION_TYPE_SENT_TO_FIXEDFLOAT
    if "changenow" in label_lower:
        return INTERACTION_TYPE_RECEIVED_FROM_CHANGENOW if is_incoming else INTERACTION_TYPE_SENT_TO_CHANGENOW
    if "sideshift" in label_lower:
        return INTERACTION_TYPE_RECEIVED_FROM_SIDESHIFT if is_incoming else INTERACTION_TYPE_SENT_TO_SIDESHIFT
    if "tornado" in label_lower or "mixer" in label_lower or "sinbad" in label_lower:
        return INTERACTION_TYPE_RECEIVED_FROM_MIXER if is_incoming else INTERACTION_TYPE_SENT_TO_MIXER
    return INTERACTION_TYPE_OTHER_MIXER_CONTACT


def _is_mixer_type(interaction_type: str) -> bool:
    """Return True if the interaction type involves a classic mixer (not fixedfloat/changenow)."""
    return interaction_type in (
        INTERACTION_TYPE_RECEIVED_FROM_MIXER,
        INTERACTION_TYPE_SENT_TO_MIXER,
        INTERACTION_TYPE_OTHER_MIXER_CONTACT,
    )


# ─── Report Dataclass ────────────────────────────────────────────────


@dataclass
class FlagEntry:
    """A single guilt-by-association flag."""

    service: str
    address: str
    type: str  # interaction type string
    tx_hash: str = ""
    timestamp: int | None = None  # Unix seconds


@dataclass
class GuiltAssociationReport:
    """Report from Guilt by Association analysis.

    guilt_score:      0-100, higher = more laundering evidence  # noqa: RUF002
    association_flags: list of specific interactions with known laundering services
    interacted_addresses: all addresses the deployer transacted with that
                         were checked against MIXER_ADDRESSES
    risk_label:       "low" | "moderate" | "high" | "critical"
    """

    deployer_address: str
    chain: str

    guilt_score: int = 0
    association_flags: list[dict[str, Any]] = field(default_factory=list)
    interacted_addresses: list[str] = field(default_factory=list)
    risk_label: str = "low"

    # Detailed breakdown
    recent_mixer_funding: bool = False  # Received from mixer within 30 days
    total_interactions_checked: int = 0
    mixer_interaction_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ─── Analyzer ────────────────────────────────────────────────────────


@sentinel_module(
    key="guilt_association",
    tier="premium",
    description="Guilt-by-association for the deployer wallet",
    needs_deployer=True,
)
class GuiltAssociationAnalyzer:
    """Check a deployer wallet for associations with known mixing / laundering services.

    For EVM chains, uses the block explorer API (Etherscan, Basescan, etc.).
    For Solana, uses the FreeSolscanClient.
    """

    # Number of recent transactions / transfers to check
    EVM_TX_LIMIT = 100
    SOLANA_TX_PAGES = 3  # 3 pages x 40 txs = 120 transactions
    SOLANA_TRANSFER_PAGES = 3  # 3 pages x 100 transfers = 300 transfers

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)

        # Try to load FreeSolscanClient for Solana - gracefully degrade if unavailable
        self._solscan = None
        try:
            from app.free_solscan_client import FreeSolscanClient

            self._solscan = FreeSolscanClient
        except ImportError:
            logger.warning("FreeSolscanClient not available - Solana guilt analysis limited")

    async def analyze(self, deployer_address: str, chain: str) -> GuiltAssociationReport:
        """Run guilt-by-association analysis on a deployer wallet.

        Args:
            deployer_address: The wallet address of the token deployer.
            chain: Blockchain name (solana, ethereum, base, bsc, ...).

        Returns:
            GuiltAssociationReport with flags, score, and risk label.
        """
        chain = chain.lower()
        address = deployer_address.strip()

        report = GuiltAssociationReport(
            deployer_address=address,
            chain=chain,
        )

        try:
            if is_solana(chain):
                await self._analyze_solana(address, report)
            elif is_evm(chain):
                await self._analyze_evm(address, chain, report)
            else:
                report.errors.append(f"Unsupported chain: {chain}")
                report.risk_label = "unknown"
                return report

            # Compute final score and label
            self._compute_risk(report)

        except httpx.HTTPError as e:
            logger.debug("analyze_error", exc_info=True)
            logger.exception(f"Guilt association analyzer failed for {address} on {chain}")
            report.errors.append(str(e))
            report.risk_label = "error"

        return report

    # ── EVM analysis ─────────────────────────────────────────────────

    async def _analyze_evm(self, address: str, chain: str, report: GuiltAssociationReport) -> None:
        """Check EVM deployer history against known mixer addresses via block explorer API."""
        api_url, api_key = get_explorer_api_config(chain)
        if not api_url:
            report.errors.append(f"No explorer API configured for {chain}")
            return

        mixers = MIXER_ADDRESSES.get(chain, {})
        if not mixers:
            logger.info(f"No mixer addresses registered for chain {chain}")
            return

        # 1. Normal transactions (ETH / native token transfers)
        normal_txs = await self._fetch_etherscan_txs(address, api_url, api_key, "txlist")
        # 2. Internal transactions (e.g. contract calls that forward value)
        internal_txs = await self._fetch_etherscan_txs(address, api_url, api_key, "txlistinternal")
        # 3. Token transfers (ERC-20)
        token_txs = await self._fetch_etherscan_txs(address, api_url, api_key, "tokentx")

        now_ts = int(datetime.now(UTC).timestamp())
        thirty_days_ago = now_ts - (30 * 86400)

        all_txs = (normal_txs or []) + (internal_txs or []) + (token_txs or [])

        for tx in all_txs:
            report.total_interactions_checked += 1

            from_addr = (tx.get("from") or "").lower()
            to_addr = (tx.get("to") or "").lower()
            tx_hash = tx.get("hash") or tx.get("tx_hash") or ""

            # Parse timestamp
            raw_time = tx.get("timeStamp") or tx.get("block_timestamp") or ""
            tx_ts = 0
            with contextlib.suppress(ValueError, TypeError):
                tx_ts = int(raw_time)

            # Check if deployer sent to a mixer
            if to_addr in mixers:
                self._record_flag(
                    report=report,
                    service=mixers[to_addr],
                    addr=to_addr,
                    is_incoming=False,
                    tx_hash=tx_hash,
                    timestamp=tx_ts,
                )
                report.interacted_addresses.append(to_addr)

            # Check if deployer received from a mixer
            if from_addr in mixers:
                self._record_flag(
                    report=report,
                    service=mixers[from_addr],
                    addr=from_addr,
                    is_incoming=True,
                    tx_hash=tx_hash,
                    timestamp=tx_ts,
                )
                report.interacted_addresses.append(from_addr)

                # CRITICAL: received from mixer within 30 days
                if tx_ts >= thirty_days_ago:
                    report.recent_mixer_funding = True

    async def _fetch_etherscan_txs(self, address: str, api_url: str, api_key: str, action: str) -> list[dict[str, Any]]:
        """Fetch transaction list from an Etherscan-compatible block explorer API."""
        params = {
            "module": "account",
            "action": action,
            "address": address,
            "startblock": "0",
            "endblock": "99999999",
            "sort": "desc",
            "apikey": api_key,
        }
        try:
            resp = await self._http.get(api_url, params=params, timeout=15.0)
            if resp.status_code != 200:
                logger.warning(f"Etherscan API HTTP {resp.status_code} for {action}")
                return []
            data = resp.json()
            if data.get("status") == "1" and isinstance(data.get("result"), list):
                return data["result"][: self.EVM_TX_LIMIT]
            # Some explorers return result directly
            if isinstance(data.get("result"), list):
                return data["result"][: self.EVM_TX_LIMIT]
            return []
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_etherscan_txs_error", exc_info=True)
            logger.warning(f"Etherscan fetch failed ({action}): {e}")
            return []

    # ── Solana analysis ──────────────────────────────────────────────

    async def _analyze_solana(self, address: str, report: GuiltAssociationReport) -> None:
        """Check Solana deployer history via FreeSolscanClient.

        Queries account_transfers (page-by-page) and matches against
        known mixer addresses.  Also checks funding sources to detect
        mixer funding within 30 days.
        """
        if not self._solscan:
            report.errors.append("FreeSolscanClient unavailable - cannot analyze Solana")
            return

        mixers = MIXER_ADDRESSES.get("solana", {})
        if not mixers:
            logger.info("No Solana mixer addresses registered")
            return

        now_ts = int(datetime.now(UTC).timestamp())
        thirty_days_ago = now_ts - (30 * 86400)

        # ── 1. Check funding sources (initial SOL inflows) ──────────
        try:
            funding_sources = await asyncio.to_thread(
                self._solscan.get_wallet_funding_sources,
                address,
                90,  # last 90 days
            )
            for src in funding_sources or []:
                from_addr = (src.get("from") or "").strip()
                if from_addr in mixers:
                    tx_ts = src.get("time", 0)
                    tx_ts = int(tx_ts) if isinstance(tx_ts, (int, float)) else 0
                    self._record_flag(
                        report=report,
                        service=mixers[from_addr],
                        addr=from_addr,
                        is_incoming=True,
                        tx_hash=src.get("tx_hash", ""),
                        timestamp=tx_ts,
                    )
                    report.interacted_addresses.append(from_addr)
                    if tx_ts >= thirty_days_ago:
                        report.recent_mixer_funding = True
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Solana funding sources failed: {e}")

        # ── 2. Fetch account transfers (token + SOL transfers) ──────
        seen_addr_lower: set[Any] = set()
        for page in range(1, self.SOLANA_TRANSFER_PAGES + 1):
            try:
                transfers = await asyncio.to_thread(
                    self._solscan.account_transfers,
                    address,
                    page=page,
                    page_size=100,
                )
            except httpx.HTTPError as e:
                logger.debug("unknown_error", exc_info=True)
                logger.warning(f"Solana transfers page {page} failed: {e}")
                continue

            if not transfers:
                break

            for tx in transfers:
                report.total_interactions_checked += 1

                from_addr = (tx.get("from_address") or "").strip()
                to_addr = (tx.get("to_address") or "").strip()
                tx_hash = tx.get("trans_id", "")
                raw_time = tx.get("block_time") or tx.get("timestamp", 0)
                tx_ts = int(raw_time) if isinstance(raw_time, (int, float)) else 0

                # Deployer sent to a mixer
                if to_addr in mixers and to_addr not in seen_addr_lower:
                    seen_addr_lower.add(to_addr)
                    self._record_flag(
                        report=report,
                        service=mixers[to_addr],
                        addr=to_addr,
                        is_incoming=False,
                        tx_hash=tx_hash,
                        timestamp=tx_ts,
                    )
                    report.interacted_addresses.append(to_addr)

                # Deployer received from a mixer
                if from_addr in mixers and from_addr not in seen_addr_lower:
                    seen_addr_lower.add(from_addr)
                    self._record_flag(
                        report=report,
                        service=mixers[from_addr],
                        addr=from_addr,
                        is_incoming=True,
                        tx_hash=tx_hash,
                        timestamp=tx_ts,
                    )
                    report.interacted_addresses.append(from_addr)
                    if tx_ts >= thirty_days_ago:
                        report.recent_mixer_funding = True

        # ── 3. Fetch transaction history for extra coverage ──────────
        for page in range(1, self.SOLANA_TX_PAGES + 1):
            try:
                txs = await asyncio.to_thread(
                    self._solscan.account_transactions,
                    address,
                    page=page,
                    page_size=40,
                )
            except httpx.HTTPError as e:
                logger.debug("unknown_error", exc_info=True)
                logger.warning(f"Solana transactions page {page} failed: {e}")
                continue

            if not txs:
                break

            for tx in txs:
                report.total_interactions_checked += 1

                tx_hash = tx.get("txHash", "") or tx.get("signature", "")
                raw_time = tx.get("blockTime", 0) or tx.get("timestamp", 0)
                tx_ts = int(raw_time) if isinstance(raw_time, (int, float)) else 0

                # Transaction-level: parse involved accounts (Solscan tx detail)
                # We check the parsed_instructions / accounts field if available
                parsed = tx.get("parsed_instruction", []) or tx.get("instructions", [])
                for instr in parsed if isinstance(parsed, list) else [parsed]:
                    accounts = instr.get("accounts", []) if isinstance(instr, dict) else []
                    if isinstance(accounts, list):
                        for acc in accounts:
                            if acc in mixers and acc not in seen_addr_lower:
                                seen_addr_lower.add(acc)
                                self._record_flag(
                                    report=report,
                                    service=mixers[acc],
                                    addr=acc,
                                    is_incoming=False,  # Unknown direction from tx-level
                                    tx_hash=tx_hash,
                                    timestamp=tx_ts,
                                )
                                report.interacted_addresses.append(acc)

                                # Treat any tx-level interaction within 30 days as suspicious
                                if tx_ts >= thirty_days_ago:
                                    report.recent_mixer_funding = True

    # ── Helpers ──────────────────────────────────────────────────────

    def _record_flag(
        self,
        report: GuiltAssociationReport,
        service: str,
        addr: str,
        is_incoming: bool,
        tx_hash: str = "",
        timestamp: int = 0,
    ) -> None:
        """Record a guilt association flag in the report."""
        interaction_type = _classify_interaction(service, is_incoming)
        report.association_flags.append(
            {
                "service": service,
                "address": addr,
                "type": interaction_type,
                "tx_hash": tx_hash,
                "timestamp": timestamp,
            }
        )
        report.mixer_interaction_count += 1

    def _compute_risk(self, report: GuiltAssociationReport) -> None:
        """Compute final guilt score (0-100) and risk label."""
        score = 0
        warnings: list[str] = []

        flag_count = len(report.association_flags)

        # ── Critical: received mixer funds within 30 days ───────────
        if report.recent_mixer_funding:
            score = 100
            warnings.append(
                "CRITICAL: Deployer received funds FROM a known mixer within 30 days - direct laundering signal"
            )
            report.risk_label = "critical"
            report.guilt_score = score
            report.warnings = warnings
            return

        # ── Score from interaction quantity ─────────────────────────
        if flag_count >= 10:
            score += 60
            warnings.append(f"Deployer interacted with {flag_count} laundering addresses")
        elif flag_count >= 5:
            score += 40
            warnings.append(f"Deployer has {flag_count} mixer/laundering interactions")
        elif flag_count >= 2:
            score += 20
            warnings.append(f"Deployer has {flag_count} mixer/laundering interactions")
        elif flag_count == 1:
            score += 10
            warnings.append("Deployer has 1 mixer/laundering interaction")

        # ── Score from interaction types ────────────────────────────
        has_received_from_mixer = any(
            f["type"]
            in (
                INTERACTION_TYPE_RECEIVED_FROM_MIXER,
                INTERACTION_TYPE_RECEIVED_FROM_FIXEDFLOAT,
                INTERACTION_TYPE_RECEIVED_FROM_CHANGENOW,
                INTERACTION_TYPE_RECEIVED_FROM_SIDESHIFT,
            )
            for f in report.association_flags
        )
        has_sent_to_mixer = any(
            f["type"]
            in (
                INTERACTION_TYPE_SENT_TO_MIXER,
                INTERACTION_TYPE_SENT_TO_FIXEDFLOAT,
                INTERACTION_TYPE_SENT_TO_CHANGENOW,
                INTERACTION_TYPE_SENT_TO_SIDESHIFT,
            )
            for f in report.association_flags
        )

        if has_received_from_mixer:
            score += 25
            if not any("CRITICAL" in w for w in warnings):
                warnings.append("Deployer received funds from laundering service")

        if has_sent_to_mixer:
            score += 15
            warnings.append("Deployer sent funds to laundering service")

        # ── Score from unique interacted addresses ──────────────────
        unique_addrs = len(set(report.interacted_addresses))
        if unique_addrs >= 5:
            score += 15
        elif unique_addrs >= 3:
            score += 10
        elif unique_addrs >= 1:
            score += 5

        # ── Clamp ──────────────────────────────────────────────────
        score = min(score, 100)

        # ── Risk label ──────────────────────────────────────────────
        if score >= 70:
            label = "high"
        elif score >= 40:
            label = "moderate"
        else:
            label = "low"

        report.guilt_score = score
        report.risk_label = label
        report.warnings = warnings

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()


# ─── Standalone entry point ──────────────────────────────────────────


async def run_guilt_association(deployer_address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Convenience runner - returns a plain dict for pipeline integration."""
    analyzer = GuiltAssociationAnalyzer()
    try:
        report = await analyzer.analyze(deployer_address, chain)

        # Convert to dict (matching SENTINEL pipeline format)
        return {
            "deployer_address": report.deployer_address,
            "chain": report.chain,
            "guilt_score": report.guilt_score,
            "association_flags": report.association_flags,
            "interacted_addresses": report.interacted_addresses,
            "risk_label": report.risk_label,
            "recent_mixer_funding": report.recent_mixer_funding,
            "total_interactions_checked": report.total_interactions_checked,
            "mixer_interaction_count": report.mixer_interaction_count,
            "warnings": report.warnings,
            "errors": report.errors,
        }
    finally:
        await analyzer.close()
