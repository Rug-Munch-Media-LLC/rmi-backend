"""Pure data types and burn-address constants for liquidity lock verification.

Extracted from ``liquidity_verifier.py`` (degodding split, 2026-08-14).
No I/O — dataclasses, an enum, and dead-address constants. Re-exported
from the parent module so importers of ``LockStatus`` / ``LockDetail`` /
``LockReport`` / ``SOLANA_BURN_ADDRESSES`` keep the same objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
