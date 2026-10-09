"""liquidity-and-market-caps module."""

from __future__ import annotations

liquidity_and_market_caps = {
    "title": "Liquidity & Market Caps",
    "type": "interactive",
    "duration": 12,
    "xp": 120,
    "content": {
        "slides": [
            {"type": "title", "text": "Why Liquidity Matters", "sub": "The fuel that lets you buy AND sell"},
            {
                "type": "interactive",
                "title": "Liquidity Pool Simulator",
                "description": "Adjust the amount of liquidity in a pool. Watch what happens to slippage when you try to sell $100 vs $1,000 vs $10,000. Low liquidity = high slippage = you lose money.",
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
                "explanation": "Market cap = price x supply. High market cap with low liquidity means the price would collapse the moment anyone tries to sell. It's a mirage — your 'value' can't be realized.",
            },
            {
                "type": "interactive",
                "title": "Using DexScreener to Check Liquidity",
                "description": "Live demo: analyze a real token on DexScreener. Find liquidity, check if LP is locked, see 24h volume vs liquidity ratio, and identify dangerous patterns.",
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
                "explanation": "A healthy project typically has at least 5-10% of its market cap in liquidity. Anything below 2% is a red flag. Always check if the LP is locked or burned.",
            },
        ]
    },
}

__all__ = ["liquidity_and_market_caps"]
