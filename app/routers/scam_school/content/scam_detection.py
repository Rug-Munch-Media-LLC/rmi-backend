scam_detection = {
        "id": "scam-detection",
        "title": "Scam Detection Masterclass",
        "subtitle": "Learn to spot rugs before they happen",
        "description": "The core RMI curriculum. Learn every red flag, from honeypots to coordinated bundling. Interactive simulations of real scam patterns.",
        "difficulty": "intermediate",
        "duration_minutes": 90,
        "lessons_count": 10,
        "xp_reward": 1200,
        "badge_reward": "scam-hunter",
        "color": "#ef4444",
        "icon": "siren",
        "modules": [
            {
                "id": "honeypots",
                "title": "Honeypot Traps",
                "type": "interactive",
                "duration": 12,
                "xp": 150,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Honeypot Trap",
                            "sub": "You can buy, but you can never sell",
                        },
                        {
                            "type": "honeypot-sim",
                            "title": "Honeypot Simulator",
                            "description": "A live trading simulation. You 'buy' a token, the price goes up. Try to sell... the button is disabled. Watch how the contract code prevents sells.",
                        },
                        {
                            "type": "code-inspection",
                            "title": "Spot the Honeypot Code",
                            "description": "Two smart contract code snippets. One is legitimate, one contains a honeypot. Find the malicious line.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is a honeypot token?",
                            "options": [
                                "A token with high rewards",
                                "A token you can buy but not sell",
                                "A token with locked liquidity",
                                "A token with a cute mascot",
                            ],
                            "correct": 1,
                            "explanation": "Honeypots allow buys (attracting victims) but block sells through smart contract code. Your money goes in, never comes out.",
                        },
                        {
                            "type": "interactive",
                            "title": "RMI Scanner Demo",
                            "description": "Paste a contract address (simulated) and watch the RMI scanner detect honeypot code, unverified contracts, and malicious functions in real-time.",
                        },
                    ]
                },
            },
            {
                "id": "rug-pull-mechanics",
                "title": "Rug Pull Mechanics",
                "type": "interactive",
                "duration": 15,
                "xp": 200,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Anatomy of a Rug Pull",
                            "sub": "From launch to drain in 48 hours",
                        },
                        {
                            "type": "timeline",
                            "title": "Rug Pull Timeline",
                            "events": [
                                {
                                    "time": "T-7 days",
                                    "event": "Dev creates token, socials, and website",
                                    "flag": False,
                                },
                                {
                                    "time": "T-1 day",
                                    "event": "Influencers paid to shill",
                                    "flag": True,
                                },
                                {
                                    "time": "T-0",
                                    "event": "Token launches with 80% insider allocation",
                                    "flag": True,
                                },
                                {
                                    "time": "T+2 hours",
                                    "event": "Price pumps 10x as insiders coordinate buys",
                                    "flag": True,
                                },
                                {
                                    "time": "T+6 hours",
                                    "event": "Insiders dump 60% of supply",
                                    "flag": True,
                                },
                                {
                                    "time": "T+12 hours",
                                    "event": "LP tokens withdrawn, liquidity drained",
                                    "flag": True,
                                },
                                {
                                    "time": "T+48 hours",
                                    "event": "Socials deleted, website down, dev gone",
                                    "flag": True,
                                },
                            ],
                        },
                        {
                            "type": "allocation-sim",
                            "title": "Token Allocation Simulator",
                            "description": "Design a token allocation. If the team holds more than 20%, the community is at risk. Watch what happens when insiders control 80%.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the FIRST red flag in a rug pull timeline?",
                            "options": [
                                "Price drops",
                                "High insider allocation at launch",
                                "Social media deleted",
                                "Website goes down",
                            ],
                            "correct": 1,
                            "explanation": "The allocation is visible BEFORE you invest. If insiders hold 60-80%, they have the power to dump on you. Check this first.",
                        },
                        {
                            "type": "interactive",
                            "title": "Liquidity Lock Check",
                            "description": "Simulate checking LP tokens on a lock platform. Unlocked LP = dev can drain the pool anytime. Locked LP = safer (but not guaranteed).",
                        },
                    ]
                },
            },
            {
                "id": "bundler-detection",
                "title": "Bundler & Sniper Detection",
                "type": "interactive",
                "duration": 15,
                "xp": 200,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Coordinated Attack",
                            "sub": "How bundlers manipulate launches",
                        },
                        {
                            "type": "bundle-visualizer",
                            "title": "Bundle Visualizer",
                            "description": "Watch 20 connected wallets buy simultaneously at launch. The visualization shows wallet connections, buy timing, and total allocation. This is a coordinated bundler attack.",
                        },
                        {
                            "type": "sniper-sim",
                            "title": "Sniper Bot Simulation",
                            "description": "Simulate a token launch. Bots buy in the same block as creation. Normal users buy 10 blocks later at 5x the price. See the unfair advantage in real-time.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is a 'bundler' in crypto?",
                            "options": [
                                "A wallet that holds many tokens",
                                "A group of coordinated wallets buying together",
                                "A type of smart contract",
                                "A blockchain validator",
                            ],
                            "correct": 1,
                            "explanation": "Bundlers are networks of wallets controlled by the same entity. They buy simultaneously to hide their total allocation and create fake demand.",
                        },
                        {
                            "type": "interactive",
                            "title": "Wallet Cluster Analysis",
                            "description": "Given 10 wallet addresses, trace their funding sources. 8 were funded by the same wallet 1 hour before launch. This is the bundler's master wallet.",
                        },
                    ]
                },
            },
            {
                "id": "contract-red-flags",
                "title": "Smart Contract Red Flags",
                "type": "interactive",
                "duration": 12,
                "xp": 150,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "Reading the Code",
                            "sub": "Red flags in smart contracts",
                        },
                        {
                            "type": "code-hunt",
                            "title": "Find the Kill Switch",
                            "description": "A smart contract has a hidden 'kill' function that lets the owner destroy all tokens. Find it in the code.",
                        },
                        {
                            "type": "red-flag-checklist",
                            "title": "Contract Red Flag Checklist",
                            "items": [
                                {
                                    "flag": "Mint function not renounced",
                                    "risk": "Dev can create infinite tokens",
                                },
                                {
                                    "flag": "Ownership not renounced",
                                    "risk": "Dev can change contract rules",
                                },
                                {
                                    "flag": "Hidden transfer fees > 10%",
                                    "risk": "Every buy/sell loses money to dev",
                                },
                                {
                                    "flag": "Blacklist function exists",
                                    "risk": "Dev can block you from selling",
                                },
                                {
                                    "flag": "Contract not verified",
                                    "risk": "You can't see what the code does",
                                },
                                {
                                    "flag": "Proxy contract with admin",
                                    "risk": "Dev can change code after launch",
                                },
                            ],
                        },
                        {
                            "type": "quiz",
                            "question": "Why is an unverified contract dangerous?",
                            "options": [
                                "It costs more gas",
                                "You can't read the source code",
                                "It has no logo",
                                "It can't be traded",
                            ],
                            "correct": 1,
                            "explanation": "Unverified contracts hide their source code. The deployer could have put anything in there - honeypots, backdoors, infinite mint functions. Always verify.",
                        },
                    ]
                },
            },
            {
                "id": "social-engineering",
                "title": "Social Engineering Attacks",
                "type": "interactive",
                "duration": 10,
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
                            "title": "Phishing Simulation",
                            "description": "You receive a DM: 'Your wallet has been compromised. Click here to secure it immediately.' The link looks like MetaMask. What do you do? Interactive choose-your-own-adventure.",
                        },
                        {
                            "type": "fake-support",
                            "title": "Fake Support Scam",
                            "description": "A 'support agent' in Discord asks you to 'verify your wallet' by sharing your seed phrase. Walk through the conversation and spot the red flags.",
                        },
                        {
                            "type": "quiz",
                            "question": "A 'dev' DMs you with a 'pre-sale' link. What do you do?",
                            "options": [
                                "Click it immediately",
                                "Ask for proof they're the real dev",
                                "Check official channels and ignore DMs",
                                "Send a small amount to test",
                            ],
                            "correct": 2,
                            "explanation": "Real devs never DM first. Always verify through official channels (verified Twitter, official Discord announcements). Pre-sale links in DMs are 99% scams.",
                        },
                        {
                            "type": "drag-drop",
                            "title": "Real vs Fake Announcement",
                            "items": [
                                {
                                    "text": "Posted by verified account with checkmark",
                                    "zone": "real",
                                },
                                {"text": "Urgency: 'Limited time, act NOW!'", "zone": "fake"},
                                {"text": "Links to official domain", "zone": "real"},
                                {"text": "Asks for private key or seed phrase", "zone": "fake"},
                                {"text": "Announced on multiple official channels", "zone": "real"},
                                {"text": "Only shared in DM", "zone": "fake"},
                            ],
                        },
                    ]
                },
            },
            {
                "id": "pump-dump-patterns",
                "title": "Pump & Dump Patterns",
                "type": "interactive",
                "duration": 10,
                "xp": 120,
                "content": {
                    "slides": [
                        {
                            "type": "title",
                            "text": "The Artificial Hype",
                            "sub": "How coordinated pumps create bagholders",
                        },
                        {
                            "type": "chart-sim",
                            "title": "Live Chart Simulation",
                            "description": "Watch a token chart in real-time. See the telltale signs: volume spikes with no news, identical buy sizes, wallet clustering, and the inevitable dump.",
                        },
                        {
                            "type": "quiz",
                            "question": "What is the hallmark of a coordinated pump?",
                            "options": [
                                "Steady organic growth",
                                "Sudden volume spike with identical wallet sizes",
                                "Slow price decline",
                                "High liquidity",
                            ],
                            "correct": 1,
                            "explanation": "Coordinated pumps show sudden volume spikes from many wallets of similar size buying at the same time. This is bots or a group acting together.",
                        },
                        {
                            "type": "interactive",
                            "title": "Volume Analysis Tool",
                            "description": "Analyze a token's trading history. Spot the wash trading: buyers and sellers are the same wallets. Real volume vs fake volume visualization.",
                        },
                    ]
                },
            },
            {
                "id": "scam-detection-exam",
                "title": "Final Exam: Scam Detection",
                "type": "exam",
                "duration": 16,
                "xp": 260,
                "passing_score": 85,
                "content": {
                    "slides": [
                        {
                            "type": "exam-intro",
                            "text": "The Scam Hunter Certification",
                            "sub": "20 questions · 85% to pass · Certificate awarded",
                        },
                        {
                            "type": "quiz",
                            "question": "A token has 70% insider allocation. What is the risk?",
                            "options": [
                                "Low risk, insiders are trustworthy",
                                "High risk, insiders can dump on retail",
                                "No risk, allocation doesn't matter",
                                "Medium risk, only if price drops",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What does 'LP not burned' mean?",
                            "options": [
                                "Liquidity is locked forever",
                                "Dev can withdraw liquidity anytime",
                                "Tokens can't be traded",
                                "The contract is verified",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "You see 15 wallets funded by the same address buying simultaneously. This is:",
                            "options": [
                                "Organic demand",
                                "A bundler attack",
                                "Market making",
                                "Staking rewards",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "A contract has a 'blacklist' function. What can the dev do?",
                            "options": [
                                "Block specific wallets from selling",
                                "Increase token supply",
                                "Change the token name",
                                "Burn tokens",
                            ],
                            "correct": 0,
                        },
                        {
                            "type": "quiz",
                            "question": "Which is the SAFEST action when receiving an unsolicited airdrop?",
                            "options": [
                                "Sell it immediately",
                                "Interact with the token's website",
                                "Ignore it and don't interact",
                                "Stake it for rewards",
                            ],
                            "correct": 2,
                        },
                        {
                            "type": "quiz",
                            "question": "What is 'slippage' in a DEX trade?",
                            "options": [
                                "The trading fee",
                                "The difference between expected and actual price",
                                "The gas cost",
                                "The time to confirm",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "A token's website claims 'audited by CertiK' but CertiK has no record. This is:",
                            "options": [
                                "A minor mistake",
                                "A fake audit claim",
                                "An old audit",
                                "A private audit",
                            ],
                            "correct": 1,
                        },
                        {
                            "type": "quiz",
                            "question": "What is the #1 rule of crypto safety?",
                            "options": [
                                "Always follow influencers",
                                "Never invest more than you can afford to lose",
                                "Buy the dip",
                                "Hold forever",
                            ],
                            "correct": 1,
                        },
                    ]
                },
            },
        ],
    }
