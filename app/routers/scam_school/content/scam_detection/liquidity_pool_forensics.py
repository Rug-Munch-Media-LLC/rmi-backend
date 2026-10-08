"""liquidity-pool-forensics module."""

from __future__ import annotations

liquidity_pool_forensics = {
    "title": "Liquidity Pool Forensics",
    "type": "interactive",
    "duration": 15,
    "xp": 200,
    "content": {
        "slides": [
            {
                "type": "title",
                "text": "Liquidity: Locked vs Burned vs Exposed",
                "sub": "Understanding the three LP states and what they mean",
            },
            {
                "type": "interactive",
                "title": "LP Lock Simulator",
                "description": "Visual comparison: Locked LP (dev cannot access for X time), Burned LP (permanently removed, no one can touch), and Unlocked LP (dev can drain at ANY moment). See the risk difference in real-time.",
            },
            {
                "type": "quiz",
                "question": "What is the difference between 'locked' and 'burned' LP?",
                "options": [
                    "They are the same thing",
                    "Locked LP is temporarily inaccessible; burned LP is permanently destroyed",
                    "Locked is safer than burned",
                    "Burned LP can be recovered",
                ],
                "correct": 1,
                "explanation": "Locked LP: tokens are held in a time-lock contract and will unlock at a future date. Burned LP: tokens are sent to a dead address (0x000...), permanently removed. Burned is safer. Locked still requires trust that the dev won't drain after unlock.",
            },
            {
                "type": "interactive",
                "title": "LP Removal Detection",
                "description": "Monitor a token where the dev withdraws liquidity over 3 small transactions instead of one big one — trying to avoid detection. Learn how to spot incremental LP removal patterns that scanners often miss.",
            },
            {
                "type": "quiz",
                "question": "What is 'migration risk' in liquidity pools?",
                "options": [
                    "Moving to a new website",
                    "The dev can migrate liquidity to a new pool they control, making the old token worthless",
                    "Updating the token logo",
                    "Changing the token name",
                ],
                "correct": 1,
                "explanation": "Some contracts allow the owner to migrate LP to a new pool. This effectively creates a new token while leaving holders of the old one with worthless assets. Always check for migration functions.",
            },
        ]
    },
}

__all__ = ["liquidity_pool_forensics"]
