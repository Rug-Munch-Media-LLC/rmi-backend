"""understanding-token-contracts module."""

from __future__ import annotations

understanding_token_contracts = {
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
                "description": "A guided tour of Etherscan/Solscan. Learn to find: token address, contract creator, holder distribution, transaction history, and the critical 'Contract' tab.",
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
                "explanation": "Verified means the human-readable Solidity/Vyper code matches the bytecode on-chain. It lets you inspect the code, but it does NOT mean the code is safe — it could still contain honeypots or backdoors.",
            },
            {
                "type": "code-hunt",
                "title": "Unverified vs Verified",
                "description": "Two contract pages side-by-side: one verified (you can see every function), one unverified (only bytecode). Learn why unverified = extreme risk.",
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
                "explanation": "An unverified contract hides its source code. The dev could have put anything in there — honeypots, infinite mint functions, wallet blacklists. Always verify the contract.",
            },
        ]
    },
}

__all__ = ["understanding_token_contracts"]
