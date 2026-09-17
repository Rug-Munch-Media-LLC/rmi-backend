"""regulatory-compliance-landscape module."""
from __future__ import annotations

regulatory_compliance_landscape = {
    "title": 'Regulatory & Compliance Landscape',
    "type": 'interactive',
    "duration": 20,
    "xp": 300,
    "image": 'https://commons.wikimedia.org/wiki/Special:FilePath/Scale_of_justice_2.svg?width=800',
    "image_alt": 'Scales of justice icon representing the regulatory and compliance landscape',
    "content": {'slides': [{'type': 'title', 'text': 'The Law Comes for Crypto', 'sub': 'Understanding the regulatory environment and its impact'}, {'type': 'interactive', 'title': 'OFAC Compliance in DeFi', 'description': 'Explore the OFAC SDN list and its impact on DeFi. Learn which addresses are sanctioned, how protocols implement compliance (blocking, geo-restrictions), and the controversy around censorship-resistant protocols.'}, {'type': 'quiz', 'question': 'What was the impact of OFAC sanctioning Tornado Cash?', 'options': ['Nothing changed', 'It became illegal for US persons to interact with Tornado Cash; many protocols blacklisted sanctioned addresses; it sparked debate about privacy vs compliance', 'Tornado Cash was shut down completely', "Only Tornado Cash's website was affected"], 'correct': 1, 'explanation': 'The OFAC sanctions made it illegal for US persons to use Tornado Cash. Major protocols and RPC providers began blocking sanctioned addresses. It created a precedent that immutable smart contracts can be sanctioned.'}, {'type': 'interactive', 'title': 'KYC/AML in DeFi: The Tension', 'description': 'Map the regulatory landscape: which jurisdictions require KYC for what activities? Compare the FATF Travel Rule, EU MiCA regulation, US FinCEN guidance, and how they affect DeFi protocols differently from CeFi.'}, {'type': 'quiz', 'question': "What is the 'Travel Rule' in the context of crypto?", 'options': ['A rule about travel expenses', 'A FATF requirement that crypto transactions above a threshold must include sender and receiver identity information', 'A rule about gas fees', 'A rule about blockchain speed'], 'correct': 1, 'explanation': 'The FATF Travel Rule requires Virtual Asset Service Providers (VASPs) to share sender and receiver information for transactions above certain thresholds, similar to wire transfer rules in traditional banking.'}]},
}

__all__ = ["regulatory_compliance_landscape"]
