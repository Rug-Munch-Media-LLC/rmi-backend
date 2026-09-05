"""Interactive exercise slide definitions for the Advanced Forensics course."""

EX_ON_CHAIN_FUND_TRACING_1 = (
    {
        "type": "trace-sim",
        "title": "Interactive Transaction Tracer",
        "description": (
            "Start with a known scammer wallet. Trace the stolen funds through 8 hops: "
            "wallet A → DEX swap → wallet B → bridge to another chain → wallet C → "
            "split into 50 wallets → consolidate into wallet D → deposit to exchange. "
            "Every hop revealed step by step."
        ),
    },
)

EX_ON_CHAIN_FUND_TRACING_2 = (
    {
        "type": "interactive",
        "title": "Multi-Chain Trace Exercise",
        "description": (
            "A scammer bridges stolen ETH from Ethereum to Base, swaps to USDC, bridges "
            "to Solana, and swaps to SOL. Follow the trail across three chains using "
            "bridge transaction hashes and cross-chain explorers."
        ),
    },
)

EX_ENTITY_ATTRIBUTION_CLUSTERING_1 = (
    {
        "type": "interactive",
        "title": "Entity Resolution Lab",
        "description": (
            "Given 100 addresses, use clustering heuristics to identify: (1) exchange deposit "
            "addresses (high volume, reuse pattern), (2) mixer addresses (fixed denominations), "
            "(3) personal wallets (long-term holding), and (4) scammer hot wallets (rapid turnover)."
        ),
    },
)

EX_ENTITY_ATTRIBUTION_CLUSTERING_2 = (
    {
        "type": "interactive",
        "title": "Known Scammer Clusters",
        "description": (
            "Explore a database of known scammer clusters: wallets linked to the same "
            "entity through shared funding sources, coordinated transaction timing, and "
            "behavioral patterns. Learn how to cross-reference clusters across investigations."
        ),
    },
)

EX_MIXER_TUMBLER_DETECTION_1 = (
    {
        "type": "interactive",
        "title": "Tornado Cash Mechanics",
        "description": (
            "Visual breakdown of how Tornado Cash works: users deposit fixed amounts (0.1, 1, 10, 100 ETH) "
            "into a pool. They later withdraw with a zero-knowledge proof, breaking the on-chain link. "
            "Learn to identify deposit and withdrawal patterns."
        ),
    },
)

EX_MIXER_TUMBLER_DETECTION_2 = (
    {
        "type": "interactive",
        "title": "Post-Sanctions Mixer Patterns",
        "description": (
            "Since Tornado Cash sanctions, scammers have shifted to: Wasabi/JoinMarket "
            "(CoinJoin for BTC), cross-chain bridges as pseudo-mixers, and privacy chains "
            "like Monero. Learn to identify these newer obfuscation techniques."
        ),
    },
)

EX_MEV_SANDWICH_ATTACK_FORENSICS_1 = (
    {
        "type": "interactive",
        "title": "Mempool Analysis Lab",
        "description": (
            "Watch the mempool in real-time (simulated). A user submits a swap. Within "
            "milliseconds, a bot front-runs it, the user's trade executes at a worse price, "
            "and the bot back-runs for profit. See the exact P&L of each party."
        ),
    },
)

EX_MEV_SANDWICH_ATTACK_FORENSICS_2 = (
    {
        "type": "interactive",
        "title": "Builder & Searcher Identification",
        "description": (
            "Analyze MEV-boost relay data to identify known searcher addresses, builder "
            "patterns, and the relays they use. Learn to associate MEV extraction with "
            "specific entities."
        ),
    },
)

EX_FLASH_LOAN_ATTACK_ANALYSIS_1 = (
    {
        "type": "interactive",
        "title": "Euler Finance Attack Reconstruction",
        "description": (
            "Step-by-step reconstruction of the $197M Euler Finance exploit. "
            "The attacker exploited a donation function to manipulate collateral ratios, "
            "then used flash loans to amplify the attack. Every transaction decoded."
        ),
    },
)

EX_FLASH_LOAN_ATTACK_ANALYSIS_2 = (
    {
        "type": "interactive",
        "title": "Flash Loan Attack Simulator",
        "description": (
            "Design your own flash loan attack (educational only!). Choose a vulnerability "
            "type, select a flash loan amount, and see how the attack unfolds. "
            "Understand the attack lifecycle: borrow → manipulate → exploit → repay → profit."
        ),
    },
)

EX_BRIDGE_CROSS_CHAIN_EXPLOITS_1 = (
    {
        "type": "interactive",
        "title": "Ronin Bridge Deep Dive",
        "description": (
            "The $624M Ronin bridge hack: How the attacker compromised 5 of 9 validator "
            "keys through a social engineering attack on Sky Mavis employees, then used "
            "them to approve fraudulent withdrawals. Full timeline and forensic breakdown."
        ),
    },
)

