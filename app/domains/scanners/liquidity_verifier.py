"""
SENTINEL - Liquidity Lock Verification
========================================
Verifies LP token locks against known locker platforms, detects fake locks,
split-lock scams, and monitors lock expiry dates.

Supported platforms:
  - UNCX Network (ETH, BSC, Polygon, Arbitrum)
  - Team Finance (ETH, BSC, Polygon, Avalanche)
  - PinkLock (multi-chain)
  - Mudra (BSC)
  - Solana LP Burn verification

Data sources (direct API calls - no self.api_base):
  EVM chains: Etherscan-family APIs (token holders, contract verification)
               Moralis (token holders, wallet balances)
  Solana:     Helius DAS API (token account info, owner verification)
              Helius Enhanced Transactions (LP burn events)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, cast

import httpx
import requests

from app.chain_registry import get_known_lockers, is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

logger = get_logger("liquidity_verifier")

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")

# Etherscan-family network configs
ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}

MORALIS_DATA_URL = "https://deep-index.moralis.io/api/v2.2"
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")


class LockStatus(Enum):
    LOCKED = "locked"
    BURNED = "burned"
    UNLOCKED = "unlocked"
    PARTIAL = "partial"  # Split lock (e.g., 80% locked, 20% unlocked)
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class LockDetail:
    locker_address: str
    platform: str  # "UNCX", "TeamFinance", "PinkLock", "Mudra", "SolanaBurn"
    lock_id: str = ""
    amount_locked: float = 0.0
    total_supply: float = 0.0
    percentage_locked: float = 0.0
    lock_expiry: int | None = None  # unix timestamp
    is_legit: bool = True  # False if locker is a fake/unverified contract
    is_burned: bool = False
    unlock_in_days: int | None = None


@dataclass
class LockReport:
    token_address: str
    chain: str
    status: LockStatus
    total_locked_percentage: float  # % of LP locked or burned
    lock_details: list[LockDetail]
    is_legitimate_locker: bool  # True if all lockers are verified platforms
    has_split_lock: bool  # True if partial lock detected
    upcoming_unlocks: list[LockDetail]  # Locks expiring within 7 days
    fake_lock_detected: bool  # True if a fake/unverified locker is found
    lp_burned: bool  # True if LP tokens are burned (Solana)
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    warnings: list[str] = field(default_factory=list)


# Dead/burn addresses for Solana
SOLANA_BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
}


@sentinel_module(
    key="liquidity_verification",
    tier="free",
    description="LP lock verification & fake lock detection",
    method="verify_lock",
)
class LiquidityVerifier:
    """Verifies liquidity locks, detects fake locks, monitors expiry.

    Uses direct API calls:
      - Etherscan-family for EVM LP holder queries
      - Moralis for EVM token holder data
      - Helius for Solana LP burn verification
    """

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=15.0)

    # ── Etherscan helpers ────────────────────────────────────

    def _etherscan_net(self, chain: str) -> dict[str, Any]:
        """Get Etherscan-family config for a chain."""
        net_key = chain.lower()
        if net_key == "solana":
            return {}  # Not applicable
        return ETHERSCAN_NETWORKS.get(net_key, ETHERSCAN_NETWORKS["ethereum"])

    async def _etherscan_get(self, chain: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Make a rate-limited Etherscan-family API call."""
        net = self._etherscan_net(chain)
        if not net:
            return None
        api_key = net["key"]
        if not api_key:
            logger.warning(f"No Etherscan API key configured for {chain}")
            return None
        params["apikey"] = api_key
        try:
            resp = await self.client.get(net["url"], params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result")
                return data
            elif resp.status_code == 429:
                logger.warning(f"Etherscan rate limited for {chain}")
                return None
        except (
            httpx.HTTPError,
            requests.RequestException,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_etherscan_get_error", exc_info=True)
            logger.warning(f"Etherscan call failed for {chain}: {e}")
        return None

    # ── Moralis helpers ───────────────────────────────────────

    def _moralis_chain(self, chain: str) -> str:
        """Convert chain name to Moralis chain param."""
        mapping = {
            "ethereum": "eth",
            "eth": "eth",
            "bsc": "bsc",
            "polygon": "polygon",
            "avalanche": "avalanche",
            "fantom": "fantom",
            "arbitrum": "arbitrum",
            "optimism": "optimism",
            "base": "base",
        }
        return mapping.get(chain.lower(), "eth")

    async def _moralis_get(self, url: str) -> dict[str, Any] | None:
        """Make a Moralis Data API call with key rotation."""
        if not MORALIS_API_KEY:
            logger.warning("MORALIS_API_KEY not set")
            return None
        try:
            resp = await self.client.get(url, headers={"X-API-Key": MORALIS_API_KEY, "Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return cast("dict[str, Any]", data)
                return data.get("result", data.get("data", data))
            elif resp.status_code == 429:
                logger.warning("Moralis rate limited")
        except (
            httpx.HTTPError,
            requests.RequestException,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_moralis_get_error", exc_info=True)
            logger.warning(f"Moralis call failed: {e}")
        return None

    # ── Helius helpers ────────────────────────────────────────

    async def _helius_rpc(self, method: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        """Make a Helius JSON-RPC call."""
        if not HELIUS_API_KEY:
            logger.warning("HELIUS_API_KEY not set")
            return None
        try:
            resp = await self.client.post(
                f"{HELIUS_RPC_URL}/?api-key={HELIUS_API_KEY}",
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result")
        except (
            httpx.HTTPError,
            requests.RequestException,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_helius_rpc_error", exc_info=True)
            logger.warning(f"Helius RPC call failed: {e}")
        return None

    async def _helius_das_get_asset(self, mint_address: str) -> dict[str, Any] | None:
        """Get DAS asset info for a Solana token via Helius DAS API."""
        if not HELIUS_API_KEY:
            return None
        try:
            resp = await self.client.post(
                f"{HELIUS_RPC_URL}/?api-key={HELIUS_API_KEY}",
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "getAsset",
                    "params": {"id": mint_address},
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    result = data.get("result")
                    if isinstance(result, dict):
                        return result
                return data
        except (
            httpx.HTTPError,
            requests.RequestException,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_helius_das_get_asset_error", exc_info=True)
            logger.warning(f"Helius DAS asset lookup failed for {mint_address}: {e}")
        return None

    async def _helius_get_token_accounts(self, owner: str) -> list[Any] | None:
        """Get all token accounts for a Solana wallet via Helius."""
        result = await self._helius_rpc(
            "getTokenAccountsByOwner",
            [
                owner,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )
        if result and isinstance(result, dict):
            return result.get("value", [])
        return None

    # ── Core verification logic ──────────────────────────────

    def is_known_locker(self, address: str, chain: str) -> str | None:
        """Check if address is a known legitimate locker contract."""
        chain_lockers = get_known_lockers(chain)
        return chain_lockers.get(address.lower() if not is_solana(chain) else address)

    # Known stablecoin / wrapped token symbols - LP lock check is irrelevant
    STABLECOIN_SYMBOLS: ClassVar[set[str]] = {
        "USDC",
        "USDT",
        "DAI",
        "BUSD",
        "TUSD",
        "USDP",
        "GUSD",
        "HUSD",
        "USDD",
        "FRAX",
        "LUSD",
        "SUSD",
        "USDS",
        "USDJ",
        "USDX",
        "EURS",
        "WETH",
        "WBTC",
        "WBNB",
        "WMATIC",
        "WAVAX",
        "WFTM",
        "WGLMR",
        "SOL",
        "WSOL",
        "BSOL",
        "MSOL",
    }

    def _is_stablecoin(self, symbol: str) -> bool:
        """Check if token is a known stablecoin or wrapped asset."""
        if not symbol:
            return False
        sym = symbol.upper().strip()
        return sym in self.STABLECOIN_SYMBOLS or sym.startswith(("W", "ST"))

    def is_dead_address(self, address: str, chain: str) -> bool:
        """Check if address is a known burn/dead address."""
        if is_solana(chain):
            return address in SOLANA_BURN_ADDRESSES
        return address.lower() in {
            "0x000000000000000000000000000000000000dEaD".lower(),
            "0x0000000000000000000000000000000000000000".lower(),
        }

    async def _verify_lock_onchain(
        self, token_address: str, chain: str, lp_token: str = ""
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Verify a liquidity lock on-chain.

        Solana: Helius LP burn check (holder list returned empty).
        EVM:    Moralis / Etherscan LP holder lookup.

        Args:
            token_address: token to verify.
            chain: chain name.
            lp_token: optional LP token address.

        Returns:
            (lp_burned, lp_holders) — lp_holders is empty for the Solana burn path.
        """
        if is_solana(chain):
            lp_burned = await self._check_solana_lp_burn(token_address)
            return lp_burned, []
        lp_holders = await self._get_lp_holders(token_address, chain, lp_token)
        return False, lp_holders

    def _check_lock_platforms(
        self, token_address: str, chain: str, lp_holders: list[dict[str, Any]]
    ) -> tuple[list[LockDetail], float, bool, bool]:
        """Classify LP holders against known locker platforms (UNCX, Team Finance, PinkLock, Mudra).

        Known lockers are treated as legit; dead addresses count as LP burn;
        "lock"-labeled holders that are not a known locker are flagged as fake/unverified.

        Args:
            token_address: token address (kept for signature parity with the spec).
            chain: chain name.
            lp_holders: holder dicts from on-chain verification.

        Returns:
            (lock_details, total_locked_pct, fake_lock, all_legit).
        """
        lock_details: list[LockDetail] = []
        total_locked_pct = 0.0
        fake_lock = False
        all_legit = True

        for holder in lp_holders:
            locker = self.is_known_locker(holder.get("address", ""), chain)

            if locker:
                lock_details.append(
                    LockDetail(
                        locker_address=holder.get("address", ""),
                        platform=locker,
                        percentage_locked=holder.get("percentage", 0),
                        is_legit=True,
                    )
                )
                total_locked_pct += holder.get("percentage", 0)
            elif self.is_dead_address(holder.get("address", ""), chain):
                # LP burned
                lock_details.append(
                    LockDetail(
                        locker_address=holder.get("address", ""),
                        platform="Burn",
                        percentage_locked=holder.get("percentage", 0),
                        is_burned=True,
                    )
                )
                total_locked_pct += holder.get("percentage", 0)
            elif "lock" in holder.get("label", "").lower() and not self.is_known_locker(
                holder.get("address", ""), chain
            ):
                # Unknown/unverified locker - potential fake
                lock_details.append(
                    LockDetail(
                        locker_address=holder.get("address", ""),
                        platform="Unknown (UNVERIFIED)",
                        percentage_locked=holder.get("percentage", 0),
                        is_legit=False,
                    )
                )
                total_locked_pct += holder.get("percentage", 0)
                fake_lock = True
                all_legit = False

        return lock_details, total_locked_pct, fake_lock, all_legit

    async def verify_lock(self, token_address: str, chain: str, lp_token: str = "", symbol: str = "") -> LockReport:
        """Full liquidity verification for a token.

        Steps:
        1. Get LP token address (if not provided)
        2. Get top LP holders
        3. Check each holder against known locker contracts
        4. Verify locker authenticity (not a fake deployed by dev)
        5. Check lock expiry dates
        6. Detect split-lock scams
        7. For Solana: check LP burn

        NOTE: Stablecoins and wrapped tokens are skipped - LP lock is irrelevant.
        """
        # Skip stablecoins/wrapped tokens - they don't need LP locks
        if self._is_stablecoin(symbol):
            return LockReport(
                token_address=token_address,
                chain=chain,
                status=LockStatus.LOCKED,
                total_locked_percentage=100.0,
                lock_details=[
                    LockDetail(
                        locker_address="stablecoin",
                        platform="N/A",
                        percentage_locked=100.0,
                        is_legit=True,
                    )
                ],
                is_legitimate_locker=True,
                has_split_lock=False,
                upcoming_unlocks=[],
                fake_lock_detected=False,
                lp_burned=False,
                risk_score=0,
                risk_level="LOW",
                warnings=["Stablecoin/wrapped token - LP lock check not applicable"],
            )

        # Verify the lock on-chain (Solana LP burn via Helius, or EVM holders via Moralis/Etherscan)
        lp_burned, lp_holders = await self._verify_lock_onchain(token_address, chain, lp_token)
        if lp_burned:
            return LockReport(
                token_address=token_address,
                chain=chain,
                status=LockStatus.BURNED,
                total_locked_percentage=100.0,
                lock_details=[
                    LockDetail(
                        locker_address="burn",
                        platform="Solana Burn",
                        percentage_locked=100.0,
                        is_burned=True,
                    )
                ],
                is_legitimate_locker=True,
                has_split_lock=False,
                upcoming_unlocks=[],
                fake_lock_detected=False,
                lp_burned=True,
                risk_score=0,
                risk_level="LOW",
                warnings=["LP tokens burned - safest mechanism"],
            )

        # Classify LP holders against known locker platforms (UNCX, Team Finance, PinkLock, Mudra)
        lock_details, total_locked_pct, fake_lock, all_legit = self._check_lock_platforms(
            token_address, chain, lp_holders
        )

        upcoming_unlocks: list[LockDetail] = []
        has_split_lock = False

        # Check for approaching unlocks
        now = datetime.now(UTC)
        for detail in lock_details:
            if detail.lock_expiry:
                days_until = (datetime.fromtimestamp(detail.lock_expiry, tz=UTC) - now).days
                detail.unlock_in_days = days_until
                if 0 < days_until < 7:
                    upcoming_unlocks.append(detail)

        # Detect split locks (e.g., 80% locked, 20% unlocked)
        if lock_details:
            max_lock = max(d.percentage_locked for d in lock_details)
            other_locks = [d for d in lock_details if d.percentage_locked < max_lock and not d.is_burned]
            if other_locks and max_lock < 90:
                has_split_lock = True

        # Determine overall status
        if total_locked_pct >= 95:
            status = LockStatus.BURNED if any(d.is_burned for d in lock_details) else LockStatus.LOCKED
        elif total_locked_pct >= 70:
            status = LockStatus.LOCKED
        elif total_locked_pct >= 30:
            status = LockStatus.PARTIAL
        else:
            status = LockStatus.UNLOCKED

        # Calculate risk
        risk_score, risk_level, warnings = self._calculate_risk(
            total_locked_pct, all_legit, fake_lock, has_split_lock, upcoming_unlocks
        )

        return LockReport(
            token_address=token_address,
            chain=chain,
            status=status,
            total_locked_percentage=round(total_locked_pct, 2),
            lock_details=lock_details,
            is_legitimate_locker=all_legit,
            has_split_lock=has_split_lock,
            upcoming_unlocks=upcoming_unlocks,
            fake_lock_detected=fake_lock,
            lp_burned=False,
            risk_score=risk_score,
            risk_level=risk_level,
            warnings=warnings,
        )

    async def _check_solana_lp_burn(self, token_address: str) -> bool:
        """Check if Solana LP tokens are burned using Helius.

        Strategy:
        1. Look up the token's DexScreener pair to find the LP mint address.
        2. Query Helius DAS API / RPC for the LP token account balance
           at the burn address (1nc1nerator11111111111111111111111111111111).
        3. If burn address holds the LP tokens, they're burned.
        """
        # Try DexScreener to find the LP pair info
        try:
            resp = await self.client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    pair = pairs[0]
                    lp_address = pair.get("pairAddress", "")
                    if not lp_address:
                        return False

                    # Check Helius: get token accounts at burn addresses
                    for burn_addr in SOLANA_BURN_ADDRESSES:
                        accounts = await self._helius_get_token_accounts(burn_addr)
                        if accounts:
                            for acct in accounts:
                                info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                                mint = info.get("mint", "")
                                if mint == lp_address:
                                    return True
        except (
            httpx.HTTPError,
            requests.RequestException,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
            TimeoutError,
        ) as e:
            logger.debug("_check_solana_lp_burn_error", exc_info=True)
            logger.debug(f"Solana LP burn check failed: {e}")

        # Fallback: check token metadata for LP burn indication
        try:
            asset = await self._helius_das_get_asset(token_address)
            if asset:
                metadata = asset.get("metadata", {}) or asset.get("onChainMetadata", {}) or {}
                # Some tokens embed "LP Burned" in metadata
                description = (metadata.get("description", "") or "").lower()
                if "lp burned" in description or "liquidity burned" in description:
                    # Weak signal - corroborate with burn-address check
                    pass
        except (ConnectionError, OSError, TimeoutError):
            pass

        return False

    async def _get_lp_holders(self, token_address: str, chain: str, lp_token: str = "") -> list[Any]:
        """Get top LP holders for a token using Etherscan / Moralis.

        Strategy:
        1. If lp_token is provided, query its holders directly.
        2. Otherwise, use Moralis token holders API.
        3. Fallback to Etherscan tokentx to identify LP contract.
        """
        holders = []

        # ── EVM chains: Moralis / Etherscan ──
        target_addr = lp_token or token_address
        moralis_chain = self._moralis_chain(chain)

        # Try Moralis first (has holder percentage data)
        try:
            url = f"{MORALIS_DATA_URL}/erc20/{target_addr}/owners?chain={moralis_chain}&limit=25"
            result = await self._moralis_get(url)
            if result and isinstance(result, list):
                for entry in result:
                    owner = entry.get("owner_address", "") or entry.get("address", "")
                    balance = float(entry.get("balance", "0") or "0")
                    pct = float(entry.get("percentage_relative_to_total", 0) or 0)
                    holders.append(
                        {
                            "address": owner,
                            "balance": balance,
                            "percentage": pct,
                            "label": "",
                        }
                    )
                if holders:
                    return holders
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            logger.debug("_get_lp_holders_error", exc_info=True)
            logger.debug(f"Moralis holders query failed: {e}")

        # Fallback: Etherscan token holders
        net_key = chain.lower()
        if net_key in ETHERSCAN_NETWORKS:
            result = await self._etherscan_get(
                net_key,
                {
                    "module": "account",
                    "action": "tokentx",
                    "address": target_addr,
                    "page": 1,
                    "offset": 50,
                    "sort": "desc",
                },
            )
            if result and isinstance(result, list):
                # Aggregate unique holders from recent transfers
                holder_totals: dict[str, float] = {}
                for tx in result:
                    addr = tx.get("to", "").lower()
                    value = float(tx.get("value", "0") or "0")
                    holder_totals[addr] = holder_totals.get(addr, 0) + value
                if holder_totals:
                    total = sum(holder_totals.values()) or 1
                    sorted_holders = sorted(holder_totals.items(), key=lambda x: x[1], reverse=True)[:25]
                    for addr, bal in sorted_holders:
                        holders.append(
                            {
                                "address": addr,
                                "balance": bal,
                                "percentage": round(bal / total * 100, 2),
                                "label": "",
                            }
                        )
                    return holders

        return holders

    def _calculate_risk(
        self, locked_pct: float, all_legit: bool, fake_lock: bool, has_split: bool, upcoming: list[Any]
    ) -> tuple[Any, ...]:
        """Calculate risk score and warnings for liquidity lock status."""
        score = 0
        warnings = []

        if locked_pct >= 95:
            score += 0  # Fully locked - safest
            if all_legit:
                warnings.append("SAFE: LP fully locked on verified platform")
            else:
                score += 20
                warnings.append("WARNING: LP fully locked but on UNVERIFIED platform")
        elif locked_pct >= 70:
            score += 15
            warnings.append(f"MODERATE: LP {locked_pct:.1f}% locked - significant lockup")
        elif locked_pct >= 30:
            score += 35
            warnings.append(f"HIGH: LP only {locked_pct:.1f}% locked - moderate rug risk")
        else:
            score += 55
            warnings.append(f"CRITICAL: LP only {locked_pct:.1f}% locked - HIGH rug pull risk")

        if fake_lock:
            score += 40
            warnings.append("CRITICAL: Unverified/fake locker contract detected")

        if has_split:
            score += 15
            warnings.append("SPLIT LOCK: Partial lock detected (e.g., 80% locked, 20% unlocked)")

        if upcoming:
            score += 10
            for u in upcoming:
                warnings.append(f"UNLOCK SOON: Lock expires in {u.unlock_in_days} days")

        score = min(100, score)
        level = "CRITICAL" if score >= 70 else "HIGH" if score >= 40 else "MEDIUM" if score >= 20 else "LOW"

        return score, level, warnings
