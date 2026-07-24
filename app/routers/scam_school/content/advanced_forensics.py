advanced_forensics = {
        "id": "advanced-forensics",
        "title": "Advanced On-Chain Forensics",
        "subtitle": "Think like a blockchain detective",
        "description": "Deep technical analysis. Trace transactions across chains, decode contract interactions, and build cases with real evidence.",
        "difficulty": "advanced",
        "duration_minutes": 120,
        "lessons_count": 8,
        "xp_reward": 2000,
        "badge_reward": "forensic-analyst",
        "color": "#f59e0b",
        "icon": "microscope",
        "modules": [
            {
                "id": "tracing-transactions",
                "title": "Tracing Transactions",
                "type": "interactive",
                "duration": 15,
                "xp": 250,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Follow the Money",
                            "sub": "Every transaction leaves a trail",
                        },
                        {
                            "type": "trace-sim",
                            "title": "Transaction Tracer",
                            "description": "Start with a suspicious wallet. Trace its funding source through 5 hops. Discover it was funded by a known scammer wallet from a previous rug pull.",
                        },
                        {
                            "type": "quiz",
                            "question": "What tool shows all transactions for a specific wallet?",
                            "options": [
                                "DEX Screener",
                                "Etherscan / Solscan",
                                "CoinMarketCap",
                                "Twitter",
                            ],
                            "correct": 1,
                            "explanation": "Block explorers like Etherscan (Ethereum) and Solscan (Solana) show every transaction, token transfer, and contract interaction for any wallet.",
                        },
                        {
                            "type": "interactive",
                            "title": "Multi-Chain Trace",
                            "description": "A scammer bridges stolen funds from Ethereum to Base to Solana. Follow the trail across chains using bridge transaction hashes.",
                        },
                    ]
                },
            },
            {
                "id": "contract-forensics",
                "title": "Contract Forensics",
                "type": "interactive",
                "duration": 20,
                "xp": 300,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Decode the Contract",
                            "sub": "Reading bytecode like a detective reads fingerprints",
                        },
                        {
                            "type": "bytecode-sim",
                            "title": "Bytecode Analysis",
                            "description": "Even unverified contracts can be analyzed. Use decompilers to find function signatures, storage patterns, and suspicious logic in raw bytecode.",
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'renounced ownership' mean?",
                            "options": [
                                "The dev sold their tokens",
                                "No one can change the contract anymore",
                                "The contract was deleted",
                                "The token is worthless",
                            ],
                            "correct": 1,
                            "explanation": "Renounced ownership means the deployer sent ownership to the zero address (0x000...). They can no longer mint tokens, change fees, or pause trading.",
                        },
                        {
                            "type": "interactive",
                            "title": "Function Signature Decoder",
                            "description": "Given a transaction's input data (0x23b872dd...), decode it to 'transferFrom(address,address,uint256)'. Learn to read raw transaction data.",
                        },
                    ]
                },
            },
            {
                "id": "advanced-exam",
                "title": "Master Forensics Exam",
                "type": "exam",
                "duration": 20,
                "xp": 450,
                "passing_score": 90,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "Forensic Analyst Certification",
                            "sub": "15 questions · 90% to pass · Elite badge awarded",
                        },
                        {
                            "type": "quiz",
                            "question": "How can you detect wash trading?",
                            "options": [
                                "High market cap",
                                "Same wallets buying and selling to each other",
                                "Low gas fees",
                                "Verified contract",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'dust attack'?",
                            "options": [
                                "Sending tiny amounts to track wallets",
                                "A type of DDoS",
                                "Mining with low power",
                                "A phishing technique",
                            ],
                            "correct": 0,
                        },
                        {
                            "type": "quiz",
                            "question": "Which bridge transaction field is MOST important for tracing?",
                            "options": [
                                "Token symbol",
                                "Transaction hash",
                                "Wallet balance",
                                "Gas price",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    }
