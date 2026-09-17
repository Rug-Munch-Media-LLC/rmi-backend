"""crypto_basics course content."""
from __future__ import annotations

# Course metadata
crypto_basics = {
    "id": 'crypto-basics',
    "title": 'Crypto Security 101',
    "subtitle": 'The foundation every crypto investor needs to survive',
    "description": 'Learn blockchain fundamentals, wallet safety, how to recognize common scams, and build a security routine that keeps your funds safe. No prior knowledge required.',
    "difficulty": 'beginner',
    "duration_minutes": 150,
    "lessons_count": 12,
    "xp_reward": 1500,
    "badge_reward": 'crypto-cadet',
    "color": '#6366f1',
    "icon": 'shield',
    "image": 'https://commons.wikimedia.org/wiki/Special:FilePath/Blockchain.svg?width=800',
    "image_alt": 'Illustration of a blockchain: blocks chained together with cryptographic hashes',
}

# Modules
crypto_basics["modules"] = [
    __import__(f"crypto_basics.what_is_a_rug_pull", fromlist=["what_is_a_rug_pull"]).what_is_a_rug_pull,
    __import__(f"crypto_basics.how_blockchains_work", fromlist=["how_blockchains_work"]).how_blockchains_work,
    __import__(f"crypto_basics.wallet_safety_fundamentals", fromlist=["wallet_safety_fundamentals"]).wallet_safety_fundamentals,
    __import__(f"crypto_basics.recognizing_common_scams", fromlist=["recognizing_common_scams"]).recognizing_common_scams,
    __import__(f"crypto_basics.understanding_token_contracts", fromlist=["understanding_token_contracts"]).understanding_token_contracts,
    __import__(f"crypto_basics.liquidity_and_market_caps", fromlist=["liquidity_and_market_caps"]).liquidity_and_market_caps,
    __import__(f"crypto_basics.introduction_to_rugcheck_tools", fromlist=["introduction_to_rugcheck_tools"]).introduction_to_rugcheck_tools,
    __import__(f"crypto_basics.red_flags_new_token_launches", fromlist=["red_flags_new_token_launches"]).red_flags_new_token_launches,
    __import__(f"crypto_basics.social_engineering_in_crypto", fromlist=["social_engineering_in_crypto"]).social_engineering_in_crypto,
    __import__(f"crypto_basics.safe_trading_practices", fromlist=["safe_trading_practices"]).safe_trading_practices,
    __import__(f"crypto_basics.understanding_gas_and_fees", fromlist=["understanding_gas_and_fees"]).understanding_gas_and_fees,
    __import__(f"crypto_basics.building_your_security_routine", fromlist=["building_your_security_routine"]).building_your_security_routine,
]

__all__ = ["crypto_basics"]