EX_BRIDGE_CROSS_CHAIN_EXPLOITS_2 = (
    {
        "type": "interactive",
        "title": "Message Verification Failure Patterns",
        "description": (
            "Compare three bridge exploits with a common theme: Wormhole (signature verification), "
            "Nomad (message verification initialized to 0x00 allowing any message), and Qubit "
            "(deprecated function left active). All stem from message/auth verification failures."
        ),
    },
)

EX_GOVERNANCE_ATTACK_DETECTION_1 = (
    {
        "type": "interactive",
        "title": "Flash Loan Governance Attack Simulator",
        "description": (
            "Watch how an attacker borrows governance tokens via flash loan, votes to "
            "drain the treasury, and repays the loan — all in one transaction. The DAO "
            "never saw it coming. Learn why snapshot-based voting is vulnerable."
        ),
    },
)

EX_GOVERNANCE_ATTACK_DETECTION_2 = (
    {
        "type": "interactive",
        "title": "Proposal Analysis Framework",
        "description": (
            "Learn to audit governance proposals: (1) Check proposer address history, "
            "(2) Analyze the proposed changes for malicious intent, (3) Verify the "
            "voting power source isn't a flash loan, (4) Check timelock implementation."
        ),
    },
)

EX_ADVANCED_SMART_CONTRACT_AUDITING_1 = (
    {
        "type": "interactive",
        "title": "Manual Audit Workflow",
        "description": (
            "Walk through a professional audit: (1) Read the spec and documentation, "
            "(2) Create a threat model, (3) Line-by-line code review with a checklist, "
            "(4) Write Proof of Concept exploits for each finding, (5) Categorize by severity."
        ),
    },
)

EX_DEFI_PROTOCOL_RISK_ASSESSMENT_1 = (
    {
        "type": "interactive",
        "title": "TVL Analysis Framework",
        "description": (
            "Learn to analyze Total Value Locked: (1) Is TVL organic or incentive-driven? "
            "(2) What's the TVL concentration (one whale vs distributed)? (3) How sticky is "
            "the TVL (withdrawal penalties, lock periods)? (4) TVL trend over 30/90 days."
        ),
    },
)

EX_DEFI_PROTOCOL_RISK_ASSESSMENT_2 = (
    {
        "type": "interactive",
        "title": "Oracle Dependency Mapping",
        "description": (
            "Map a DeFi protocol's oracle dependencies: Which price feeds does it use? "
            "Chainlink? A custom TWAP? A single DEX pool? If the oracle can be manipulated, "
            "every product relying on it is at risk. Build a dependency graph."
        ),
    },
)

EX_DARK_WEB_OFFCHAIN_INTELLIGENCE_1 = (
    {
        "type": "interactive",
        "title": "OSINT Workflow for Crypto",
        "description": (
            "Build an intelligence gathering pipeline: (1) Social media analysis (Twitter, Telegram, Discord), "
            "(2) Domain/WHOIS lookups, (3) GitHub commit analysis, (4) Darknet forum monitoring, "
            "(5) Threat intelligence feeds (TRM, Elliptic, Chainalysis)."
        ),
    },
)

EX_DARK_WEB_OFFCHAIN_INTELLIGENCE_2 = (
    {
        "type": "interactive",
        "title": "Threat Intelligence Feed Integration",
        "description": (
            "Learn to consume and correlate threat intel: sanctioned address lists (OFAC SDN), "
            "known scammer databases, phishing URL feeds, and blockchain analytics alerts. "
            "Set up automated monitoring for your watchlist addresses."
        ),
    },
)

EX_REGULATORY_COMPLIANCE_LANDSCAPE_1 = (
    {
        "type": "interactive",
        "title": "OFAC Compliance in DeFi",
        "description": (
            "Explore the OFAC SDN list and its impact on DeFi. Learn which addresses are "
            "sanctioned, how protocols implement compliance (blocking, geo-restrictions), "
            "and the controversy around censorship-resistant protocols."
        ),
    },
)

EX_REGULATORY_COMPLIANCE_LANDSCAPE_2 = (
    {
        "type": "interactive",
        "title": "KYC/AML in DeFi: The Tension",
        "description": (
            "Map the regulatory landscape: which jurisdictions require KYC for what activities? "
            "Compare the FATF Travel Rule, EU MiCA regulation, US FinCEN guidance, and "
            "how they affect DeFi protocols differently from CeFi."
        ),
    },
)

EX_CAPSTONE_THREAT_INTELLIGENCE_REPORT_1 = (
    {
        "type": "interactive",
        "title": "Capstone Briefing",
        "description": (
            "You are assigned to investigate a real-world crypto incident. Using all the skills "
            "from this course: trace the stolen funds, attribute the attacker, identify "
            "the vulnerability exploited, estimate total losses, and produce a professional "
            "threat intelligence report suitable for law enforcement or insurance claims."
        ),
    },
)
