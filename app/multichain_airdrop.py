"""
Multi-Chain Airdrop Engine
==========================
Manages airdrop campaigns across multiple blockchains, including eligibility
checking, distribution, and claim verification.

Backward-compatibility shim: this module now lives in ``app.domains.airdrop``.

Usage:
    from app.multichain_airdrop import AirdropEngine
"""

from app.domains.airdrop.multichain_airdrop import *  # noqa: F401, F403
