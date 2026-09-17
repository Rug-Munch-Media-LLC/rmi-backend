"""defi-protocol-risk-assessment module."""
from __future__ import annotations

defi_protocol_risk_assessment = {
    "title": 'DeFi Protocol Risk Assessment',
    "type": 'interactive',
    "duration": 20,
    "xp": 300,
    "content": {'slides': [{'type': 'title', 'text': 'Before You Deposit', 'sub': 'Systematic risk evaluation for DeFi protocols'}, {'type': 'interactive', 'title': 'TVL Analysis Framework', 'description': "Learn to analyze Total Value Locked: (1) Is TVL organic or incentive-driven? (2) What's the TVL concentration (one whale vs distributed)? (3) How sticky is the TVL (withdrawal penalties, lock periods)? (4) TVL trend over 30/90 days."}, {'type': 'quiz', 'question': "A DeFi protocol's TVL dropped from $500M to $50M in one week. The most likely cause is:", 'options': ['Normal market fluctuations', 'A security incident, exploit, or mass exodus due to a vulnerability disclosure', 'Everyone went on vacation', 'The protocol upgraded'], 'correct': 1, 'explanation': 'A 90% TVL drop in a week is almost always a sign of catastrophic failure: an exploit, a critical vulnerability disclosure, or a bank run after bad news. Investigate immediately.'}, {'type': 'interactive', 'title': 'Oracle Dependency Mapping', 'description': "Map a DeFi protocol's oracle dependencies: Which price feeds does it use? Chainlink? A custom TWAP? A single DEX pool? If the oracle can be manipulated, every product relying on it is at risk. Build a dependency graph."}, {'type': 'quiz', 'question': "What is a 'liquidation cascade' and why is it dangerous?", 'options': ['A waterfall in a DeFi UI', 'When falling prices trigger liquidations, which push prices lower, triggering more liquidations in a death spiral', 'A type of token launch', 'A governance proposal'], 'correct': 1, 'explanation': 'In lending protocols, falling collateral values trigger liquidations. The liquidated collateral is sold, pushing prices lower, triggering more liquidations. In extreme cases, this can drain an entire lending market (see: Black Thursday on MakerDAO).'}]},
}

__all__ = ["defi_protocol_risk_assessment"]
