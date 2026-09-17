"""introduction-to-rugcheck-tools module."""
from __future__ import annotations

introduction_to_rugcheck_tools = {
    "title": 'Introduction to RugCheck Tools',
    "type": 'interactive',
    "duration": 12,
    "xp": 120,
    "content": {'slides': [{'type': 'title', 'text': 'Your Scam Detection Arsenal', 'sub': 'Free tools that save you money'}, {'type': 'interactive', 'title': 'Scanner Roundup', 'description': 'Overview of free scanners: RugMunch, GoPlus, TokenSniffer, Honeypot.is, DexScreener Safety Check. Live demo of each tool analyzing the same contract. See which red flags each tool catches.'}, {'type': 'quiz', 'question': "How does RugMunch's scanner help protect you?", 'options': ['It guarantees tokens are safe', 'It analyzes contracts for honeypots, ownership risks, and 20+ other security factors', 'It trades tokens for you', 'It only checks token prices'], 'correct': 1, 'explanation': 'RugMunch runs a comprehensive analysis: honeypot detection, ownership check, mint function audit, liquidity lock verification, holder distribution analysis, and more. It gives you a risk score to make informed decisions.'}, {'type': 'interactive', 'title': 'Hands-On: Scan a Token', 'description': 'Paste a real contract address into a simulated scanner. Watch the analysis run: contract verification, honeypot check, owner privileges, holder concentration, liquidity analysis. Interpret each result.'}, {'type': 'quiz', 'question': 'You scan a token and it passes one scanner but fails another. You should:', 'options': ["Trust the one that says it's safe", 'Investigate why they disagree — the failing scanner may have caught something', 'Buy immediately before others find out', "Ignore all scanners, they're unreliable"], 'correct': 1, 'explanation': 'Different scanners check different things. A disagreement is a red flag that warrants deeper investigation. Never rely on a single source.'}]},
}

__all__ = ["introduction_to_rugcheck_tools"]
