"""governance-attack-detection module."""

from __future__ import annotations

governance_attack_detection = {
    "title": "Governance Attack Detection",
    "type": "interactive",
    "duration": 20,
    "xp": 300,
    "content": {
        "slides": [
            {
                "type": "title",
                "text": "Hijacking Democracy",
                "sub": "How attackers manipulate DAOs and governance systems",
            },
            {
                "type": "interactive",
                "title": "Flash Loan Governance Attack Simulator",
                "description": "Watch how an attacker borrows governance tokens via flash loan, votes to drain the treasury, and repays the loan — all in one transaction. The DAO never saw it coming. Learn why snapshot-based voting is vulnerable.",
            },
            {
                "type": "quiz",
                "question": "How does a flash loan governance attack work?",
                "options": [
                    "By bribing voters",
                    "By borrowing governance tokens for one block, voting on a malicious proposal, and repaying — all in one transaction",
                    "By hacking the voting website",
                    "By creating fake proposals",
                ],
                "correct": 1,
                "explanation": "If governance power is based on token holdings at a snapshot, an attacker can flash-loan a massive amount of tokens, vote to drain the treasury or change protocol parameters, and return the loan — all atomic.",
            },
            {
                "type": "interactive",
                "title": "Proposal Analysis Framework",
                "description": "Learn to audit governance proposals: (1) Check proposer address history, (2) Analyze the proposed changes for malicious intent, (3) Verify the voting power source isn't a flash loan, (4) Check timelock implementation.",
            },
            {
                "type": "quiz",
                "question": "What is the best defense against flash loan governance attacks?",
                "options": [
                    "No defense is possible",
                    "Requiring voting power to be held for a minimum period (e.g., 1 block before proposal) and timelocks on execution",
                    "Limiting proposals to one per day",
                    "Using a different blockchain",
                ],
                "correct": 1,
                "explanation": "A minimum holding period prevents flash-loaned tokens from being used to vote. Timelocks give the community time to review and potentially veto malicious proposals before they execute.",
            },
        ]
    },
}

__all__ = ["governance_attack_detection"]
