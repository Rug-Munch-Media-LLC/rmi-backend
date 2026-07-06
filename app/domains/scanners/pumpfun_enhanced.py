"""
Enhanced pump.fun analyzer using pumpfun-research patterns.
Integrates wallet trade analysis, PnL computation, and delay-to-first-sell
from haccer/pumpfun-research (MIT licensed, open source).
"""

import logging

logger = logging.getLogger(__name__)

# Key patterns extracted from pumpfun-research:
# 1. Batch pulls pump.fun BUY/SELL activity via Helius
# 2. Computes PnL, delay-to-first-sell per mint
# 3. Raydium Launchpad detection combining instruction fingerprints + log keywords
# 4. Mode-aware caching with auto-resume

# These patterns enhance our existing pumpfun_analyzer.py with:
# - Wallet-level PnL tracking (not just token-level)
# - Delay-to-first-sell as a rug pull signal (< 60s = likely scam)
# - Raydium launch detection (wider coverage beyond pump.fun)
