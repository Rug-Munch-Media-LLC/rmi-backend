"""
Scam School — Advanced Course: "Blockchain Forensics & Intelligence"
12 modules covering on-chain fund tracing, entity attribution, mixer detection,
MEV forensics, flash loan analysis, bridge exploits, governance attacks,
contract auditing, DeFi risk assessment, dark web intelligence, and regulatory landscape.
"""

from __future__ import annotations

advanced_forensics = {
    "id": "advanced-forensics",
    "title": "Blockchain Forensics & Intelligence",
    "subtitle": "Think like a professional blockchain detective",
    "description": (
        "Master professional-grade forensic analysis: trace funds across chains, attribute entities, "
        "detect mixers and tumblers, analyze MEV and flash loan attacks, audit smart contracts, "
        "assess DeFi protocol risk, leverage dark web intelligence, and navigate the regulatory landscape. "
        "Capstone: produce a full threat intelligence report."
    ),
    "difficulty": "advanced",
    "duration_minutes": 240,
    "lessons_count": 12,
    "xp_reward": 3600,
    "badge_reward": "forensic-analyst",
    "color": "#f59e0b",
    "icon": "microscope",
    "modules": [
        # ── Module 1 ──
        {
            "id": "on-chain-fund-tracing",
            "title": "On-Chain Fund Tracing",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Follow the Money",
                        "sub": "Every transaction leaves a permanent trail",
                    },
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
                    {
                        "type": "quiz",
                        "question": "What is the most reliable tool for tracing ETH transactions across multiple hops?",
                        "options": [
                            "CoinMarketCap",
                            "Etherscan combined with manual transaction graph analysis",
                            "Twitter",
                            "Discord",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Etherscan shows every transaction, internal transaction, and token transfer. "
                            "By following the 'To' address of each transaction, you can trace funds through "
                            "arbitrary numbers of hops. Tools like Breadcrumbs and Chainalysis automate this."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Multi-Chain Trace Exercise",
                        "description": (
                            "A scammer bridges stolen ETH from Ethereum to Base, swaps to USDC, bridges "
                            "to Solana, and swaps to SOL. Follow the trail across three chains using "
                            "bridge transaction hashes and cross-chain explorers."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'peel chain' in crypto money laundering?",
                        "options": [
                            "A type of blockchain",
                            "Sending funds through many wallets, peeling off small amounts at each step to obscure the trail",
                            "A DeFi protocol",
                            "A mining technique",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A peel chain sends large amounts through chains of wallets, 'peeling' off "
                            "smaller amounts at each step. The small amounts are harder to trace and "
                            "the large transfers create a complex graph that's expensive to analyze."
                        ),
                    },
                ]
            },
        },
        # ── Module 2 ──
        {
            "id": "entity-attribution-clustering",
            "title": "Entity Attribution & Clustering",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Who Is Behind the Wallet?",
                        "sub": "Linking addresses to known entities and scammer groups",
                    },
                    {
                        "type": "interactive",
                        "title": "Entity Resolution Lab",
                        "description": (
                            "Given 100 addresses, use clustering heuristics to identify: (1) exchange deposit "
                            "addresses (high volume, reuse pattern), (2) mixer addresses (fixed denominations), "
                            "(3) personal wallets (long-term holding), and (4) scammer hot wallets (rapid turnover)."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What heuristic helps identify an exchange deposit address?",
                        "options": [
                            "It holds tokens for years",
                            "It receives funds from thousands of unique addresses and quickly moves them to a single consolidation wallet",
                            "It only interacts with one dApp",
                            "It has a low transaction count",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Exchange deposit addresses receive from many unique senders and forward funds "
                            "to exchange hot wallets or consolidation addresses. The many-to-one pattern "
                            "with high address reuse is a strong exchange indicator."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Known Scammer Clusters",
                        "description": (
                            "Explore a database of known scammer clusters: wallets linked to the same "
                            "entity through shared funding sources, coordinated transaction timing, and "
                            "behavioral patterns. Learn how to cross-reference clusters across investigations."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the 'common spend' heuristic in wallet clustering?",
                        "options": [
                            "Wallets that hold the same token",
                            "Multiple input addresses used in the same transaction indicate those addresses are controlled by the same entity",
                            "Wallets with similar balances",
                            "Wallets created on the same day",
                        ],
                        "correct": 1,
                        "explanation": (
                            "When a transaction has multiple input addresses, all those addresses must be "
                            "controlled by the same entity (since signing requires each private key). This "
                            "is one of the strongest clustering heuristics available."
                        ),
                    },
                ]
            },
        },
        # ── Module 3 ──
        {
            "id": "mixer-tumbler-detection",
            "title": "Mixer & Tumbler Detection",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Following Through the Fog",
                        "sub": "How mixers work and how to detect their use",
                    },
                    {
                        "type": "interactive",
                        "title": "Tornado Cash Mechanics",
                        "description": (
                            "Visual breakdown of how Tornado Cash works: users deposit fixed amounts (0.1, 1, 10, 100 ETH) "
                            "into a pool. They later withdraw with a zero-knowledge proof, breaking the on-chain link. "
                            "Learn to identify deposit and withdrawal patterns."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the key on-chain signature of Tornado Cash usage?",
                        "options": [
                            "Random transaction amounts",
                            "Deposits and withdrawals in fixed denominations (0.1, 1, 10, 100 ETH) to/from known contract addresses",
                            "Very high gas fees",
                            "Only during business hours",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Tornado Cash pools accept fixed denominations. So if you see deposits of exactly "
                            "1 ETH to a known Tornado address, followed by withdrawals of 1 ETH from different "
                            "addresses, you're seeing mixer activity."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Post-Sanctions Mixer Patterns",
                        "description": (
                            "Since Tornado Cash sanctions, scammers have shifted to: Wasabi/JoinMarket "
                            "(CoinJoin for BTC), cross-chain bridges as pseudo-mixers, and privacy chains "
                            "like Monero. Learn to identify these newer obfuscation techniques."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "Why is using a cross-chain bridge an effective pseudo-mixing technique?",
                        "options": [
                            "Bridges are anonymous",
                            "Bridging breaks the direct on-chain link — the funds emerge on a different chain with a new address",
                            "Bridges change your IP address",
                            "Bridges delete transaction history",
                        ],
                        "correct": 1,
                        "explanation": (
                            "When you bridge from chain A to chain B, the on-chain trail breaks — the source "
                            "chain shows funds sent to the bridge contract, and the destination chain shows "
                            "funds received from the bridge. Correlating the two requires cross-chain analysis."
                        ),
                    },
                ]
            },
        },
        # ── Module 4 ──
        {
            "id": "mev-sandwich-attack-forensics",
            "title": "MEV & Sandwich Attack Forensics",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Invisible Tax",
                        "sub": "Analyzing MEV extraction and identifying the extractors",
                    },
                    {
                        "type": "interactive",
                        "title": "Mempool Analysis Lab",
                        "description": (
                            "Watch the mempool in real-time (simulated). A user submits a swap. Within "
                            "milliseconds, a bot front-runs it, the user's trade executes at a worse price, "
                            "and the bot back-runs for profit. See the exact P&L of each party."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How can you identify a sandwich attack in transaction history?",
                        "options": [
                            "The transactions are in different blocks",
                            "Three transactions in the same block: buy (bot), buy (victim), sell (bot) — with the bot profiting",
                            "The gas is higher than normal",
                            "The token price goes up",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A sandwich attack appears as three transactions in quick succession: bot buys "
                            "(driving price up), victim buys (at inflated price), bot sells (at profit). "
                            "The victim effectively paid the bot's profit in slippage."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Builder & Searcher Identification",
                        "description": (
                            "Analyze MEV-boost relay data to identify known searcher addresses, builder "
                            "patterns, and the relays they use. Learn to associate MEV extraction with "
                            "specific entities."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the role of a 'searcher' in the MEV supply chain?",
                        "options": [
                            "A blockchain validator",
                            "An entity that scans the mempool for profitable opportunities and submits bundles to builders",
                            "A security auditor",
                            "A token creator",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Searchers run algorithms to find MEV opportunities (arbitrage, liquidations, "
                            "sandwiching). They create transaction bundles and submit them to block builders "
                            "who include them in blocks for a share of the profit."
                        ),
                    },
                ]
            },
        },
        # ── Module 5 ──
        {
            "id": "flash-loan-attack-analysis",
            "title": "Flash Loan Attack Analysis",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Borrow Billions, Attack in Seconds",
                        "sub": "Deconstructing the most devastating DeFi exploits",
                    },
                    {
                        "type": "interactive",
                        "title": "Euler Finance Attack Reconstruction",
                        "description": (
                            "Step-by-step reconstruction of the $197M Euler Finance exploit. "
                            "The attacker exploited a donation function to manipulate collateral ratios, "
                            "then used flash loans to amplify the attack. Every transaction decoded."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What was the root cause of the Cream Finance $130M exploit?",
                        "options": [
                            "A phishing attack",
                            "A flash loan combined with a rounding error in the collateral calculation",
                            "A 51% attack",
                            "An oracle failure",
                        ],
                        "correct": 1,
                        "explanation": (
                            "The attacker used a flash loan to deposit a large amount of yUSDVault tokens, "
                            "exploiting a rounding error that inflated their collateral value, allowing "
                            "them to borrow far more than they should have been able to."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Flash Loan Attack Simulator",
                        "description": (
                            "Design your own flash loan attack (educational only!). Choose a vulnerability "
                            "type, select a flash loan amount, and see how the attack unfolds. "
                            "Understand the attack lifecycle: borrow → manipulate → exploit → repay → profit."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "Why are flash loans so dangerous as an attack vector?",
                        "options": [
                            "They're slow",
                            "They let anyone with a smart contract borrow unlimited capital with zero collateral, executing complex attacks in a single transaction",
                            "They require KYC",
                            "They only work on Ethereum",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Flash loans remove the capital barrier for attacks. Previously, an attacker "
                            "needed millions to manipulate a protocol. With flash loans, anyone can borrow "
                            "that amount for the duration of a single transaction."
                        ),
                    },
                ]
            },
        },
        # ── Module 6 ──
        {
            "id": "bridge-cross-chain-exploits",
            "title": "Bridge & Cross-Chain Exploits",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "When Bridges Break",
                        "sub": "The $2.5B bridge exploit epidemic",
                    },
                    {
                        "type": "interactive",
                        "title": "Ronin Bridge Deep Dive",
                        "description": (
                            "The $624M Ronin bridge hack: How the attacker compromised 5 of 9 validator "
                            "keys through a social engineering attack on Sky Mavis employees, then used "
                            "them to approve fraudulent withdrawals. Full timeline and forensic breakdown."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What was unique about the Ronin bridge hack compared to other bridge exploits?",
                        "options": [
                            "It used a flash loan",
                            "It relied on compromised validator keys obtained through social engineering, not a smart contract bug",
                            "It exploited a reentrancy vulnerability",
                            "It happened on Solana",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Unlike most bridge exploits that target smart contract bugs, the Ronin hack "
                            "compromised the validator set itself. The attacker gained control of 5/9 "
                            "validator keys through a spear-phishing campaign."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Message Verification Failure Patterns",
                        "description": (
                            "Compare three bridge exploits with a common theme: Wormhole (signature verification), "
                            "Nomad (message verification initialized to 0x00 allowing any message), and Qubit "
                            "(deprecated function left active). All stem from message/auth verification failures."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "The Nomad bridge was exploited for $190M because:",
                        "options": [
                            "A validator was hacked",
                            "An initialization bug set the trusted root to 0x00, allowing anyone to submit fake messages as valid",
                            "A flash loan was used",
                            "The bridge had no liquidity",
                        ],
                        "correct": 1,
                        "explanation": (
                            "During a routine upgrade, the Nomad team initialized the 'trusted root' to 0x00. "
                            "This meant any message was considered valid. Once one person demonstrated the "
                            "exploit, hundreds copied the transaction and drained the bridge."
                        ),
                    },
                ]
            },
        },
        # ── Module 7 ──
        {
            "id": "governance-attack-detection",
            "title": "Governance Attack Detection",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Hijacking Democracy",
                        "sub": "How attackers manipulate DAOs and governance systems",
                    },
                    {
                        "type": "interactive",
                        "title": "Flash Loan Governance Attack Simulator",
                        "description": (
                            "Watch how an attacker borrows governance tokens via flash loan, votes to "
                            "drain the treasury, and repays the loan — all in one transaction. The DAO "
                            "never saw it coming. Learn why snapshot-based voting is vulnerable."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How does a flash loan governance attack work?",
                        "options": [
                            "By bribing voters",
                            "By borrowing governance tokens for one block, voting on a malicious proposal, and repaying — all in one transaction",
                            "By hacking the voting website",
                            "By creating fake proposals",
                        ],
                        "correct": 1,
                        "explanation": (
                            "If governance power is based on token holdings at a snapshot, an attacker can "
                            "flash-loan a massive amount of tokens, vote to drain the treasury or change "
                            "protocol parameters, and return the loan — all atomic."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Proposal Analysis Framework",
                        "description": (
                            "Learn to audit governance proposals: (1) Check proposer address history, "
                            "(2) Analyze the proposed changes for malicious intent, (3) Verify the "
                            "voting power source isn't a flash loan, (4) Check timelock implementation."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the best defense against flash loan governance attacks?",
                        "options": [
                            "No defense is possible",
                            "Requiring voting power to be held for a minimum period (e.g., 1 block before proposal) and timelocks on execution",
                            "Limiting proposals to one per day",
                            "Using a different blockchain",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A minimum holding period prevents flash-loaned tokens from being used to vote. "
                            "Timelocks give the community time to review and potentially veto malicious "
                            "proposals before they execute."
                        ),
                    },
                ]
            },
        },
        # ── Module 8 ──
        {
            "id": "advanced-smart-contract-auditing",
            "title": "Advanced Smart Contract Auditing",
            "type": "interactive",
            "duration": 22,
            "xp": 350,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Reading Code Like a Pro",
                        "sub": "Manual review techniques, fuzzing, and formal verification",
                    },
                    {
                        "type": "interactive",
                        "title": "Manual Audit Workflow",
                        "description": (
                            "Walk through a professional audit: (1) Read the spec and documentation, "
                            "(2) Create a threat model, (3) Line-by-line code review with a checklist, "
                            "(4) Write Proof of Concept exploits for each finding, (5) Categorize by severity."
                        ),
                    },
                    {
                        "type": "code-inspection",
                        "title": "Find All 5 Vulnerabilities",
                        "description": (
                            "A deliberately buggy ERC-20 contract contains: reentrancy, unchecked return value, "
                            "integer overflow (Solidity < 0.8), incorrect access control, and front-running "
                            "vulnerability. You have 10 minutes to find all 5."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is fuzz testing in the context of smart contract security?",
                        "options": [
                            "Testing on random blockchains",
                            "Automatically generating random/malformed inputs to find edge cases that crash or exploit the contract",
                            "Testing only the frontend",
                            "Testing with very slow transactions",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Fuzzing tools like Foundry's invariant tests or Echidna generate millions of "
                            "random inputs and call sequences, searching for state violations. This catches "
                            "edge cases that manual review often misses."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What does 'formal verification' prove about a smart contract?",
                        "options": [
                            "That the contract has no bugs",
                            "That specific mathematical properties hold for all possible inputs (not that it has zero bugs)",
                            "That the contract is profitable",
                            "That the contract will never be hacked",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Formal verification proves that specified invariants (e.g., 'total supply never "
                            "exceeds MAX_SUPPLY') hold for all possible states. It does NOT prove the contract "
                            "does what it's supposed to do or that there are no bugs in unspecified properties."
                        ),
                    },
                ]
            },
        },
        # ── Module 9 ──
        {
            "id": "defi-protocol-risk-assessment",
            "title": "DeFi Protocol Risk Assessment",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Before You Deposit",
                        "sub": "Systematic risk evaluation for DeFi protocols",
                    },
                    {
                        "type": "interactive",
                        "title": "TVL Analysis Framework",
                        "description": (
                            "Learn to analyze Total Value Locked: (1) Is TVL organic or incentive-driven? "
                            "(2) What's the TVL concentration (one whale vs distributed)? (3) How sticky is "
                            "the TVL (withdrawal penalties, lock periods)? (4) TVL trend over 30/90 days."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A DeFi protocol's TVL dropped from $500M to $50M in one week. The most likely cause is:",
                        "options": [
                            "Normal market fluctuations",
                            "A security incident, exploit, or mass exodus due to a vulnerability disclosure",
                            "Everyone went on vacation",
                            "The protocol upgraded",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A 90% TVL drop in a week is almost always a sign of catastrophic failure: "
                            "an exploit, a critical vulnerability disclosure, or a bank run after bad news. "
                            "Investigate immediately."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Oracle Dependency Mapping",
                        "description": (
                            "Map a DeFi protocol's oracle dependencies: Which price feeds does it use? "
                            "Chainlink? A custom TWAP? A single DEX pool? If the oracle can be manipulated, "
                            "every product relying on it is at risk. Build a dependency graph."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'liquidation cascade' and why is it dangerous?",
                        "options": [
                            "A waterfall in a DeFi UI",
                            "When falling prices trigger liquidations, which push prices lower, triggering more liquidations in a death spiral",
                            "A type of token launch",
                            "A governance proposal",
                        ],
                        "correct": 1,
                        "explanation": (
                            "In lending protocols, falling collateral values trigger liquidations. The liquidated "
                            "collateral is sold, pushing prices lower, triggering more liquidations. In extreme "
                            "cases, this can drain an entire lending market (see: Black Thursday on MakerDAO)."
                        ),
                    },
                ]
            },
        },
        # ── Module 10 ──
        {
            "id": "dark-web-offchain-intelligence",
            "title": "Dark Web & Off-Chain Intelligence",
            "type": "interactive",
            "duration": 22,
            "xp": 330,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Beyond the Blockchain",
                        "sub": "OSINT and dark web intelligence for crypto investigations",
                    },
                    {
                        "type": "interactive",
                        "title": "OSINT Workflow for Crypto",
                        "description": (
                            "Build an intelligence gathering pipeline: (1) Social media analysis (Twitter, Telegram, Discord), "
                            "(2) Domain/WHOIS lookups, (3) GitHub commit analysis, (4) Darknet forum monitoring, "
                            "(5) Threat intelligence feeds (TRM, Elliptic, Chainalysis)."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What OSINT technique can reveal a scammer's identity from their token launch?",
                        "options": [
                            "Checking the token price",
                            "Analyzing the deployer wallet's funding source — if it came from a CEX with KYC, law enforcement can subpoena records",
                            "Reading the token website",
                            "Following their Twitter",
                        ],
                        "correct": 1,
                        "explanation": (
                            "If the deployer wallet was funded from a centralized exchange (Coinbase, Binance, etc.), "
                            "that exchange has KYC records. Law enforcement can subpoena those records to identify "
                            "the scammer. This is why sophisticated criminals use mixers or privacy coins."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Threat Intelligence Feed Integration",
                        "description": (
                            "Learn to consume and correlate threat intel: sanctioned address lists (OFAC SDN), "
                            "known scammer databases, phishing URL feeds, and blockchain analytics alerts. "
                            "Set up automated monitoring for your watchlist addresses."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the value of darknet forum monitoring for crypto investigations?",
                        "options": [
                            "None — it's all fake",
                            "Scammers often sell tools, discuss techniques, and coordinate attacks on darknet forums, providing early warning intelligence",
                            "It's only for drugs",
                            "It's too hard to access",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Darknet forums (Dread, BreachForums, etc.) are where scammers buy and sell "
                            "drainers, discuss new exploit techniques, and recruit for coordinated attacks. "
                            "Monitoring these provides actionable early warning intelligence."
                        ),
                    },
                ]
            },
        },
        # ── Module 11 ──
        {
            "id": "regulatory-compliance-landscape",
            "title": "Regulatory & Compliance Landscape",
            "type": "interactive",
            "duration": 20,
            "xp": 300,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Law Comes for Crypto",
                        "sub": "Understanding the regulatory environment and its impact",
                    },
                    {
                        "type": "interactive",
                        "title": "OFAC Compliance in DeFi",
                        "description": (
                            "Explore the OFAC SDN list and its impact on DeFi. Learn which addresses are "
                            "sanctioned, how protocols implement compliance (blocking, geo-restrictions), "
                            "and the controversy around censorship-resistant protocols."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What was the impact of OFAC sanctioning Tornado Cash?",
                        "options": [
                            "Nothing changed",
                            "It became illegal for US persons to interact with Tornado Cash; many protocols blacklisted sanctioned addresses; it sparked debate about privacy vs compliance",
                            "Tornado Cash was shut down completely",
                            "Only Tornado Cash's website was affected",
                        ],
                        "correct": 1,
                        "explanation": (
                            "The OFAC sanctions made it illegal for US persons to use Tornado Cash. "
                            "Major protocols and RPC providers began blocking sanctioned addresses. "
                            "It created a precedent that immutable smart contracts can be sanctioned."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "KYC/AML in DeFi: The Tension",
                        "description": (
                            "Map the regulatory landscape: which jurisdictions require KYC for what activities? "
                            "Compare the FATF Travel Rule, EU MiCA regulation, US FinCEN guidance, and "
                            "how they affect DeFi protocols differently from CeFi."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the 'Travel Rule' in the context of crypto?",
                        "options": [
                            "A rule about travel expenses",
                            "A FATF requirement that crypto transactions above a threshold must include sender and receiver identity information",
                            "A rule about gas fees",
                            "A rule about blockchain speed",
                        ],
                        "correct": 1,
                        "explanation": (
                            "The FATF Travel Rule requires Virtual Asset Service Providers (VASPs) to share "
                            "sender and receiver information for transactions above certain thresholds, "
                            "similar to wire transfer rules in traditional banking."
                        ),
                    },
                ]
            },
        },
        # ── Module 12: Capstone ──
        {
            "id": "capstone-threat-intelligence-report",
            "title": "Capstone: Full Threat Intelligence Report",
            "type": "exam",
            "duration": 24,
            "xp": 420,
            "passing_score": 90,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Forensic Analyst Certification",
                        "sub": "Capstone exam — produce a professional threat intelligence report",
                    },
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
                    {
                        "type": "quiz",
                        "question": "Which component is MOST critical for a professional threat intelligence report?",
                        "options": [
                            "The font choice and layout",
                            "A clear executive summary, evidence-based findings with on-chain proof, and actionable recommendations",
                            "A list of all transactions",
                            "Screenshots of social media posts",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Professional reports must be actionable. Decision-makers need a clear summary, "
                            "evidence-backed findings (transaction hashes, timestamps, addresses), and specific "
                            "recommendations. Raw data without analysis isn't intelligence."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How can you trace funds that went through a mixer?",
                        "options": [
                            "You can't — mixers make tracing impossible",
                            "By analyzing deposit/withdrawal timing, amounts, and behavior patterns — mixers obscure but don't perfectly anonymize",
                            "Mixers automatically reverse the transactions",
                            "By asking the mixer operators",
                        ],
                        "correct": 1,
                        "explanation": (
                            "While mixers break the direct on-chain link, timing analysis, amount correlation, "
                            "and behavioral patterns can still provide probabilistic attribution. It's harder "
                            "but not impossible — especially when combined with off-chain intelligence."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What does a 'transaction graph' reveal about an attack?",
                        "options": [
                            "Just the gas prices",
                            "The complete flow of funds: source, intermediate hops, destinations — revealing the attacker's operational patterns and infrastructure",
                            "Only the total amount stolen",
                            "Nothing useful",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A transaction graph maps every movement of funds. It reveals the attacker's "
                            "operational security level (did they use mixers? bridges? CEX deposits?), "
                            "their infrastructure (funding sources, consolidation patterns), and potential "
                            "attribution points (CEX deposit = KYC record)."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the most important ethical consideration when publishing a threat intelligence report?",
                        "options": [
                            "Getting more followers",
                            "Protecting victim privacy, verifying facts before publication, and not endangering ongoing investigations",
                            "Publishing as fast as possible",
                            "Making the report go viral",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Threat intelligence reports must balance transparency with responsibility. "
                            "Revealing victim identities, sharing unverified claims, or compromising active "
                            "law enforcement investigations can cause real harm."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "You've completed ALL three courses in Scam School. What is the single most important lesson?",
                        "options": [
                            "Buy every new token",
                            "Trust, but verify — always do your own research, use multiple tools, and never invest what you can't lose",
                            "Follow influencers blindly",
                            "Trading is the only way to make money",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Congratulations on completing Scam School! The most important lesson is that "
                            "crypto safety is a continuous practice. There is no single tool, no guarantee, "
                            "and no shortcut. Trust nothing, verify everything, and always protect yourself."
                        ),
                    },
                ]
            },
        },
    ],
}
