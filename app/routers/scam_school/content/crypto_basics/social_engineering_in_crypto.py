"""social-engineering-in-crypto module."""
from __future__ import annotations

social_engineering_in_crypto = {
    "title": 'Social Engineering in Crypto',
    "type": 'interactive',
    "duration": 12,
    "xp": 120,
    "content": {'slides': [{'type': 'title', 'text': 'The Human Exploit', 'sub': 'When scammers hack your mind, not your wallet'}, {'type': 'phishing-sim', 'title': 'Choose Your Own Adventure: Discord Scam', 'description': "You join a new token's Discord. A 'moderator' DMs you with an exclusive pre-sale link that requires connecting your wallet. Walk through the decision tree. Each choice reveals a new red flag you should have noticed."}, {'type': 'quiz', 'question': "A 'support agent' in Telegram asks you to verify your wallet by sharing your screen. You should:", 'options': ["Share your screen, they're support", "Never share your screen or any wallet info — real support doesn't ask", "Only share if they're from a verified account", "Send a small transaction to prove it's your wallet"], 'correct': 1, 'explanation': "Real crypto support will NEVER ask you to share your screen, seed phrase, private key, or to connect your wallet. This is 100% a scam, regardless of how authentic the 'support agent' seems."}, {'type': 'drag-drop', 'title': 'Real vs Fake Announcement', 'items': [{'text': 'Posted by verified account with checkmark', 'zone': 'real'}, {'text': "Urgency: 'Limited time — act NOW or miss out!'", 'zone': 'fake'}, {'text': 'Links to official domain (rugmunch.io)', 'zone': 'real'}, {'text': 'Asks for private key or seed phrase', 'zone': 'fake'}, {'text': 'Announced on multiple official channels simultaneously', 'zone': 'real'}, {'text': 'Only shared in DM, not in public announcement channels', 'zone': 'fake'}]}, {'type': 'quiz', 'question': "What is a 'pump and dump' group?", 'options': ['A legitimate trading community', 'A group that coordinates buying to inflate a price, then sells on newcomers', 'A technical analysis group', 'A mining pool'], 'correct': 1, 'explanation': 'Pump and dump groups coordinate mass buying of a low-cap token to spike its price, then sell into the FOMO. Late joiners are left holding worthless bags.'}]},
}

__all__ = ["social_engineering_in_crypto"]
