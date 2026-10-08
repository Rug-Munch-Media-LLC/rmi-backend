"""what-is-a-rug-pull module."""

from __future__ import annotations

what_is_a_rug_pull = {
    "title": "What Is a Rug Pull?",
    "type": "interactive",
    "duration": 12,
    "xp": 120,
    "content": {
        "slides": [
            {"type": "title", "text": "The Rug Pull Explained", "sub": "Understanding the most common crypto scam"},
            {
                "type": "interactive",
                "title": "Rug Pull Timeline",
                "description": "Watch how a rug pull unfolds: from the launch hype, through the price pump, to the moment liquidity vanishes. Interactive timeline with real examples.",
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
                "explanation": "A rug pull is specifically when the project creators intentionally steal funds — either by draining the liquidity pool or dumping their entire token supply on investors.",
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
                "explanation": "The Squid Game token used a 'anti-dumping mechanism' that prevented anyone from selling, while the developers drained $3.3M from the liquidity pool.",
            },
        ]
    },
}

__all__ = ["what_is_a_rug_pull"]
