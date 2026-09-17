"""recognizing-common-scams module."""
from __future__ import annotations

recognizing_common_scams = {
    "title": 'Recognizing Common Scams',
    "type": 'interactive',
    "duration": 12,
    "xp": 120,
    "content": {'slides': [{'type': 'title', 'text': 'The Hall of Shame', 'sub': 'Common crypto scams and how to spot them instantly'}, {'type': 'phishing-sim', 'title': 'Phishing Link Detector', 'description': 'Six wallet-connect popups appear. Three are real dApps, three are phishing clones. Can you tell the difference? Learn the subtle tells: incorrect URLs, missing SSL, typo-squatted domains.'}, {'type': 'quiz', 'question': "A random account DMs you: 'Congrats! You won 10 ETH in our airdrop. Just connect your wallet and approve this transaction to claim.' What do you do?", 'options': ['Connect and claim immediately before it expires', 'Click the link to check if it looks legitimate', 'Ignore and block — real projects never DM you for wallet connections', 'Connect but use an empty wallet to test'], 'correct': 2, 'explanation': "Real projects don't DM you about airdrops. The transaction you 'approve' is actually giving the scammer permission to drain your wallet. Never connect to unsolicited links."}, {'type': 'interactive', 'title': "The 'Send 1 ETH, Get 2 Back' Scam", 'description': "A classic: a fake celebrity account or hacked verified account posts a 'giveaway' address. Interactive scenario showing how these work, why people fall for them, and red flags to watch for."}, {'type': 'quiz', 'question': 'What is the most common vector for impersonation scams?', 'options': ['Email only', 'Fake verified accounts on Twitter/X and Telegram impersonating projects/devs', 'Physical mail', "Phone calls from 'crypto support'"], 'correct': 1, 'explanation': "Scammers create accounts that look identical to real project accounts, often with one character difference in the handle. They then DM followers with 'support' or 'giveaway' offers."}]},
}

__all__ = ["recognizing_common_scams"]
