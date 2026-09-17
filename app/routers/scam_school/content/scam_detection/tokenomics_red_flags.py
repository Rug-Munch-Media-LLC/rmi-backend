"""tokenomics-red-flags module."""
from __future__ import annotations

tokenomics_red_flags = {
    "title": 'Tokenomics Red Flags',
    "type": 'interactive',
    "duration": 14,
    "xp": 180,
    "content": {'slides': [{'type': 'title', 'text': 'Math That Steals Your Money', 'sub': 'Bad tokenomics are a preview of bad outcomes'}, {'type': 'allocation-sim', 'title': 'Token Allocation Designer', 'description': "Design a token allocation. See what happens when a team holds 5% vs 50% vs 80%. Simulate the price impact when the team's tokens unlock. Notice how 80% insider allocation = guaranteed dump."}, {'type': 'quiz', 'question': "A token has a 10% buy tax and 10% sell tax. After buying and immediately selling, you've lost:", 'options': ['0% — the taxes cancel out', '~19% of your capital (you lose 10% on buy, then 10% on the remaining 90%)', '10% — only the sell tax matters', '5% — the average of both taxes'], 'correct': 1, 'explanation': "Buy with $100 → $10 tax → $90 worth of tokens. Sell $90 → $9 tax → $81 back. You've lost $19 total. High buy/sell taxes are a common mechanism for devs to extract value from every transaction."}, {'type': 'red-flag-checklist', 'title': 'Tokenomics Red Flag Checklist', 'items': [{'flag': 'Total supply not fixed (can mint more)', 'risk': 'Infinite dilution possible'}, {'flag': 'Team allocation > 20%', 'risk': 'Concentrated dump risk'}, {'flag': 'Buy/sell tax > 5%', 'risk': 'Excessive extraction per trade'}, {'flag': 'Unfair vesting (team unlocks before community)', 'risk': 'Insiders dump first'}, {'flag': 'Reflection/redistribution mechanics', 'risk': 'Often used to hide tax extraction'}, {'flag': 'No burn mechanism or deflationary model', 'risk': 'Supply only increases'}]}, {'type': 'quiz', 'question': "What is the danger of 'reflection' or 'redistribution' tokenomics?", 'options': ['They make tokens faster', "They can hide high effective tax rates while marketing it as 'earning passive rewards'", 'They increase security', 'They are always legitimate'], 'correct': 1, 'explanation': "Reflection tokens often take a 10-15% tax on every transaction and redistribute a portion to holders. The effective cost to traders is high, but it's marketed as 'earning rewards.' Always calculate the real cost before buying."}]},
}

__all__ = ["tokenomics_red_flags"]
