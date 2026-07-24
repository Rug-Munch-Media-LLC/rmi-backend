"""
RMI Agent Skills - Extended Pack. More workflows for more agent types.
"""

from __future__ import annotations

AGENT_SKILLS_EXTENDED = {
    "mev_avoidance": {
        "name": "MEV/Sandwich Attack Avoidance",
        "description": "Protect transactions from MEV extraction. Check before submitting any trade.",
        "tools_used": ["mev_protection", "mev_alert", "gas_oracle", "liquidity_depth"],
        "workflow": [
            {
                "step": 1,
                "tool": "mev_alert",
                "why": "Check if there is active MEV activity on the target pool.",
            },
            {
                "step": 2,
                "tool": "mev_protection",
                "why": "Verify your transaction parameters will not be frontrun.",
            },
            {
                "step": 3,
                "tool": "gas_oracle",
                "why": "Set gas appropriately. Too low = vulnerable to sandwiching.",
            },
            {
                "step": 4,
                "tool": "liquidity_depth",
                "why": "Deep liquidity reduces slippage and MEV surface area.",
            },
        ],
        "pro_tip": "Always run MEV checks on trades above $1,000. The $0.03 check saves thousand-dollar sandwiches.",
    },
    "nft_sniper": {
        "name": "NFT Mint Sniper Strategy",
        "description": "Evaluate NFT collections before mint. Avoid rugs, identify blue chips early.",
        "tools_used": [
            "clone_detect",
            "wash_trading",
            "syndicate_scan",
            "deployer_history",
            "sentiment_spike",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "deployer_history",
                "why": "Check if the collection deployer has previous rug pulls.",
            },
            {
                "step": 2,
                "tool": "clone_detect",
                "why": "Verify the art/contract is not copied from another collection.",
            },
            {
                "step": 3,
                "tool": "wash_trading",
                "why": "Detect fake volume. Wash-traded NFTs dump immediately after mint.",
            },
            {
                "step": 4,
                "tool": "syndicate_scan",
                "why": "Check for coordinated mint groups that will dump on real minters.",
            },
            {
                "step": 5,
                "tool": "sentiment_spike",
                "why": "Gauge genuine community interest vs bot-driven hype.",
            },
        ],
        "pro_tip": "Run the full scan 5 minutes before mint. Syndicates often reveal themselves in the final hour.",
    },
    "defi_yield_optimizer": {
        "name": "DeFi Yield Farming Optimizer",
        "description": "Find the best yields across chains while avoiding rugs and unsustainable APYs.",
        "tools_used": ["protocol_risk", "liquidity_flow", "arbitrage_scan", "unlock_calendar"],
        "workflow": [
            {
                "step": 1,
                "tool": "protocol_risk",
                "why": "Check protocol safety: TVL stability, admin keys, oracle dependency.",
            },
            {
                "step": 2,
                "tool": "liquidity_flow",
                "why": "Track where capital is moving. Follow the flow for best yields.",
            },
            {
                "step": 3,
                "tool": "arbitrage_scan",
                "why": "Find cross-chain yield discrepancies for higher returns.",
            },
            {
                "step": 4,
                "tool": "unlock_calendar",
                "why": "Avoid protocols with imminent team token unlocks that dilute value.",
            },
        ],
        "pro_tip": "Protocols with >1 year TVL stability and renounced admin keys are safest for long-term farming.",
    },
    "launch_sniper": {
        "name": "Token Launch Day Playbook",
        "description": "Complete launch day strategy. From detection to entry to exit.",
        "tools_used": [
            "fresh_pair",
            "sniper_alert",
            "bundler_detect",
            "liquidity_depth",
            "rug_pull_predictor",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "fresh_pair",
                "why": "Detect the pair the moment liquidity is added.",
            },
            {
                "step": 2,
                "tool": "sniper_alert",
                "why": "Check sniper activity. If >10 snipers detected, expect immediate sell pressure.",
            },
            {
                "step": 3,
                "tool": "bundler_detect",
                "why": "Check for MEV bundlers. Bundled launches often contain coordinated dumps.",
            },
            {
                "step": 4,
                "tool": "liquidity_depth",
                "why": "Verify adequate liquidity. Under $5K liquidity is a red flag.",
            },
            {
                "step": 5,
                "tool": "rug_pull_predictor",
                "why": "Final safety check before entry. Score >50 = skip.",
            },
        ],
        "pro_tip": "Use the New Token Firehose stream to catch launches instantly. The first 30 seconds determine profitability.",
    },
    "cex_listing_predictor": {
        "name": "CEX Listing Prediction",
        "description": "Predict which tokens are about to get listed on major exchanges. Front-run the announcement.",
        "tools_used": [
            "listing_predictor",
            "whale_accumulation",
            "smart_money_alpha",
            "sentiment_spike",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "listing_predictor",
                "why": "Get the primary signal. On-chain patterns that precede CEX listings.",
            },
            {
                "step": 2,
                "tool": "whale_accumulation",
                "why": "Confirm whales are accumulating. CEX listings require large supply deposits.",
            },
            {
                "step": 3,
                "tool": "smart_money_alpha",
                "why": "Check if insiders are buying. Smart money often knows before announcements.",
            },
            {
                "step": 4,
                "tool": "sentiment_spike",
                "why": "Monitor for rumor-driven volume. Real listings often leak before official announcement.",
            },
        ],
        "pro_tip": "Combine with exchange deposit tracking. Large transfers to exchange hot wallets precede listings by 24-72 hours.",
    },
    "dao_governance": {
        "name": "DAO Governance Intelligence",
        "description": "Track DAO proposals, voting power concentration, and governance attacks before they happen.",
        "tools_used": ["protocol_risk", "whale_profile", "syndicate_scan", "insider_network"],
        "workflow": [
            {
                "step": 1,
                "tool": "protocol_risk",
                "why": "Assess governance risk. Concentrated voting power = centralized control.",
            },
            {
                "step": 2,
                "tool": "whale_profile",
                "why": "Profile top voters. Understand their interests and historical voting patterns.",
            },
            {
                "step": 3,
                "tool": "syndicate_scan",
                "why": "Detect coordinated voting blocs that can hijack governance.",
            },
            {
                "step": 4,
                "tool": "insider_network",
                "why": "Map relationships between proposal authors and top voters.",
            },
        ],
        "pro_tip": "Governance attacks typically require 51% voting power. Monitor top-10 voter concentration weekly.",
    },
    "rugpull_forensics": {
        "name": "Post-Rug Investigation",
        "description": "After a rug pull: trace the funds, identify the scammer, build the evidence package.",
        "tools_used": ["funding_trace", "cross_chain_trace", "wallet_graph", "scam_database"],
        "workflow": [
            {
                "step": 1,
                "tool": "funding_trace",
                "why": "Trace where the deployer got initial funds. This leads to their identity.",
            },
            {
                "step": 2,
                "tool": "cross_chain_trace",
                "why": "Follow funds across chains. Scammers bridge to obfuscate the trail.",
            },
            {
                "step": 3,
                "tool": "wallet_graph",
                "why": "Map the full transaction network. Identify every wallet in the scam ring.",
            },
            {
                "step": 4,
                "tool": "scam_database",
                "why": "Check if these wallets appear in known scam databases. Build the case.",
            },
        ],
        "pro_tip": "CEX deposit addresses are the weak point. Scammers must eventually off-ramp through exchanges that require KYC.",
    },
    "market_maker": {
        "name": "Market Making Intelligence",
        "description": "Data for professional market makers: order flow, spread optimization, inventory management.",
        "tools_used": ["liquidity_depth", "wash_trading", "arbitrage_scan", "whale_scan"],
        "workflow": [
            {
                "step": 1,
                "tool": "liquidity_depth",
                "why": "Analyze order book depth to optimize spread and position sizing.",
            },
            {
                "step": 2,
                "tool": "wash_trading",
                "why": "Detect fake volume that distorts true market depth.",
            },
            {
                "step": 3,
                "tool": "arbitrage_scan",
                "why": "Find pricing discrepancies to balance inventory across venues.",
            },
            {
                "step": 4,
                "tool": "whale_scan",
                "why": "Anticipate large orders that will move the market against your position.",
            },
        ],
        "pro_tip": "Run liquidity analysis every 30 seconds during volatile periods. Wash trading spikes precede large manipulation events.",
    },
    "kols_detector": {
        "name": "KOL/Influencer Performance Tracker",
        "description": "Track which influencers actually deliver alpha and which are paid dumpers.",
        "tools_used": ["kol_performance", "profile_flip", "sentiment_spike", "smart_money_alpha"],
        "workflow": [
            {
                "step": 1,
                "tool": "kol_performance",
                "why": "Get the hard numbers: call accuracy, average ROI, follower quality score.",
            },
            {
                "step": 2,
                "tool": "profile_flip",
                "why": "Check for suspicious profile changes before calls. Flipped accounts = paid promotions.",
            },
            {
                "step": 3,
                "tool": "sentiment_spike",
                "why": "Verify the influencer actually moves sentiment or just posts into the void.",
            },
            {
                "step": 4,
                "tool": "smart_money_alpha",
                "why": "Cross-reference with actual smart money. If wallets are selling while KOL is shilling, run.",
            },
        ],
        "pro_tip": "The best KOLs have >60% call accuracy and negative correlation with dump events. Track them, not the hype merchants.",
    },
    "bridge_monitor": {
        "name": "Cross-Chain Bridge Monitor",
        "description": "Monitor bridge activity for arbitrage, exploit detection, and capital flow tracking.",
        "tools_used": ["liquidity_migration", "liquidity_flow", "arbitrage_scan", "whale_scan"],
        "workflow": [
            {
                "step": 1,
                "tool": "liquidity_migration",
                "why": "Detect tokens migrating across chains. Often a rug pull precursor.",
            },
            {
                "step": 2,
                "tool": "liquidity_flow",
                "why": "Track overall capital flow direction. Money moving to a chain = opportunity there.",
            },
            {
                "step": 3,
                "tool": "arbitrage_scan",
                "why": "Find cross-chain price gaps exploitable through bridges.",
            },
            {
                "step": 4,
                "tool": "whale_scan",
                "why": "Large bridge transactions often precede major market moves.",
            },
        ],
        "pro_tip": "Bridge exploiters typically test with small amounts first. Monitor bridges for unusual small transactions followed by large ones.",
    },
    "insider_trading": {
        "name": "Insider Trading Detector",
        "description": "Identify wallet clusters trading on insider information before public announcements.",
        "tools_used": [
            "insider_network",
            "syndicate_scan",
            "listing_predictor",
            "smart_money_alpha",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "insider_network",
                "why": "Map connected wallets that trade in sync before announcements.",
            },
            {
                "step": 2,
                "tool": "syndicate_scan",
                "why": "Identify the trading group. Coordinated buying before news = insider activity.",
            },
            {
                "step": 3,
                "tool": "listing_predictor",
                "why": "Cross-reference with listing prediction signals.",
            },
            {
                "step": 4,
                "tool": "smart_money_alpha",
                "why": "If multiple smart wallets enter the same microcap simultaneously, someone knows something.",
            },
        ],
        "pro_tip": "The strongest insider signal: 3+ unconnected wallets buying the same token within a 60-second window.",
    },
    "compliance_screen": {
        "name": "Compliance & Sanctions Screening",
        "description": "Screen wallets and tokens against sanctions lists, known scam databases, and OFAC records.",
        "tools_used": ["scam_database", "wash_trading", "wallet_graph", "deployer_history"],
        "workflow": [
            {
                "step": 1,
                "tool": "scam_database",
                "why": "Check against known scam, phishing, and sanctioned addresses.",
            },
            {
                "step": 2,
                "tool": "deployer_history",
                "why": "Verify the deployer has no history with sanctioned entities.",
            },
            {
                "step": 3,
                "tool": "wallet_graph",
                "why": "Trace connections to sanctioned wallets through transaction flows.",
            },
            {
                "step": 4,
                "tool": "wash_trading",
                "why": "Detect volume manipulation that may indicate compliance evasion.",
            },
        ],
        "pro_tip": "OFAC compliance requires ongoing monitoring, not one-time checks. Run weekly on all counterparties.",
    },
}

# Merge with existing skills
from app.caching_shield.agent_skills import AGENT_SKILLS, get_agent_skills

ALL_AGENT_SKILLS = {**AGENT_SKILLS, **AGENT_SKILLS_EXTENDED}

def get_all_agent_skills() -> dict:
    base = get_agent_skills()
    base["skills"] = ALL_AGENT_SKILLS
    base["quick_start"]["total_skills"] = len(ALL_AGENT_SKILLS)
    return base
