"""scam_detection course content."""

from __future__ import annotations

from .building_scam_investigation import building_scam_investigation
from .cross_chain_scam_patterns import cross_chain_scam_patterns
from .dex_manipulation_techniques import dex_manipulation_techniques
from .holder_distribution_analysis import holder_distribution_analysis
from .honeypot_contracts import honeypot_contracts
from .liquidity_pool_forensics import liquidity_pool_forensics
from .memecoin_launch_patterns import memecoin_launch_patterns
from .nft_scam_detection import nft_scam_detection
from .proxy_contract_risks import proxy_contract_risks
from .smart_contract_vulnerability_patterns import smart_contract_vulnerability_patterns
from .social_sentiment_analysis import social_sentiment_analysis
from .tokenomics_red_flags import tokenomics_red_flags

# Course metadata
scam_detection = {
    "id": "scam-detection",
    "title": "Scam Detection & Analysis",
    "subtitle": "Master the art of spotting rugs before they happen",
    "description": "Deep-dive into honeypots, tokenomics red flags, holder distribution analysis, LP forensics, smart contract vulnerabilities, cross-chain threats, NFT scams, social sentiment manipulation, and building professional scam investigation reports.",
    "difficulty": "intermediate",
    "duration_minutes": 180,
    "lessons_count": 12,
    "xp_reward": 2400,
    "badge_reward": "scam-hunter",
    "color": "#ef4444",
    "icon": "siren",
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Magnifying_glass.svg?width=800",
    "image_alt": "Magnifying glass icon representing scam detection and investigation",
}

# Modules
scam_detection["modules"] = [
    honeypot_contracts,
    holder_distribution_analysis,
    liquidity_pool_forensics,
    smart_contract_vulnerability_patterns,
    proxy_contract_risks,
    tokenomics_red_flags,
    dex_manipulation_techniques,
    cross_chain_scam_patterns,
    nft_scam_detection,
    social_sentiment_analysis,
    memecoin_launch_patterns,
    building_scam_investigation,
]

__all__ = ["scam_detection"]
