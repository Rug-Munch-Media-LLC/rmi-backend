"""
Expanded tool aliases - maps 44 new specialized tools + 80 per-chain variants
to their closest real handler endpoint.

Per-chain variants (e.g., wallet_solana) are handled by the dispatcher
automatically via the base_tool field in TOOL_PRICES.
This file only maps the 44 brand-new tool names.
"""

EXPANDED_ALIASES = {
    # Security → real SENTINEL Tier 2/3 endpoints where available
    "flash_loan_detect": "flash_loan_detect",
    "governance_attack": "governance_attack",
    "contract_upgrade_monitor": "proxy_detect",
    "reentrancy_scanner": "static_analysis",
    "wallet_drain_scanner": "decompiler_analysis",
    "dust_attack_detect": "urlcheck",
    "oracle_manipulation": "oracle_manipulation",
    "privilege_escalation": "static_analysis",
    "phantom_mint_detect": "honeypot_check",
    "smart_contract_interactions": "decompiler_analysis",
    "wallet_label_registry": "address_labels",
    # SENTINEL Tier 3/4 direct routes
    "static_analysis": "static_analysis",
    "decompiler_analysis": "decompiler_analysis",
    "fund_flow": "fund_flow",
    "contract_diff": "contract_diff",
    # Intelligence
    "cross_chain_whale": "whale",
    "degen_score": "smart_money_alpha",
    "wallet_cluster_score": "cluster",
    "dormant_whale_alert": "whale",
    "token_distribution_health": "wallet",
    "token_velocity": "pulse",
    # Market
    "funding_rate": "market_overview",
    "options_flow": "market_overview",
    "dex_volume_rank": "market_overview",
    "liquidation_heatmap": "market_overview",
    "volatility_surface": "market_overview",
    "stablecoin_flow": "market_overview",
    # Social
    "reddit_sentiment": "sentiment",
    "discord_alpha": "social_signal",
    "telegram_pump_detect": "pump_dump_detect",
    "influencer_impact_score": "social_signal",
    "github_developer_activity": "social_signal",
    # Analysis
    "correlation_matrix": "portfolio_tracker",
    "volume_profile": "pulse",
    "orderbook_imbalance": "market_overview",
    "drawdown_analyzer": "portfolio_tracker",
    "sharpe_ratio_calc": "portfolio_tracker",
    "sector_rotation": "market_overview",
    "nft_floor_analytics": "market_overview",
    "tax_lot_optimizer": "portfolio_tracker",
    # Launchpad
    "presale_scanner": "launch",
    "ido_tracker": "launch_intel",
    "fair_launch_detect": "launch",
    "vesting_schedule_analyzer": "market_overview",
    # Premium
    "deep_forensics": "forensics",
    "whale_network_map": "cluster",
    "cross_chain_trace": "insider",
    "full_wallet_dossier": "comprehensive_audit",
    # DeFi
    "yield_aggregator": "defi_yield_scanner",
    "impermanent_loss": "defi_yield_scanner",
}
