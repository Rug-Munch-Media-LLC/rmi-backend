"""dark-web-offchain-intelligence module."""
from __future__ import annotations

dark_web_offchain_intelligence = {
    "title": 'Dark Web & Off-Chain Intelligence',
    "type": 'interactive',
    "duration": 22,
    "xp": 330,
    "content": {'slides': [{'type': 'title', 'text': 'Beyond the Blockchain', 'sub': 'OSINT and dark web intelligence for crypto investigations'}, {'type': 'interactive', 'title': 'OSINT Workflow for Crypto', 'description': 'Build an intelligence gathering pipeline: (1) Social media analysis (Twitter, Telegram, Discord), (2) Domain/WHOIS lookups, (3) GitHub commit analysis, (4) Darknet forum monitoring, (5) Threat intelligence feeds (TRM, Elliptic, Chainalysis).'}, {'type': 'quiz', 'question': "What OSINT technique can reveal a scammer's identity from their token launch?", 'options': ['Checking the token price', "Analyzing the deployer wallet's funding source — if it came from a CEX with KYC, law enforcement can subpoena records", 'Reading the token website', 'Following their Twitter'], 'correct': 1, 'explanation': 'If the deployer wallet was funded from a centralized exchange (Coinbase, Binance, etc.), that exchange has KYC records. Law enforcement can subpoena those records to identify the scammer. This is why sophisticated criminals use mixers or privacy coins.'}, {'type': 'interactive', 'title': 'Threat Intelligence Feed Integration', 'description': 'Learn to consume and correlate threat intel: sanctioned address lists (OFAC SDN), known scammer databases, phishing URL feeds, and blockchain analytics alerts. Set up automated monitoring for your watchlist addresses.'}, {'type': 'quiz', 'question': 'What is the value of darknet forum monitoring for crypto investigations?', 'options': ["None — it's all fake", 'Scammers often sell tools, discuss techniques, and coordinate attacks on darknet forums, providing early warning intelligence', "It's only for drugs", "It's too hard to access"], 'correct': 1, 'explanation': 'Darknet forums (Dread, BreachForums, etc.) are where scammers buy and sell drainers, discuss new exploit techniques, and recruit for coordinated attacks. Monitoring these provides actionable early warning intelligence.'}]},
}

__all__ = ["dark_web_offchain_intelligence"]
