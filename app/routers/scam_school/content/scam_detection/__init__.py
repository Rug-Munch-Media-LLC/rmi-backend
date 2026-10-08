"""scam_detection course content."""

from __future__ import annotations

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
    __import__("scam_detection.honeypot_contracts", fromlist=["honeypot_contracts"]).honeypot_contracts,
    __import__(
        "scam_detection.holder_distribution_analysis", fromlist=["holder_distribution_analysis"]
    ).holder_distribution_analysis,
    __import__(
        "scam_detection.liquidity_pool_forensics", fromlist=["liquidity_pool_forensics"]
    ).liquidity_pool_forensics,
    __import__(
        "scam_detection.smart_contract_vulnerability_patterns", fromlist=["smart_contract_vulnerability_patterns"]
    ).smart_contract_vulnerability_patterns,
    __import__("scam_detection.proxy_contract_risks", fromlist=["proxy_contract_risks"]).proxy_contract_risks,
    __import__("scam_detection.tokenomics_red_flags", fromlist=["tokenomics_red_flags"]).tokenomics_red_flags,
    __import__(
        "scam_detection.dex_manipulation_techniques", fromlist=["dex_manipulation_techniques"]
    ).dex_manipulation_techniques,
    __import__(
        "scam_detection.cross_chain_scam_patterns", fromlist=["cross_chain_scam_patterns"]
    ).cross_chain_scam_patterns,
    __import__("scam_detection.nft_scam_detection", fromlist=["nft_scam_detection"]).nft_scam_detection,
    __import__(
        "scam_detection.social_sentiment_analysis", fromlist=["social_sentiment_analysis"]
    ).social_sentiment_analysis,
    __import__(
        "scam_detection.memecoin_launch_patterns", fromlist=["memecoin_launch_patterns"]
    ).memecoin_launch_patterns,
    __import__(
        "scam_detection.building_scam_investigation", fromlist=["building_scam_investigation"]
    ).building_scam_investigation,
]

__all__ = ["scam_detection"]
