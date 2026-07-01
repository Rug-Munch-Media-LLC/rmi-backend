"""
RMI Agent Skills - Teaching agents how to use our tools effectively.

Served as MCP prompts/resources so agents discover best practices
alongside tool definitions. Each skill teaches a specific workflow.
"""


# ═══════════════════════════════════════════════════════════════════════════
# AGENT SKILLS - Workflow guides for common agent tasks
# ═══════════════════════════════════════════════════════════════════════════

AGENT_SKILLS = {
    "token_vetting": {
        "name": "Pre-Buy Token Vetting",
        "description": "Complete workflow to vet a token before buying. Run these tools in order for maximum safety.",
        "tools_used": [
            "rug_pull_predictor",
            "clone_detect",
            "gmgn_security",
            "deployer_history",
            "sniper_alert",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "rug_pull_predictor",
                "why": "Quick risk score. If score > 70, skip the rest - it's likely a rug.",
            },
            {
                "step": 2,
                "tool": "clone_detect",
                "why": "Check if contract is copied from known scams. Cloned contracts are red flags.",
            },
            {
                "step": 3,
                "tool": "gmgn_security",
                "why": "Analyze holder concentration. If top 10 hold > 50%, high dump risk.",
            },
            {
                "step": 4,
                "tool": "deployer_history",
                "why": "Check deployer track record. Previous scams = immediate skip.",
            },
            {
                "step": 5,
                "tool": "sniper_alert",
                "why": "Check for sniper bot activity. If snipers already in, expect immediate dump on your entry.",
            },
        ],
        "verdict_logic": "Count red flags: rug_score > 70 (+2), cloned (+2), top10 > 50% (+1), deployer_scams > 0 (+3), snipers > 5 (+1). Score 0-1 = BUY, 2-3 = CAUTION, 4+ = SKIP.",
        "pro_tip": "Use the Hunter Pack bundle ($0.09) instead of individual calls to save 53%.",
    },
    "whale_tracking": {
        "name": "Whale Movement Tracking",
        "description": "Track what the big wallets are doing and piggyback their moves.",
        "tools_used": ["whale_scan", "whale_accumulation", "smart_money_alpha", "wallet_pnl"],
        "workflow": [
            {
                "step": 1,
                "tool": "whale_scan",
                "why": "Find recent whale activity. Focus on wallets moving > $100K.",
            },
            {
                "step": 2,
                "tool": "whale_accumulation",
                "why": "Check if whales are quietly accumulating specific tokens.",
            },
            {
                "step": 3,
                "tool": "smart_money_alpha",
                "why": "See what historically profitable wallets are buying right now.",
            },
            {
                "step": 4,
                "tool": "wallet_pnl",
                "why": "Verify the whale's track record. Follow only wallets with >60% win rate.",
            },
        ],
        "pro_tip": "Subscribe to Whale Alert Stream ($0.75/hr) for real-time notifications instead of polling.",
    },
    "scam_investigation": {
        "name": "Scam Investigation & Reporting",
        "description": "Investigate a suspected scam token or wallet and build an evidence report.",
        "tools_used": [
            "funding_trace",
            "insider_network",
            "wash_trading",
            "wallet_graph",
            "deployer_history",
        ],
        "workflow": [
            {
                "step": 1,
                "tool": "funding_trace",
                "why": "Trace where the deployer got their initial funds. CEX funding = easier to identify.",
            },
            {
                "step": 2,
                "tool": "deployer_history",
                "why": "Check every token this wallet has launched. Pattern of scams = evidence.",
            },
            {
                "step": 3,
                "tool": "insider_network",
                "why": "Map connected wallets. Coordinated groups amplify scam impact.",
            },
            {
                "step": 4,
                "tool": "wash_trading",
                "why": "Detect fake volume. Wash trading creates false appearance of demand.",
            },
            {
                "step": 5,
                "tool": "wallet_graph",
                "why": "Visualize the full transaction network to identify the scam ring.",
            },
        ],
        "pro_tip": "Use the Wallet Forensics Pack ($0.17) to save 51% on investigations.",
    },
    "alpha_discovery": {
        "name": "Alpha Discovery Pipeline",
        "description": "Automated pipeline to discover tokens BEFORE they pump.",
        "tools_used": ["fresh_pair", "sentiment_spike", "smart_money_alpha", "whale_accumulation"],
        "workflow": [
            {
                "step": 1,
                "tool": "fresh_pair",
                "why": "Find newly created pairs. First mover advantage matters.",
            },
            {
                "step": 2,
                "tool": "sentiment_spike",
                "why": "Check if social media is picking up. Early sentiment = early signal.",
            },
            {
                "step": 3,
                "tool": "smart_money_alpha",
                "why": "See if profitable wallets are entering. Follow the money.",
            },
            {
                "step": 4,
                "tool": "whale_accumulation",
                "why": "Confirm whales are accumulating. Large buys confirm the signal.",
            },
        ],
        "pro_tip": "Subscribe to New Token Firehose ($0.50/hr) to catch every token the moment it launches.",
    },
    "portfolio_defense": {
        "name": "Portfolio Defense System",
        "description": "Protect your portfolio by monitoring positions for exit signals.",
        "tools_used": ["rug_pull_predictor", "liquidity_flow", "whale_scan", "unlock_calendar"],
        "workflow": [
            {
                "step": 1,
                "tool": "rug_pull_predictor",
                "why": "Daily risk check on held tokens. Score changes = early warning.",
            },
            {
                "step": 2,
                "tool": "liquidity_flow",
                "why": "Track liquidity leaving. Liquidity exodus precedes price crashes.",
            },
            {
                "step": 3,
                "tool": "whale_scan",
                "why": "Check if whales are selling your holdings. Follow the exits.",
            },
            {
                "step": 4,
                "tool": "unlock_calendar",
                "why": "Know when team tokens unlock. Unlocks often trigger dumps.",
            },
        ],
        "pro_tip": "Subscribe to Security Alert Feed ($0.60/hr) for instant rug pull and exploit notifications.",
    },
    "airdrop_hunting": {
        "name": "Airdrop Qualification & Sybil Check",
        "description": "Find qualifying airdrops and verify eligibility without getting flagged as Sybil.",
        "tools_used": ["airdrop_finder", "airdrop_check", "wallet_graph", "wallet_pnl"],
        "workflow": [
            {
                "step": 1,
                "tool": "airdrop_finder",
                "why": "Discover active airdrops with eligibility criteria.",
            },
            {
                "step": 2,
                "tool": "airdrop_check",
                "why": "Verify the airdrop is legitimate (not a wallet drainer).",
            },
            {
                "step": 3,
                "tool": "wallet_graph",
                "why": "Check wallet isn't linked to Sybil clusters that could disqualify you.",
            },
            {
                "step": 4,
                "tool": "wallet_pnl",
                "why": "Ensure wallet history looks organic. Fresh wallets with no history get flagged.",
            },
        ],
        "pro_tip": "Use Batch Wallet Analysis ($0.03/10 wallets) to check multiple wallets for Sybil patterns.",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# ANTI-ABUSE RULES - What agents must know to avoid getting blocked
# ═══════════════════════════════════════════════════════════════════════════

ANTI_ABUSE_RULES = {
    "rate_limits": {
        "free_trials": "1-5 calls per tool. Fingerprint-gated. Exceeding triggers 1-hour cooldown.",
        "paid_calls": "No hard limit. Rate limited at 50 calls/second per IP.",
        "subscription": "Based on tier. Exceeding daily cap charges per-call rates for remainder.",
        "streams": "One connection per subscription. Reconnection within 30s reuses same session.",
    },
    "prohibited": [
        "Scraping tool descriptions or pricing for resale",
        "Credential stuffing or trial abuse across multiple fingerprints",
        "Automated mass scanning without subscription",
        "Reselling raw tool output as a competing service",
        "Using free trials to build derivative datasets",
    ],
    "best_practices": [
        "Use bundles for multi-tool workflows - cheaper and faster",
        "Cache results locally - our data has TTLs for a reason",
        "Batch your queries - batch products are 75-90% cheaper",
        "Subscribe if you make >50 calls/day - payback at 60-90% discount",
        "Use webhooks for alerts instead of polling - saves your credits",
    ],
}

# ═══════════════════════════════════════════════════════════════════════════
# AGENT PROMPTS - Ready-to-use system prompts for AI agents
# ═══════════════════════════════════════════════════════════════════════════

AGENT_PROMPTS = {
    "trader_agent": {
        "role": "Crypto Trading Agent",
        "system_prompt": "You are a crypto trading agent with access to Rug Munch Intelligence tools. Before any trade: 1) Run pre-buy vetting, 2) Check whale activity, 3) Verify deployer history. Only recommend trades passing all checks. Use bundles to save credits.",
        "recommended_skills": ["token_vetting", "whale_tracking", "portfolio_defense"],
        "starter_bundle": "hunter_pack",
    },
    "security_agent": {
        "role": "Blockchain Security Agent",
        "system_prompt": "You are a blockchain security investigator. Investigate suspicious tokens and wallets thoroughly. Build evidence chains. Use the forensic workflow: trace funding, map networks, detect wash trading, analyze graphs. Every investigation should produce actionable evidence.",
        "recommended_skills": ["scam_investigation", "token_vetting"],
        "starter_bundle": "forensic_pack",
    },
    "alpha_agent": {
        "role": "Alpha Discovery Agent",
        "system_prompt": "You are an alpha discovery agent. Your job is finding tokens before they pump. Monitor new pairs, track social sentiment, follow smart money. Speed matters - use streaming for real-time data, batch for screening. Report signals with confidence scores.",
        "recommended_skills": ["alpha_discovery", "whale_tracking"],
        "starter_bundle": "market_pulse",
    },
    "portfolio_agent": {
        "role": "Portfolio Management Agent",
        "system_prompt": "You are a portfolio manager. Protect assets, identify exits, optimize allocations. Monitor positions daily: rug risk, liquidity flow, whale exits, unlock schedules. Alert on any deterioration. Use webhooks for instant notifications.",
        "recommended_skills": ["portfolio_defense", "whale_tracking"],
        "starter_bundle": "hunter_pack",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# CATALOG
# ═══════════════════════════════════════════════════════════════════════════


def get_agent_skills() -> dict:
    return {
        "skills": AGENT_SKILLS,
        "anti_abuse": ANTI_ABUSE_RULES,
        "agent_prompts": AGENT_PROMPTS,
        "quick_start": {
            "message": "New agent? Start with Hunter Tier ($14.99/mo, 200 calls/day). Use bundles for 50%+ savings. Follow the skill workflows for best results.",
            "recommended_first_tool": "rug_pull_predictor",
            "why": "Free trial available. Most important tool - never buy a token without checking it first.",
        },
    }
