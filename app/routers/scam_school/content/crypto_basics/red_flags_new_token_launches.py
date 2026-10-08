"""red-flags-new-token-launches module."""

from __future__ import annotations

red_flags_new_token_launches = {
    "title": "Red Flags in New Token Launches",
    "type": "interactive",
    "duration": 12,
    "xp": 120,
    "content": {
        "slides": [
            {"type": "title", "text": "Launch Day Warning Signs", "sub": "Spot a scam before you ape in"},
            {
                "type": "interactive",
                "title": "Launch Simulator: Good vs Bad",
                "description": "Two tokens launch simultaneously. Token A: verified contract, renounced ownership, locked LP, organic holder growth. Token B: unverified, 80% insider wallets, sniper bots in first block, unlocked LP. Which would you buy?",
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
                "explanation": "When dozens of wallets are funded by one source right before launch, it's a bundler attack. These wallets will buy at launch to pump the price, then dump on retail buyers.",
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
                "explanation": "Sniper bots monitor for new liquidity pool creation and buy within the same block. They get the lowest possible price and sell to you at a profit seconds later.",
            },
        ]
    },
}

__all__ = ["red_flags_new_token_launches"]
