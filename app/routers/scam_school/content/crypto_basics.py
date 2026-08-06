"""
Scam School — Beginner Course: "Crypto Security 101"
12 modules covering blockchain fundamentals, wallet safety, and scam awareness.
"""

from __future__ import annotations

crypto_basics = {
    "id": "crypto-basics",
    "title": "Crypto Security 101",
    "subtitle": "The foundation every crypto investor needs to survive",
    "description": (
        "Learn blockchain fundamentals, wallet safety, how to recognize common scams, "
        "and build a security routine that keeps your funds safe. No prior knowledge required."
    ),
    "difficulty": "beginner",
    "duration_minutes": 150,
    "lessons_count": 12,
    "xp_reward": 1500,
    "badge_reward": "crypto-cadet",
    "color": "#6366f1",
    "icon": "shield",
    "modules": [
        # ── Module 1 ──
        {
            "id": "what-is-a-rug-pull",
            "title": "What Is a Rug Pull?",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Rug Pull Explained",
                        "sub": "Understanding the most common crypto scam",
                    },
                    {
                        "type": "interactive",
                        "title": "Rug Pull Timeline",
                        "description": (
                            "Watch how a rug pull unfolds: from the launch hype, through the price pump, "
                            "to the moment liquidity vanishes. Interactive timeline with real examples."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What distinguishes a rug pull from a normal price drop?",
                        "options": [
                            "The price goes down",
                            "The developer intentionally drains liquidity or sells all tokens",
                            "The token has low volume",
                            "The website goes offline",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A rug pull is specifically when the project creators intentionally steal funds — "
                            "either by draining the liquidity pool or dumping their entire token supply on investors."
                        ),
                    },
                    {
                        "type": "comparison",
                        "left": {
                            "title": "Slow Rug",
                            "examples": ["Luna/UST Collapse", "Iron Finance"],
                            "traits": [
                                "Gradual, hard to detect",
                                "Often disguised as 'market conditions'",
                                "Can last weeks or months",
                                "Devs slowly exit while maintaining appearances",
                            ],
                        },
                        "right": {
                            "title": "Instant Rug",
                            "examples": ["Squid Game Token", "Baller Ape"],
                            "traits": [
                                "Liquidity pulled in seconds",
                                "No warning signs to casual observers",
                                "Often within 24 hours of launch",
                                "Social media disappears immediately after",
                            ],
                        },
                    },
                    {
                        "type": "quiz",
                        "question": "The Squid Game token rug pull was notable because:",
                        "options": [
                            "It had a small market cap",
                            "It used a popular Netflix show's name and trapped buyers with a sell restriction",
                            "It lasted for years",
                            "It only affected one person",
                        ],
                        "correct": 1,
                        "explanation": (
                            "The Squid Game token used a 'anti-dumping mechanism' that prevented anyone from selling, "
                            "while the developers drained $3.3M from the liquidity pool."
                        ),
                    },
                ]
            },
        },
        # ── Module 2 ──
        {
            "id": "how-blockchains-work",
            "title": "How Blockchains Work",
            "type": "interactive",
            "duration": 14,
            "xp": 140,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Digital Ledger",
                        "sub": "Understanding blockchain technology in 5 minutes",
                    },
                    {
                        "type": "interactive-block",
                        "title": "Build a Chain",
                        "description": (
                            "Drag blocks to form a blockchain. Each block contains transactions and the hash of the "
                            "previous block. Change one transaction and watch every subsequent hash break — "
                            "that's immutability in action."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What makes a blockchain immutable?",
                        "options": [
                            "It uses a database",
                            "Each block hashes the previous block, creating a tamper-evident chain",
                            "It is stored on one computer",
                            "It uses password protection",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Each block contains the hash of the previous block. Change one transaction and every "
                            "subsequent hash changes, making tampering immediately obvious to the network."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Why 'Code Is Law' Matters for Safety",
                        "description": (
                            "Smart contracts execute exactly as written — no exceptions, no appeals. A malicious "
                            "contract will do exactly what its code says. Understanding this is the first step "
                            "to protecting yourself."
                        ),
                    },
                    {
                        "type": "simulation",
                        "title": "Hash Visualization",
                        "description": (
                            "Type any text and see its SHA-256 hash change in real-time. Even one letter difference "
                            "creates a completely different hash. This is the cryptographic foundation of blockchain security."
                        ),
                    },
                ]
            },
        },
        # ── Module 3 ──
        {
            "id": "wallet-safety-fundamentals",
            "title": "Wallet Safety Fundamentals",
            "type": "interactive",
            "duration": 14,
            "xp": 140,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Your Crypto Wallet",
                        "sub": "Not actually a wallet — it's a keychain",
                    },
                    {
                        "type": "interactive-keygen",
                        "title": "Generate a Key Pair",
                        "description": (
                            "Watch as a random private key is generated and its corresponding public address is "
                            "derived. Click to generate a new one. Your private key IS your money."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What should you NEVER do with your seed phrase?",
                        "options": [
                            "Store it in a hardware wallet",
                            "Share it with anyone, screenshot it, or store it online",
                            "Write it on paper and keep in a safe",
                            "Use it to restore your wallet",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Your seed phrase is the master key to ALL your funds. Anyone with it controls everything. "
                            "NEVER share it, screenshot it, store it in cloud storage, or type it into websites."
                        ),
                    },
                    {
                        "type": "comparison",
                        "left": {
                            "title": "Hot Wallet",
                            "examples": ["MetaMask", "Phantom", "Trust Wallet"],
                            "traits": [
                                "Connected to the internet",
                                "Convenient for daily use",
                                "Vulnerable to malware and phishing",
                                "Keep small amounts only",
                            ],
                        },
                        "right": {
                            "title": "Cold Wallet",
                            "examples": ["Ledger", "Trezor", "Keystone"],
                            "traits": [
                                "Offline, never connected to internet",
                                "Sign transactions on the device",
                                "Much harder to hack",
                                "Store large amounts here",
                            ],
                        },
                    },
                    {
                        "type": "drag-drop",
                        "title": "Secure vs Insecure Storage",
                        "items": [
                            {"text": "Hardware wallet (Ledger/Trezor)", "zone": "secure"},
                            {"text": "Screenshot saved on phone", "zone": "insecure"},
                            {"text": "Encrypted USB in a safe", "zone": "secure"},
                            {"text": "Email to yourself", "zone": "insecure"},
                            {"text": "Paper backup in fireproof safe", "zone": "secure"},
                            {"text": "Google Docs / cloud storage", "zone": "insecure"},
                        ],
                    },
                ]
            },
        },
        # ── Module 4 ──
        {
            "id": "recognizing-common-scams",
            "title": "Recognizing Common Scams",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Hall of Shame",
                        "sub": "Common crypto scams and how to spot them instantly",
                    },
                    {
                        "type": "phishing-sim",
                        "title": "Phishing Link Detector",
                        "description": (
                            "Six wallet-connect popups appear. Three are real dApps, three are phishing clones. "
                            "Can you tell the difference? Learn the subtle tells: incorrect URLs, missing SSL, "
                            "typo-squatted domains."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A random account DMs you: 'Congrats! You won 10 ETH in our airdrop. Just connect your wallet and approve this transaction to claim.' What do you do?",
                        "options": [
                            "Connect and claim immediately before it expires",
                            "Click the link to check if it looks legitimate",
                            "Ignore and block — real projects never DM you for wallet connections",
                            "Connect but use an empty wallet to test",
                        ],
                        "correct": 2,
                        "explanation": (
                            "Real projects don't DM you about airdrops. The transaction you 'approve' is actually "
                            "giving the scammer permission to drain your wallet. Never connect to unsolicited links."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "The 'Send 1 ETH, Get 2 Back' Scam",
                        "description": (
                            "A classic: a fake celebrity account or hacked verified account posts a 'giveaway' address. "
                            "Interactive scenario showing how these work, why people fall for them, and red flags to watch for."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the most common vector for impersonation scams?",
                        "options": [
                            "Email only",
                            "Fake verified accounts on Twitter/X and Telegram impersonating projects/devs",
                            "Physical mail",
                            "Phone calls from 'crypto support'",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Scammers create accounts that look identical to real project accounts, often with one "
                            "character difference in the handle. They then DM followers with 'support' or 'giveaway' offers."
                        ),
                    },
                ]
            },
        },
        # ── Module 5 ──
        {
            "id": "understanding-token-contracts",
            "title": "Understanding Token Contracts",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "What Is a Token Address?",
                        "sub": "Reading smart contracts without being a developer",
                    },
                    {
                        "type": "interactive",
                        "title": "Explorer Walkthrough",
                        "description": (
                            "A guided tour of Etherscan/Solscan. Learn to find: token address, contract creator, "
                            "holder distribution, transaction history, and the critical 'Contract' tab."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What does 'verified contract' mean on Etherscan?",
                        "options": [
                            "The token has been audited",
                            "The source code is publicly visible and matches the deployed bytecode",
                            "The token is guaranteed safe",
                            "The contract has been approved by Vitalik",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Verified means the human-readable Solidity/Vyper code matches the bytecode on-chain. "
                            "It lets you inspect the code, but it does NOT mean the code is safe — it could still "
                            "contain honeypots or backdoors."
                        ),
                    },
                    {
                        "type": "code-hunt",
                        "title": "Unverified vs Verified",
                        "description": (
                            "Two contract pages side-by-side: one verified (you can see every function), "
                            "one unverified (only bytecode). Learn why unverified = extreme risk."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "If a token contract is NOT verified, you should:",
                        "options": [
                            "Buy anyway, unverified is common",
                            "Treat it as extremely high risk — you cannot see what the code does",
                            "Only buy on weekends",
                            "Trust the website description of the token",
                        ],
                        "correct": 1,
                        "explanation": (
                            "An unverified contract hides its source code. The dev could have put anything in there — "
                            "honeypots, infinite mint functions, wallet blacklists. Always verify the contract."
                        ),
                    },
                ]
            },
        },
        # ── Module 6 ──
        {
            "id": "liquidity-and-market-caps",
            "title": "Liquidity & Market Caps",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Why Liquidity Matters",
                        "sub": "The fuel that lets you buy AND sell",
                    },
                    {
                        "type": "interactive",
                        "title": "Liquidity Pool Simulator",
                        "description": (
                            "Adjust the amount of liquidity in a pool. Watch what happens to slippage when you try to "
                            "sell $100 vs $1,000 vs $10,000. Low liquidity = high slippage = you lose money."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A token has a $1M market cap but only $5K in liquidity. What does this mean?",
                        "options": [
                            "The token is very stable",
                            "You won't be able to sell any meaningful amount without massive price impact",
                            "It's a great investment opportunity",
                            "The token has strong community backing",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Market cap = price × supply. High market cap with low liquidity means the price would "
                            "collapse the moment anyone tries to sell. It's a mirage — your 'value' can't be realized."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Using DexScreener to Check Liquidity",
                        "description": (
                            "Live demo: analyze a real token on DexScreener. Find liquidity, check if LP is locked, "
                            "see 24h volume vs liquidity ratio, and identify dangerous patterns."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the minimum liquidity-to-market-cap ratio you should look for?",
                        "options": [
                            "0.1% is fine",
                            "At least 5-10% — ideally locked",
                            "Liquidity doesn't matter",
                            "50% or nothing",
                        ],
                        "correct": 1,
                        "explanation": (
                            "A healthy project typically has at least 5-10% of its market cap in liquidity. "
                            "Anything below 2% is a red flag. Always check if the LP is locked or burned."
                        ),
                    },
                ]
            },
        },
        # ── Module 7 ──
        {
            "id": "introduction-to-rugcheck-tools",
            "title": "Introduction to RugCheck Tools",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Your Scam Detection Arsenal",
                        "sub": "Free tools that save you money",
                    },
                    {
                        "type": "interactive",
                        "title": "Scanner Roundup",
                        "description": (
                            "Overview of free scanners: RugMunch, GoPlus, TokenSniffer, Honeypot.is, "
                            "DexScreener Safety Check. Live demo of each tool analyzing the same contract. "
                            "See which red flags each tool catches."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "How does RugMunch's scanner help protect you?",
                        "options": [
                            "It guarantees tokens are safe",
                            "It analyzes contracts for honeypots, ownership risks, and 20+ other security factors",
                            "It trades tokens for you",
                            "It only checks token prices",
                        ],
                        "correct": 1,
                        "explanation": (
                            "RugMunch runs a comprehensive analysis: honeypot detection, ownership check, "
                            "mint function audit, liquidity lock verification, holder distribution analysis, "
                            "and more. It gives you a risk score to make informed decisions."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Hands-On: Scan a Token",
                        "description": (
                            "Paste a real contract address into a simulated scanner. Watch the analysis run: "
                            "contract verification, honeypot check, owner privileges, holder concentration, "
                            "liquidity analysis. Interpret each result."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "You scan a token and it passes one scanner but fails another. You should:",
                        "options": [
                            "Trust the one that says it's safe",
                            "Investigate why they disagree — the failing scanner may have caught something",
                            "Buy immediately before others find out",
                            "Ignore all scanners, they're unreliable",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Different scanners check different things. A disagreement is a red flag that warrants "
                            "deeper investigation. Never rely on a single source."
                        ),
                    },
                ]
            },
        },
        # ── Module 8 ──
        {
            "id": "red-flags-new-token-launches",
            "title": "Red Flags in New Token Launches",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Launch Day Warning Signs",
                        "sub": "Spot a scam before you ape in",
                    },
                    {
                        "type": "interactive",
                        "title": "Launch Simulator: Good vs Bad",
                        "description": (
                            "Two tokens launch simultaneously. Token A: verified contract, renounced ownership, "
                            "locked LP, organic holder growth. Token B: unverified, 80% insider wallets, "
                            "sniper bots in first block, unlocked LP. Which would you buy?"
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A new token has 50 wallets that were all funded by the same address 30 minutes before launch. This indicates:",
                        "options": [
                            "A viral launch with real demand",
                            "A coordinated bundler or insider group preparing to dump",
                            "A community airdrop",
                            "Normal launch mechanics",
                        ],
                        "correct": 1,
                        "explanation": (
                            "When dozens of wallets are funded by one source right before launch, it's a bundler "
                            "attack. These wallets will buy at launch to pump the price, then dump on retail buyers."
                        ),
                    },
                    {
                        "type": "red-flag-checklist",
                        "title": "Launch Red Flag Checklist",
                        "items": [
                            {"flag": "Deployer has rug-pulled before", "risk": "Serial scammer — highest risk"},
                            {"flag": "Contract unverified at launch", "risk": "Can't see what the code does"},
                            {"flag": "Ownership not renounced", "risk": "Dev can mint, pause, or blacklist"},
                            {"flag": "Liquidity not locked/burned", "risk": "Dev can drain the pool anytime"},
                            {"flag": "80%+ supply in one wallet", "risk": "Single dump could crash price"},
                            {"flag": "No social media or website", "risk": "No accountability, ghost project"},
                        ],
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'sniper bot' in the context of token launches?",
                        "options": [
                            "A bot that posts on social media",
                            "An automated bot that buys milliseconds after liquidity is added, before humans can",
                            "A type of security scanner",
                            "A bot that moderates Discord",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Sniper bots monitor for new liquidity pool creation and buy within the same block. "
                            "They get the lowest possible price and sell to you at a profit seconds later."
                        ),
                    },
                ]
            },
        },
        # ── Module 9 ──
        {
            "id": "social-engineering-in-crypto",
            "title": "Social Engineering in Crypto",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "The Human Exploit",
                        "sub": "When scammers hack your mind, not your wallet",
                    },
                    {
                        "type": "phishing-sim",
                        "title": "Choose Your Own Adventure: Discord Scam",
                        "description": (
                            "You join a new token's Discord. A 'moderator' DMs you with an exclusive pre-sale link "
                            "that requires connecting your wallet. Walk through the decision tree. Each choice "
                            "reveals a new red flag you should have noticed."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "A 'support agent' in Telegram asks you to verify your wallet by sharing your screen. You should:",
                        "options": [
                            "Share your screen, they're support",
                            "Never share your screen or any wallet info — real support doesn't ask",
                            "Only share if they're from a verified account",
                            "Send a small transaction to prove it's your wallet",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Real crypto support will NEVER ask you to share your screen, seed phrase, "
                            "private key, or to connect your wallet. This is 100% a scam, regardless of "
                            "how authentic the 'support agent' seems."
                        ),
                    },
                    {
                        "type": "drag-drop",
                        "title": "Real vs Fake Announcement",
                        "items": [
                            {"text": "Posted by verified account with checkmark", "zone": "real"},
                            {"text": "Urgency: 'Limited time — act NOW or miss out!'", "zone": "fake"},
                            {"text": "Links to official domain (rugmunch.io)", "zone": "real"},
                            {"text": "Asks for private key or seed phrase", "zone": "fake"},
                            {"text": "Announced on multiple official channels simultaneously", "zone": "real"},
                            {"text": "Only shared in DM, not in public announcement channels", "zone": "fake"},
                        ],
                    },
                    {
                        "type": "quiz",
                        "question": "What is a 'pump and dump' group?",
                        "options": [
                            "A legitimate trading community",
                            "A group that coordinates buying to inflate a price, then sells on newcomers",
                            "A technical analysis group",
                            "A mining pool",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Pump and dump groups coordinate mass buying of a low-cap token to spike its price, "
                            "then sell into the FOMO. Late joiners are left holding worthless bags."
                        ),
                    },
                ]
            },
        },
        # ── Module 10 ──
        {
            "id": "safe-trading-practices",
            "title": "Safe Trading Practices",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Trade Smart, Not Reckless",
                        "sub": "Settings and strategies that protect your capital",
                    },
                    {
                        "type": "interactive",
                        "title": "Slippage Simulator",
                        "description": (
                            "Adjust slippage from 0.5% to 50%. See how different settings affect your trade. "
                            "Learn why 0.5-2% is standard and why high slippage = you're getting front-run."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "Setting your slippage to 49% is dangerous because:",
                        "options": [
                            "It costs more gas",
                            "MEV bots can sandwich your trade, extracting maximum value at your expense",
                            "The transaction will fail",
                            "It makes the trade slower",
                        ],
                        "correct": 1,
                        "explanation": (
                            "High slippage tolerance allows MEV bots to profit at your expense through sandwich "
                            "attacks. The bot buys before you (raising the price), your trade executes at the "
                            "worst price, then the bot sells after (lowering it back)."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "DEX Aggregators vs Direct Swaps",
                        "description": (
                            "Compare a $1000 swap on Uniswap direct vs 1inch vs Jupiter. Aggregators split your "
                            "trade across multiple pools for better rates and less slippage. Plus built-in MEV protection."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is the #1 rule of safe crypto trading?",
                        "options": [
                            "Always follow crypto influencers",
                            "Never invest more than you can afford to lose, and never FOMO",
                            "Always buy at the all-time high",
                            "Trade with 100x leverage for maximum gains",
                        ],
                        "correct": 1,
                        "explanation": (
                            "FOMO (Fear Of Missing Out) is the crypto investor's worst enemy. Set a budget, "
                            "do your research, and never invest money you can't afford to lose entirely."
                        ),
                    },
                ]
            },
        },
        # ── Module 11 ──
        {
            "id": "understanding-gas-and-fees",
            "title": "Understanding Gas & Transaction Fees",
            "type": "interactive",
            "duration": 12,
            "xp": 120,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Why Transactions Cost Money",
                        "sub": "Gas: the fuel that powers every blockchain action",
                    },
                    {
                        "type": "gas-simulator",
                        "title": "Gas Fee Simulator",
                        "description": (
                            "Adjust network congestion and transaction complexity. Watch how gas fees change in "
                            "real-time. Compare Ethereum ($5-50), Base ($0.01-0.10), and Solana ($0.0002) costs."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What happens if you set gas too low on a pending transaction?",
                        "options": [
                            "The transaction is free",
                            "It gets stuck pending indefinitely — miners/validators ignore it",
                            "You get a refund",
                            "It processes faster on the cheaper network",
                        ],
                        "correct": 1,
                        "explanation": (
                            "If gas is too low, validators won't include your transaction in any block. "
                            "It can sit pending for hours or days. You may need to 'cancel' it by sending "
                            "a 0 ETH transaction with the same nonce and higher gas."
                        ),
                    },
                    {
                        "type": "interactive",
                        "title": "Failed Transactions: Why They Still Cost Gas",
                        "description": (
                            "A swap reverts because of insufficient output. You still paid gas. Learn why: "
                            "the validators still did the computational work of processing (and rejecting) your transaction."
                        ),
                    },
                    {
                        "type": "quiz",
                        "question": "What is front-running in the context of gas?",
                        "options": [
                            "Running nodes at the front of the network",
                            "A bot seeing your pending transaction and submitting the same trade with higher gas",
                            "Buying gas tokens in advance",
                            "The first transaction in a block",
                        ],
                        "correct": 1,
                        "explanation": (
                            "Front-runners monitor the mempool for profitable trades. When they see yours, "
                            "they submit an identical transaction with higher gas to get executed first, "
                            "profiting at your expense."
                        ),
                    },
                ]
            },
        },
        # ── Module 12: Review & Exam ──
        {
            "id": "building-your-security-routine",
            "title": "Building Your Security Routine",
            "type": "exam",
            "duration": 14,
            "xp": 140,
            "passing_score": 80,
            "content": {
                "slides": [
                    {
                        "type": "title",
                        "text": "Final Exam: Crypto Security 101",
                        "sub": "15 questions · 80% to pass · Crypto Cadet badge on completion",
                    },
                    {
                        "type": "checklist",
                        "title": "Your Daily/Weekly Security Routine",
                        "items": [
                            "Bookmark official project URLs — never Google search for links",
                            "Check token contracts on RugMunch scanner before any trade",
                            "Verify contract addresses against official sources",
                            "Check liquidity lock status on DexScreener or RugCheck",
                            "Follow security researchers, not hype influencers",
                            "Keep 90% of funds in cold storage, 10% in hot wallet for trading",
                            "Always use hardware wallet confirmation for significant transactions",
                            "Review token approvals monthly and revoke unused ones",
                        ],
                    },
                    {
                        "type": "quiz",
                        "question": "What is the primary purpose of a blockchain?",
                        "options": [
                            "To mine Bitcoin",
                            "To store decentralized, tamper-proof records",
                            "To replace all banks",
                            "To create memes",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "Where is the SAFEST place to store large amounts of crypto?",
                        "options": ["Exchange wallet", "Mobile app", "Hardware wallet", "Browser extension"],
                        "correct": 2,
                    },
                    {
                        "type": "quiz",
                        "question": "A token has 80% insider allocation and unlocked liquidity. Your action:",
                        "options": [
                            "Buy — insiders are probably bullish",
                            "Avoid — insiders can dump and/or drain liquidity at any time",
                            "Buy a small amount to test",
                            "Wait for the price to go up first",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What is the difference between a verified and unverified contract?",
                        "options": [
                            "Verified is faster",
                            "Verified source code is publicly visible and auditable; unverified hides the code",
                            "Unverified costs less gas",
                            "They are the same thing",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "A 'dev' DMs you with a pre-sale link. You should:",
                        "options": [
                            "Click it — you don't want to miss out",
                            "Check official announcement channels first and NEVER click DM links",
                            "Ask them to prove their identity in DM",
                            "Send a tiny amount as a test",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What does it mean if a token has $1M market cap but only $2K in liquidity?",
                        "options": [
                            "The token is fairly valued",
                            "The market cap is an illusion — you can't sell without crashing the price",
                            "It's a great buying opportunity",
                            "Liquidity will automatically increase",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "What is the function of a DEX aggregator like 1inch or Jupiter?",
                        "options": [
                            "It creates new tokens",
                            "It splits your trade across multiple liquidity sources for the best rate",
                            "It stores your tokens securely",
                            "It generates wallet addresses",
                        ],
                        "correct": 1,
                    },
                    {
                        "type": "quiz",
                        "question": "Your seed phrase is:",
                        "options": [
                            "Your wallet's public address",
                            "The master key that can restore your entire wallet — protect it at all costs",
                            "A password you can reset",
                            "Only needed for staking",
                        ],
                        "correct": 1,
                    },
                ]
            },
        },
    ],
}
