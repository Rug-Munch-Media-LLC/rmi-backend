"""flash-loan-attack-analysis module."""
from __future__ import annotations

flash_loan_attack_analysis = {
    "title": 'Flash Loan Attack Analysis',
    "type": 'interactive',
    "duration": 20,
    "xp": 300,
    "content": {'slides': [{'type': 'title', 'text': 'Borrow Billions, Attack in Seconds', 'sub': 'Deconstructing the most devastating DeFi exploits'}, {'type': 'interactive', 'title': 'Euler Finance Attack Reconstruction', 'description': 'Step-by-step reconstruction of the $197M Euler Finance exploit. The attacker exploited a donation function to manipulate collateral ratios, then used flash loans to amplify the attack. Every transaction decoded.'}, {'type': 'quiz', 'question': 'What was the root cause of the Cream Finance $130M exploit?', 'options': ['A phishing attack', 'A flash loan combined with a rounding error in the collateral calculation', 'A 51% attack', 'An oracle failure'], 'correct': 1, 'explanation': 'The attacker used a flash loan to deposit a large amount of yUSDVault tokens, exploiting a rounding error that inflated their collateral value, allowing them to borrow far more than they should have been able to.'}, {'type': 'interactive', 'title': 'Flash Loan Attack Simulator', 'description': 'Design your own flash loan attack (educational only!). Choose a vulnerability type, select a flash loan amount, and see how the attack unfolds. Understand the attack lifecycle: borrow → manipulate → exploit → repay → profit.'}, {'type': 'quiz', 'question': 'Why are flash loans so dangerous as an attack vector?', 'options': ["They're slow", 'They let anyone with a smart contract borrow unlimited capital with zero collateral, executing complex attacks in a single transaction', 'They require KYC', 'They only work on Ethereum'], 'correct': 1, 'explanation': 'Flash loans remove the capital barrier for attacks. Previously, an attacker needed millions to manipulate a protocol. With flash loans, anyone can borrow that amount for the duration of a single transaction.'}]},
}

__all__ = ["flash_loan_attack_analysis"]
