"""memecoin-launch-patterns module."""
from __future__ import annotations

memecoin_launch_patterns = {
    "title": 'Memecoin Launch Patterns',
    "type": 'interactive',
    "duration": 14,
    "xp": 180,
    "content": {'slides': [{'type': 'title', 'text': 'The Memecoin Casino', 'sub': 'Understanding pump.fun mechanics and graduated token risks'}, {'type': 'interactive', 'title': 'Pump.fun Launch Simulator', 'description': "Simulate the lifecycle of a pump.fun token: creation → bonding curve → graduation to Raydium. See how 99% of tokens die on the bonding curve and why 'graduated' doesn't mean 'safe'."}, {'type': 'quiz', 'question': "On pump.fun, what does a token 'graduating' mean?", 'options': ['The token is now safe', 'It reached sufficient market cap to automatically deploy to Raydium with burned LP', 'The developers graduated from the platform', 'The token received an audit'], 'correct': 1, 'explanation': 'Graduation means the bonding curve completed and the token was deployed to Raydium. While LP is burned in this process, graduation does NOT mean the token is safe — graduated tokens can still be bundled, manipulated, or rugged.'}, {'type': 'interactive', 'title': 'Sniper Bot Detection on New Launches', 'description': 'Watch a new pump.fun token get created. Within milliseconds, 20+ wallets buy in. These are sniper bots. By the time it appears on the front page for humans, the bots already own 30%+ of the supply. Learn to read the trade history.'}, {'type': 'quiz', 'question': 'What percentage of pump.fun tokens ever reach a $100K+ market cap?', 'options': ['About 50%', 'About 25%', 'Less than 1% — the vast majority go to zero within hours', 'About 90%'], 'correct': 2, 'explanation': 'The vast majority of memecoins go to zero quickly. The few that succeed often have coordinated marketing and insider groups. Treat every memecoin as a lottery ticket at best — and a scam at worst.'}]},
}

__all__ = ["memecoin_launch_patterns"]
