#!/usr/bin/env python3
"""
Free Solscan API Client - Reverse-Engineered
Bypasses Cloudflare + $200/mo paywall
Based on: github.com/paoloanzn/free-solscan-api
Extended with RMI security intelligence
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from http import HTTPStatus
from typing import Any

import cloudscraper

from app.config import settings
from app.core.circuit_breaker import CircuitBreakerOpenError, SimpleCircuitBreaker
from app.core.logging import get_logger

logger = get_logger(__name__)

# Default request configuration
REQUEST_TIMEOUT = 15  # Seconds
DEFAULT_RETRIES = 3  # Number of retry attempts
RETRY_BASE_DELAY = 2  # Base seconds for exponential backoff
RETRY_ERROR_DELAY = 1  # Seconds between retries on errors
PAGE_SIZE_DEFAULT = 40  # Default page size for paginated endpoints
PAGE_SIZE_TRANSFERS = 100  # Page size for transfer endpoints
PAGE_SIZE_HOLDERS = 200  # Page size for holder endpoints
PAGE_SIZE_TOKENS = 500  # Page size for token listing endpoints
BALANCE_DECIMALS = 1e9  # Solana lamports divisor

# Circuit breaker for Solscan API (sync — reverse-engineered, unreliable API)
_solscan_cb = SimpleCircuitBreaker(
    name="solscan",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ─── Token Generator ──────────────────────────────────────────────────────────

def generate_solauth_token() -> str:
    """Generate sol-aut token for Solscan API authentication."""
    timestamp = str(int(time.time()))
    data = f"solscan_{timestamp}"
    signature = hashlib.sha256(data.encode()).hexdigest()
    return base64.b64encode(f"{timestamp}:{signature}".encode()).decode()

# ─── Scrapers (separate instances for parallelism) ────────────────────────────

_scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "desktop": True}
)

BASE_URL = settings.solscan_url

def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "sol-aut": generate_solauth_token(),
        "Referer": "https://solscan.io/",
        "Origin": "https://solscan.io",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

# ─── Core Request Handler ─────────────────────────────────────────────────────

def _request(endpoint: str, params: dict | None = None, retries: int = DEFAULT_RETRIES) -> Any:
    """Make authenticated request to Solscan API with retries and circuit breaker."""
    # Circuit breaker check — serve stale cache if OPEN
    if not _solscan_cb.can_call():
        stale = _solscan_cb.get_stale_result()
        if stale is not None:
            return stale
        raise CircuitBreakerOpenError(
            _solscan_cb.name,
            _solscan_cb.state,
            "Solscan circuit OPEN — failing fast",
        )

    url = f"{BASE_URL}{endpoint}"
    for attempt in range(retries):
        try:
            response = _scraper.get(url, headers=_headers(), params=params or {}, timeout=REQUEST_TIMEOUT)
            if response.status_code == HTTPStatus.OK:
                data = response.json()
                result = data.get("data") if isinstance(data, dict) and "data" in data else data
                _solscan_cb.set_stale_result(result)
                _solscan_cb.record_success()
                return result
            elif response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                wait = RETRY_BASE_DELAY**attempt
                logger.warning("Rate limited, waiting %ds...", wait)
                _solscan_cb.record_failure(is_server_error=False)
                time.sleep(wait)
            elif response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                logger.warning("HTTP %s on %s", response.status_code, endpoint)
                _solscan_cb.record_failure(is_server_error=True)
                if attempt == retries - 1:
                    return None
            else:
                logger.warning("HTTP %s on %s", response.status_code, endpoint)
                _solscan_cb.record_failure(is_server_error=False)
                if attempt == retries - 1:
                    return None
        except Exception as e:
            logger.error("Request failed (%s/%s): %s", attempt + 1, retries, e)
            _solscan_cb.record_failure(is_server_error=True)
            if attempt == retries - 1:
                return None
            time.sleep(RETRY_ERROR_DELAY)
    return None

# ─── API Endpoints ────────────────────────────────────────────────────────────

class FreeSolscanClient:
    """Reverse-engineered Solscan API client with all security endpoints."""

    # ── Account / Wallet ─────────────────────────────────────────────────────

    @staticmethod
    def account_info(address: str) -> dict | None:
        """Get account info (balance, type, owner)."""
        return _request("/account", {"address": address})

    @staticmethod
    def account_transactions(address: str, page: int = 1, page_size: int = 40) -> list | None:
        """Get transaction history for an address."""
        return _request(
            "/account/transaction", {"address": address, "page": page, "page_size": page_size}
        )

    @staticmethod
    def account_transfers(
        address: str,
        page: int = 1,
        page_size: int = 100,
        remove_spam: bool = True,
        exclude_zero: bool = True,
        activity_type: str | None = None,
        flow: str | None = None,
    ) -> list | None:
        """Get token transfers for an address."""
        params = {
            "address": address,
            "page": page,
            "page_size": page_size,
            "remove_spam": str(remove_spam).lower(),
            "exclude_amount_zero": str(exclude_zero).lower(),
        }
        if activity_type:
            params["activity_type"] = activity_type
        if flow:
            params["flow"] = flow
        return _request("/account/transfer", params)

    @staticmethod
    def account_defi_activities(
        address: str, page: int = 1, page_size: int = 100, activity_type: str | None = None
    ) -> list | None:
        """Get DeFi activities (swaps, liquidity, etc)."""
        params = {
            "address": address,
            "page": page,
            "page_size": page_size,
        }
        if activity_type:
            params["activity_type"] = activity_type
        return _request("/account/defi/activities", params)

    @staticmethod
    def account_balance_history(address: str) -> list | None:
        """Get SOL balance history over time."""
        return _request("/account/balance_change", {"address": address})

    @staticmethod
    def account_portfolio(
        address: str, page: int = 1, page_size: int = 100, hide_zero: bool = True
    ) -> dict | None:
        """Get token portfolio for an address."""
        return _request(
            "/account/tokenaccounts",
            {
                "address": address,
                "page": page,
                "page_size": page_size,
                "type": "token",
                "hide_zero": str(hide_zero).lower(),
            },
        )

    @staticmethod
    def account_token_accounts(address: str, token: str) -> list | None:
        """Get specific token accounts owned by address."""
        return _request(
            "/account/tokenaccounts",
            {
                "address": address,
                "tokenAddress": token,
                "type": "token",
            },
        )

    # ── Token Data ───────────────────────────────────────────────────────────

    @staticmethod
    def token_data(
        token_address: str = "So11111111111111111111111111111111111111112",
    ) -> dict | None:
        """Get token metadata (name, symbol, supply, decimals, etc)."""
        return _request("/token", {"address": token_address})

    @staticmethod
    def token_holders(token_address: str, page: int = 1, page_size: int = 100) -> list | None:
        """Get top token holders."""
        return _request(
            "/token/holder",
            {
                "tokenAddress": token_address,
                "page": page,
                "page_size": page_size,
            },
        )

    @staticmethod
    def token_holders_total(token_address: str) -> int | None:
        """Get total number of unique holders."""
        result = _request("/token/holder/total", {"tokenAddress": token_address})
        if isinstance(result, dict):
            return result.get("result")
        return result

    @staticmethod
    def token_transfer_summary(token_address: str) -> dict | None:
        """Get transfer summary (volume, count, etc)."""
        return _request("/token/transfer/summary", {"tokenAddress": token_address})

    @staticmethod
    def token_defi_activities(
        token_address: str, page: int = 1, page_size: int = 100, activity_type: str | None = None
    ) -> list | None:
        """Get DeFi activities for a token (swaps, liquidity changes)."""
        params = {
            "token": token_address,
            "page": page,
            "page_size": page_size,
        }
        if activity_type:
            params["activity_type"] = activity_type
        return _request("/token/defi/activities", params)

    @staticmethod
    def token_markets(token_address: str, page: int = 1, page_size: int = 100) -> list | None:
        """Get DEX markets for a token."""
        return _request(
            "/token/markets",
            {
                "token": token_address,
                "page": page,
                "page_size": page_size,
            },
        )

    # ── Transaction Details ──────────────────────────────────────────────────

    @staticmethod
    def transaction_detail(tx_hash: str) -> dict | None:
        """Get full transaction details including inner instructions."""
        return _request("/transaction/detail", {"tx": tx_hash})

    # ── Top Transfers (Critical for Bundler Detection) ──────────────────────

    @staticmethod
    def top_address_transfers(address: str, range_days: int = 7) -> list | None:
        """Get top transfers to/from an address - KEY for gas funding traces."""
        return _request(
            "/account/top-transfer",
            {
                "address": address,
                "range_day": range_days,
            },
        )

    # ── Batch Operations ─────────────────────────────────────────────────────

    @staticmethod
    def batch_account_info(addresses: list[str]) -> dict[str, Any]:
        """Get info for multiple addresses (for holder analysis)."""
        results = {}
        for addr in addresses:
            results[addr] = FreeSolscanClient.account_info(addr)
            time.sleep(0.2)  # Rate limit respect
        return results

    @staticmethod
    def get_holder_wallets(token_address: str, top_n: int = 50) -> list[dict]:
        """Get top N holders with their balances."""
        holders = []
        page = 1
        while len(holders) < top_n:
            batch = FreeSolscanClient.token_holders(token_address, page=page, page_size=100)
            if not batch:
                break
            holders.extend(batch)
            page += 1
            time.sleep(0.3)
        return holders[:top_n]

    # ── Security-Specific Helpers ────────────────────────────────────────────

    @staticmethod
    def get_deployer_transactions(deployer: str, pages: int = 5) -> list[dict]:
        """Get recent transaction history for deployer analysis."""
        all_txs = []
        for page in range(1, pages + 1):
            txs = FreeSolscanClient.account_transactions(deployer, page=page, page_size=40)
            if not txs:
                break
            all_txs.extend(txs)
            time.sleep(0.2)
        return all_txs

    @staticmethod
    def get_wallet_funding_sources(wallet: str, days: int = 30) -> list[dict]:
        """Trace where a wallet got its initial SOL from - KEY for exchange funding %."""
        transfers = FreeSolscanClient.top_address_transfers(wallet, range_days=days)
        if not transfers:
            return []

        # Look for incoming transfers (funding)
        funding_sources = []
        for tx in transfers:
            flow = tx.get("flow", "")
            if flow == "in" or tx.get("change_amount", 0) > 0:
                funding_sources.append(
                    {
                        "from": tx.get("from_address"),
                        "to": tx.get("to_address"),
                        "amount": tx.get("change_amount", 0),
                        "token": tx.get("token_address"),
                        "symbol": tx.get("token_symbol"),
                        "time": tx.get("block_time"),
                        "tx_hash": tx.get("trans_id"),
                    }
                )
        return funding_sources

    @staticmethod
    def get_token_creation_tx(token_address: str) -> dict | None:
        """Find the transaction that created this token."""
        transfers = FreeSolscanClient.account_transfers(token_address, page=1, page_size=10)
        if transfers and len(transfers) > 0:
            # The earliest transfer often reveals the creation
            oldest = min(transfers, key=lambda x: x.get("block_time", float("inf")))
            tx_hash = oldest.get("trans_id")
            if tx_hash:
                return FreeSolscanClient.transaction_detail(tx_hash)
        return None

    @staticmethod
    def get_lp_token_accounts(token_address: str) -> list[dict]:
        """Get liquidity pool token accounts for LP analysis."""
        markets = FreeSolscanClient.token_markets(token_address, page_size=100)
        lp_accounts = []
        if markets:
            for market in markets:
                lp_info = {
                    "market_id": market.get("marketId"),
                    "program": market.get("programId"),
                    "liquidity_a": market.get("liquidityA"),
                    "liquidity_b": market.get("liquidityB"),
                    "volume_24h": market.get("volume24h"),
                    "lp_locked": market.get("lpLocked"),
                    "lp_lock_tx": market.get("lpLockTx"),
                }
                lp_accounts.append(lp_info)
        return lp_accounts

# ─── Known Exchange Wallets (for Exchange Funding % calculation) ────────────

KNOWN_EXCHANGE_WALLETS = {
    # Binance
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWJTSc": "Binance",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJbkEjpJgjHfUJVZW": "Binance",
    # Coinbase
    "3X3kC6PTLVPKYxFG3KeiqEA6f3icpJYYrGAGTB2EfUta": "Coinbase",
    "GJRs4FwHtemZ5ZE9x3SSv2EHfFpQMFt4VqFz9KxM8WGH": "Coinbase",
    # Kraken
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Kraken",
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN1": "Kraken",
    # OKX
    "7CNAohxBYpFi8zAAfNcRpt7Hjn2FyuKVs2aHXV5Wpump": "OKX",
    "BHwdGGP9LFLBdPZWuY6mk8mGG9i7WwfUuRG4vxWaFkqA": "OKX",
    # Bybit
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWJTSc": "Bybit",  # noqa: F601
    "6FzX58J2q7Wd1j5GmU5uZJhKLnG9zQZxYGvZk2Jx6WqD": "Bybit",
    # Jupiter / DEX aggregator
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter DEX",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGJBb": "Jupiter DEX",
    # Pump.fun
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "Pump.fun",
    # Raydium
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium",
    # Orca
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca",
    # Phantom
    "DeJBGdMFa1uynnnKiwrVioatTuHmNLpyADnmSZ5EVXd1": "Phantom",
}

EXCHANGE_DOMAINS = {
    "binance.com",
    "coinbase.com",
    "kraken.com",
    "okx.com",
    "bybit.com",
    "kucoin.com",
    "gate.io",
    "mexc.com",
    "huobi.com",
    "bitget.com",
    "crypto.com",
}

def is_known_exchange(wallet_address: str) -> str | None:
    """Check if a wallet belongs to a known exchange."""
    return KNOWN_EXCHANGE_WALLETS.get(wallet_address)

# ─── Quick Test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test with a known token
    token = "So11111111111111111111111111111111111111112"  # wSOL
    print("Testing Free Solscan Client...")
    data = FreeSolscanClient.token_data(token)
    print(f"Token: {data}")
    holders = FreeSolscanClient.token_holders_total(token)
    print(f"Total holders: {holders}")
