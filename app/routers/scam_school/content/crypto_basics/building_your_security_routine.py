"""building-your-security-routine module."""

from __future__ import annotations

building_your_security_routine = {
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
}

__all__ = ["building_your_security_routine"]
