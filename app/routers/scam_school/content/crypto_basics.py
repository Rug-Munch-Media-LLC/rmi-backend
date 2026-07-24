crypto_basics = {
        "id": "crypto-basics",
        "title": "Crypto Basics",
        "subtitle": "The foundation every investor needs",
        "description": "Learn how blockchains work, what wallets are, how to read explorers, and why decentralization matters. No prior knowledge required.",
        "difficulty": "beginner",
        "duration_minutes": 45,
        "lessons_count": 6,
        "xp_reward": 500,
        "badge_reward": "crypto-cadet",
        "color": "#6366f1",
        "icon": "shield",
        "modules": [
            {
                "id": "what-is-blockchain",
                "title": "What is a Blockchain?",
                "type": "interactive",
                "duration": 8,
                "xp": 80,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Digital Ledger",
                            "sub": "Understanding blockchain in 3 minutes",
                        },
                        {
                            "type": "interactive-block",
                            "title": "Build a Chain",
                            "description": "Drag blocks to form a chain. Each block contains transactions. Change one block and watch the chain break.",
                        },
                        {
                            "type": "quiz",
                            "question": "What makes a blockchain immutable?",
                            "options": [
                                "It uses a database",
                                "Each block hashes the previous block",
                                "It is stored on one computer",
                                "It uses encryption",
                            ],
                            "correct": 1,
                            "explanation": "Each block contains the hash of the previous block. Change one transaction and every subsequent hash changes, making tampering obvious.",
                        },
                        {
                            "type": "simulation",
                            "title": "Hash Visualization",
                            "description": "Type any text and see its SHA-256 hash change in real-time. Even one letter difference creates a completely different hash.",
                        },
                    ]
                },
            },
            {
                "id": "wallets-explained",
                "title": "Wallets & Keys",
                "type": "interactive",
                "duration": 7,
                "xp": 70,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Your Crypto Wallet",
                            "sub": "Not actually a wallet",
                        },
                        {
                            "type": "interactive-keygen",
                            "title": "Generate a Key Pair",
                            "description": "Watch as a random private key is generated, and its corresponding public address is derived. Click to generate a new one.",
                        },
                        {
                            "type": "quiz",
                            "question": "What should you NEVER do with your private key?",
                            "options": [
                                "Store it in a hardware wallet",
                                "Share it with anyone",
                                "Write it on paper",
                                "Use it to sign transactions",
                            ],
                            "correct": 1,
                            "explanation": "Your private key is like the password to your entire account. Anyone with it controls your funds. NEVER share it, screenshot it, or paste it into websites.",
                        },
                        {
                            "type": "drag-drop",
                            "title": "Secure vs Insecure Storage",
                            "items": [
                                {"text": "Hardware wallet", "zone": "secure"},
                                {"text": "Screenshot on phone", "zone": "insecure"},
                                {"text": "Encrypted USB drive", "zone": "secure"},
                                {"text": "Email to yourself", "zone": "insecure"},
                                {"text": "Paper in safe", "zone": "secure"},
                                {"text": "Cloud storage", "zone": "insecure"},
                            ],
                        },
                    ]
                },
            },
            {
                "id": "reading-explorers",
                "title": "Reading Block Explorers",
                "type": "interactive",
                "duration": 10,
                "xp": 100,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Become a Blockchain Detective",
                            "sub": "How to read what really happened",
                        },
                        {
                            "type": "explorer-sim",
                            "title": "Live Explorer Simulation",
                            "description": "A simulated blockchain explorer showing real-looking transactions. Click through to trace a token from creation to first buyers.",
                        },
                        {
                            "type": "quiz",
                            "question": "What does a 'contract creation' transaction mean?",
                            "options": [
                                "Someone bought a token",
                                "A new smart contract was deployed",
                                "The blockchain was upgraded",
                                "A wallet was created",
                            ],
                            "correct": 1,
                            "explanation": "Contract creation means a developer deployed a new smart contract to the blockchain. This is how tokens, DEXs, and DeFi protocols are born.",
                        },
                        {
                            "type": "spot-the-difference",
                            "title": "Real vs Fake Explorer",
                            "description": "Two explorer screenshots side by side. One is real Etherscan, the other is a phishing clone. Spot 5 differences.",
                        },
                    ]
                },
            },
            {
                "id": "tokens-vs-coins",
                "title": "Coins vs Tokens",
                "type": "lesson",
                "duration": 5,
                "xp": 50,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Not All Crypto is Equal",
                            "sub": "The difference between layer-1 coins and ERC-20 tokens",
                        },
                        {
                            "type": "comparison",
                            "left": {
                                "title": "Coin (Layer 1)",
                                "examples": ["Bitcoin", "Ethereum", "Solana"],
                                "traits": [
                                    "Has its own blockchain",
                                    "Used for network fees",
                                    "Native to the protocol",
                                ],
                            },
                            "right": {
                                "title": "Token",
                                "examples": ["USDC", "SHIB", "PEPE"],
                                "traits": [
                                    "Built ON another blockchain",
                                    "Uses smart contracts",
                                    "Can be created by anyone",
                                ],
                            },
                        },
                        {
                            "type": "quiz",
                            "question": "Which can be created by anyone in 5 minutes?",
                            "options": [
                                "A new Bitcoin",
                                "A new Ethereum",
                                "A new token on Ethereum",
                                "A new Solana validator",
                            ],
                            "correct": 2,
                            "explanation": "Anyone can deploy an ERC-20 token on Ethereum for under $5. This is why there are 500,000+ tokens and most are worthless or scams.",
                        },
                    ]
                },
            },
            {
                "id": "gas-fees",
                "title": "Gas Fees & Transactions",
                "type": "interactive",
                "duration": 8,
                "xp": 80,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Why Transactions Cost Money",
                            "sub": "Gas: the fuel of the blockchain",
                        },
                        {
                            "type": "gas-simulator",
                            "title": "Gas Fee Simulator",
                            "description": "Adjust network congestion and transaction complexity. Watch how gas fees change in real-time. Compare Ethereum, Base, and Solana costs.",
                        },
                        {
                            "type": "quiz",
                            "question": "What happens if you set gas too low?",
                            "options": [
                                "Transaction is free",
                                "Transaction fails or gets stuck",
                                "You get a refund",
                                "It processes faster",
                            ],
                            "correct": 1,
                            "explanation": "If gas is too low, miners/validators won't include your transaction. It can get stuck 'pending' for hours or days until you cancel or replace it.",
                        },
                        {
                            "type": "interactive",
                            "title": "Estimate a Swap",
                            "description": "Simulate a token swap on Uniswap. Input amount, see price impact, slippage, and total gas cost before confirming.",
                        },
                    ]
                },
            },
            {
                "id": "crypto-basics-exam",
                "title": "Module Exam: Crypto Basics",
                "type": "exam",
                "duration": 7,
                "xp": 120,
                "passing_score": 80,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "Prove your knowledge",
                            "sub": "12 questions · 80% to pass · 3 attempts allowed",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the primary purpose of a blockchain?",
                            "options": [
                                "To mine Bitcoin",
                                "To store decentralized, tamper-proof records",
                                "To replace banks",
                                "To create NFTs",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "Which of these is a Layer-1 blockchain?",
                            "options": ["USDC", "Uniswap", "Ethereum", "Chainlink"],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'immutable' mean in blockchain?",
                            "options": [
                                "Fast transactions",
                                "Cannot be changed after creation",
                                "Free to use",
                                "Anonymous",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "Where is the SAFEST place to store large amounts of crypto?",
                            "options": [
                                "Exchange wallet",
                                "Mobile app",
                                "Hardware wallet",
                                "Browser extension",
                            ],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'private key'?",
                            "options": [
                                "Your wallet address",
                                "Your password to the blockchain",
                                "A type of cryptocurrency",
                                "A mining tool",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What happens to gas fees when the network is congested?",
                            "options": [
                                "They decrease",
                                "They increase",
                                "They stay the same",
                                "They become free",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    }
