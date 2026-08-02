"""Scanner service — placeholder module for future API-key'd integrations.

Previously contained 17 stub check functions that always returned
``{"result": "unavailable"}``. Those stubs were removed 2026-08-02:

- The 4 ELITE-tier stubs (``_check_nansen``, ``_check_blowfish``,
  ``_check_dune``, ``_check_arkham``) were wired into ``scan.py`` and
  silently inflated confidence scores for paying ELITE users while never
  returning data. They are no longer advertised in the ELITE tier.
- The remaining 13 stubs were never wired into any scanner tier (dead code).

Do NOT re-add hardcoded "unavailable" stubs. When a real integration with
API keys exists, implement it as a real module in
``app/domains/scanner/modules/`` and wire it into the appropriate tier in
``app/domains/scanner/service/scan.py``.

# UNIMPLEMENTED - awaiting API keys
# Removed: _get_birdeye_data, _verify_mint_consensus, _get_solscan_data,
#          _get_moralis_data, _get_etherscan_enhanced, _get_quicknode_pumpfun,
#          _check_nansen, _check_chainaware, _check_blowfish,
#          _check_santiment, _check_news_sentiment, _check_augmento,
#          _check_webacy, _check_scamsniffer_live, _check_dune,
#          _check_arkham, _check_thegraph
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)
