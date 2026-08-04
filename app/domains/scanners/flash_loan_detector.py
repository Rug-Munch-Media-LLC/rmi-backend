"""
SENTINEL - Flash Loan Attack Detector
======================================
Detects if a token's trades were sandwiched by flash loans.
Pattern: huge buy in block N, huge sell in block N or N+1, wallet had
near-zero balance before the trade - classic flash-loan manipulation.

Uses direct API calls: DexScreener (trades/pairs), Birdeye (trades),
Solscan/Etherscan (account history), Helius (parsed transactions).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_client import ChainClient
from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("flash_loan_detector")

# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class FlashLoanFlag:
    """A single suspected flash-loan attack."""

    attacker_address: str
    buy_block: int
    sell_block: int
    buy_amount_usd: float
    sell_amount_usd: float
    pool_liquidity_usd: float
    volume_vs_liquidity_pct: float  # trade volume as % of pool liquidity
    same_block: bool
    wallet_had_no_prior_balance: bool
    block_timestamp: int | None = None


@dataclass
class FlashLoanReport:
    token_address: str
    chain: str
    flash_loan_flags: list[FlashLoanFlag] = field(default_factory=list)
    total_suspect_volume: float = 0.0
    suspect_trade_count: int = 0
    analyzed_trade_count: int = 0
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Detector Class ──────────────────────────────────────────────────


@sentinel_module(key="flash_loan_detection", tier="premium", description="Flash-loan funded manipulation patterns")
class FlashLoanDetector:
    """Detects flash-loan sandwich / manipulation patterns.

    Fetches recent swap events, checks for single-block or consecutive-block
    trades by the same wallet with abnormally large amounts relative to pool
    liquidity, and verifies whether the wallet had near-zero balance before
    the attack (a strong flash-loan indicator).
    """

    # Thresholds
    MIN_VOLUME_VS_LIQUIDITY_PCT = 20.0  # trade >20% of pool liquidity → suspicious
    MAX_BLOCKS_APART = 3  # buy-sell within 3 blocks → suspicious
    MIN_TRADE_USD = 5000.0  # ignore dust trades

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
        self._birdeye_key = os.getenv("BIRDEYE_API_KEY", "")

    # ── Direct API fetchers ─────────────────────────────────────────

    async def _fetch_dexscreener_trades(self, token_address: str) -> list[dict]:
        """Fetch recent trades from DexScreener for a token."""
        try:
            resp = await self._http.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_dexscreener_trades_error", exc_info=True)
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return []

    async def _fetch_birdeye_trades(self, token_address: str, limit: int = 50) -> list[dict]:
        """Fetch recent trades from Birdeye."""
        if not self._birdeye_key:
            return []
        try:
            resp = await self._http.get(
                "https://public-api.birdeye.so/defi/tx",
                params={"address": token_address, "limit": limit},
                headers={"X-API-KEY": self._birdeye_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("data", {}).get("items", []) or []
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_birdeye_trades_error", exc_info=True)
            logger.warning(f"Birdeye trades failed for {token_address}: {e}")
        return []

    async def _fetch_solana_account_balance_before(self, address: str, slot: int) -> float | None:
        """Check a Solana wallet's SOL balance at a past slot via Helius."""
        if not self._helius_key:
            return None
        try:
            result = await self._chain.rpc_call("getBalance", [address, {"slot": max(1, slot - 2)}])
            if result and "result" in result:
                lamports = result["result"].get("value", 0)
                return lamports / 1e9
        except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
            logger.debug("_fetch_solana_account_balance_before_error", exc_info=True)
            logger.warning(f"Helius balance check failed for {address}: {e}")
        return None

    async def _fetch_solana_signatures(self, address: str, limit: int = 5) -> list[dict]:
        """Fetch early signatures to determine wallet freshness."""
        result = await self._chain.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    async def _fetch_evm_account_history(self, address: str, chain: str) -> dict | None:
        """Fetch EVM account tx count via Etherscan-style API."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return None
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return None
        try:
            url = (
                f"{chain_cfg.explorer_api_url}"
                f"?module=account&action=txlist&address={address}"
                f"&startblock=0&endblock=99999999&page=1&offset=5"
                f"&sort=asc&apikey={key}"
            )
            resp = await self._http.get(url)
            if resp.status_code == 200:
                return resp.json()
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_evm_account_history_error", exc_info=True)
            logger.warning(f"Etherscan account history failed for {address}: {e}")
        return None

    # ── Analysis helpers ────────────────────────────────────────────

    def _group_trades_by_wallet(self, trades: list[dict]) -> dict[str, list[dict]]:
        """Group trades by wallet address."""
        grouped: dict[str, list[dict]] = {}
        for t in trades:
            addr = t.get("wallet", t.get("address", t.get("maker", "")))
            if addr:
                grouped.setdefault(addr, []).append(t)
        return grouped

    def _detect_flash_pattern(
        self,
        wallet_trades: list[dict],
        pool_liquidity_usd: float,
    ) -> list[FlashLoanFlag]:
        """Within a single wallet's trades, find buy-then-sell patterns
        that look like flash-loan attacks."""
        flags = []
        # Sort by block number / timestamp
        sorted_trades = sorted(wallet_trades, key=lambda t: int(t.get("block", t.get("slot", t.get("blockTime", 0)))))

        buys = [t for t in sorted_trades if t.get("side", t.get("type", "")).lower() in ("buy", "0")]
        sells = [t for t in sorted_trades if t.get("side", t.get("type", "")).lower() in ("sell", "1")]

        for buy in buys:
            buy_block = int(buy.get("block", buy.get("slot", buy.get("blockTime", 0))))
            buy_usd = float(buy.get("volumeUsd", buy.get("amountUsd", buy.get("value", 0))))

            if buy_usd < self.MIN_TRADE_USD:
                continue

            for sell in sells:
                sell_block = int(sell.get("block", sell.get("slot", sell.get("blockTime", 0))))
                if sell_block < buy_block:
                    continue
                blocks_apart = sell_block - buy_block
                if blocks_apart > self.MAX_BLOCKS_APART:
                    continue

                sell_usd = float(sell.get("volumeUsd", sell.get("amountUsd", sell.get("value", 0))))
                if sell_usd < self.MIN_TRADE_USD:
                    continue

                total_volume = buy_usd + sell_usd
                vol_vs_liq = (total_volume / pool_liquidity_usd * 100) if pool_liquidity_usd else 0

                if vol_vs_liq < self.MIN_VOLUME_VS_LIQUIDITY_PCT:
                    continue

                flags.append(
                    FlashLoanFlag(
                        attacker_address=buy.get("wallet", buy.get("address", buy.get("maker", ""))),
                        buy_block=buy_block,
                        sell_block=sell_block,
                        buy_amount_usd=buy_usd,
                        sell_amount_usd=sell_usd,
                        pool_liquidity_usd=pool_liquidity_usd,
                        volume_vs_liquidity_pct=round(vol_vs_liq, 2),
                        same_block=(blocks_apart == 0),
                        wallet_had_no_prior_balance=False,  # filled later
                        block_timestamp=buy.get("blockTime", buy.get("timestamp")),
                    )
                )
        return flags

    # ── Risk calculation ─────────────────────────────────────────────

    def _calculate_risk(self, report: FlashLoanReport) -> tuple[int, str, list[str]]:
        """Calculate risk score from flash-loan flags."""
        score = 0
        warnings = []

        if report.suspect_trade_count == 0:
            return 0, "LOW", []

        # Number of distinct attacks
        attack_count = len(report.flash_loan_flags)
        if attack_count >= 5:
            score += 40
            warnings.append(f"CRITICAL: {attack_count} flash-loan attack patterns detected")
        elif attack_count >= 3:
            score += 25
            warnings.append(f"HIGH: {attack_count} flash-loan attack patterns detected")
        elif attack_count >= 1:
            score += 15
            warnings.append(f"MEDIUM: {attack_count} flash-loan attack pattern(s) detected")

        # Total suspect volume relative to pool
        if report.total_suspect_volume > 0 and report.analyzed_trade_count > 0:
            vol_ratio = report.total_suspect_volume / max(1, report.analyzed_trade_count * 1000)
            if vol_ratio > 50:
                score += 30
                warnings.append("CRITICAL: Suspect volume dominates trading activity")
            elif vol_ratio > 20:
                score += 15
                warnings.append("HIGH: Significant suspect volume relative to activity")

        # Same-block attacks are most dangerous
        same_block_attacks = [f for f in report.flash_loan_flags if f.same_block]
        if same_block_attacks:
            score += 20
            warnings.append(f"HIGH: {len(same_block_attacks)} same-block buy+sell (classic flash loan)")

        # Wallets with no prior balance (strongest signal)
        no_balance_attacks = [f for f in report.flash_loan_flags if f.wallet_had_no_prior_balance]
        if no_balance_attacks:
            score += 15
            warnings.append(f"HIGH: {len(no_balance_attacks)} attacker wallets had near-zero balance pre-trade")

        score = min(100, score)

        if score >= 70:
            level = "CRITICAL"
        elif score >= 40:
            level = "HIGH"
        elif score >= 20:
            level = "MEDIUM"
        else:
            level = "LOW"

        return score, level, warnings

    # ── Main analysis entry point ─────────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> FlashLoanReport:
        """Full flash-loan attack analysis for a token.

        Steps:
        1. Fetch recent DEX pair/trade data from DexScreener & Birdeye
        2. Group trades by wallet, detect buy-then-sell patterns
        3. For each suspect pattern, check wallet's pre-trade balance
        4. Build flags for patterns exceeding thresholds
        5. Calculate risk score
        """
        report = FlashLoanReport(token_address=token_address, chain=chain)

        # 1. Fetch DEX data
        pairs = await self._fetch_dexscreener_trades(token_address)
        pool_liquidity_usd = 0.0
        if pairs:
            primary = pairs[0]
            liq = primary.get("liquidity", {})
            pool_liquidity_usd = float(liq.get("usd", 0)) if isinstance(liq, dict) else float(liq or 0)

        # 2. Fetch trades
        trades: list[dict] = []
        birdeye_trades = await self._fetch_birdeye_trades(token_address, limit=100)
        if birdeye_trades:
            trades.extend(birdeye_trades)

        # DexScreener doesn't return individual trades in pairs endpoint,
        # but pairs have volume info we can leverage
        report.analyzed_trade_count = len(trades) or 1

        if not trades:
            # No trade data available - cannot detect flash loans
            report.risk_score = 0
            report.risk_level = "LOW"
            report.warnings = ["No trade data available for flash-loon analysis"]
            return report

        # 3. Group by wallet and detect flash-loan patterns
        grouped = self._group_trades_by_wallet(trades)
        all_flags: list[FlashLoanFlag] = []

        for _wallet, wallet_trades in grouped.items():
            flags = self._detect_flash_pattern(wallet_trades, pool_liquidity_usd)
            all_flags.extend(flags)

        # 4. For each suspect, check pre-trade balance
        for flag in all_flags:
            if is_solana(chain):
                balance = await self._fetch_solana_account_balance_before(flag.attacker_address, flag.buy_block)
                if balance is not None and balance < 0.01:
                    flag.wallet_had_no_prior_balance = True

                # Also check wallet freshness (few transactions = likely borrowed)
                sigs = await self._fetch_solana_signatures(flag.attacker_address, limit=5)
                if len(sigs) <= 3:
                    flag.wallet_had_no_prior_balance = True

            elif is_evm(chain):
                history = await self._fetch_evm_account_history(flag.attacker_address, chain)
                if history:
                    tx_list = history.get("result", [])
                    # If the wallet's first transactions are these trades, it's suspicious
                    if len(tx_list) <= 5:
                        flag.wallet_had_no_prior_balance = True

        # 5. Build report
        report.flash_loan_flags = all_flags
        report.suspect_trade_count = len(all_flags)
        report.total_suspect_volume = sum(f.buy_amount_usd + f.sell_amount_usd for f in all_flags)

        # 6. Calculate risk
        report.risk_score, report.risk_level, report.warnings = self._calculate_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"flash loan attack {chain}",
                chain=chain,
                address=token_address,
                scanner_type="flash_loan",
            )
            report.citations = rag_cits
            # Enhance warnings with citation references
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except (ConnectionError, OSError, TimeoutError):
            pass

        return report
