"""crypto_basics course content."""

from __future__ import annotations

# Course metadata
crypto_basics = {
    "id": "crypto-basics",
    "title": "Crypto Security 101",
    "subtitle": "The foundation every crypto investor needs to survive",
    "description": "Learn blockchain fundamentals, wallet safety, how to recognize common scams, and build a security routine that keeps your funds safe. No prior knowledge required.",
    "difficulty": "beginner",
    "duration_minutes": 150,
    "lessons_count": 12,
    "xp_reward": 1500,
    "badge_reward": "crypto-cadet",
    "color": "#6366f1",
    "icon": "shield",
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Blockchain.svg?width=800",
    "image_alt": "Illustration of a blockchain: blocks chained together with cryptographic hashes",
}

# Modules
from .what_is_a_rug_pull import what_is_a_rug_pull
from .how_blockchains_work import how_blockchains_work
from .wallet_safety_fundamentals import wallet_safety_fundamentals
from .recognizing_common_scams import recognizing_common_scams
from .understanding_token_contracts import understanding_token_contracts
from .liquidity_and_market_caps import liquidity_and_market_caps
from .introduction_to_rugcheck_tools import introduction_to_rugcheck_tools
from .red_flags_new_token_launches import red_flags_new_token_launches
from .social_engineering_in_crypto import social_engineering_in_crypto
from .safe_trading_practices import safe_trading_practices
from .understanding_gas_and_fees import understanding_gas_and_fees
from .building_your_security_routine import building_your_security_routine
crypto_basics["modules"] = [
    what_is_a_rug_pull,
    how_blockchains_work,
    wallet_safety_fundamentals,
    recognizing_common_scams,
    understanding_token_contracts,
    liquidity_and_market_caps,
    introduction_to_rugcheck_tools,
    red_flags_new_token_launches,
    social_engineering_in_crypto,
    safe_trading_practices,
    understanding_gas_and_fees,
    building_your_security_routine,
]

__all__ = ["crypto_basics"]
