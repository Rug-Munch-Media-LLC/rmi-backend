"""Scanner engines — data sources and detection modules.

Phase 1 (Complete):
  - own_price_oracle.py — CoinGecko replacement (TWAP/VWAP from dex_events)
  - own_dex_data.py — DexScreener replacement (DEX data from dex_events)

Phase 2 (Current):
  - own_security_analysis.py — GoPlus replacement (security from evm_transfers + deployer_events)
  - own_holder_analysis.py — Blockscout holder replacement (holders from evm_transfers)

Phase 4:
  - own_deployer_reputation.py — deployer scoring (deployer_events + dex_events)
  - own_whale_detect.py — whale wallets (dex_events + price oracle)
  - own_volume_authenticity.py — wash trading detection (dex_events + evm_transfers)
  - own_holder_age.py — holder vintage analysis (evm_transfers + deployer_events)

Phase 5 (RugMunchScan capabilities):
  - wallet_flow.py — C1 wallet-to-wallet flow visualization (evm_transfers + wallet_labels)
  - holder_clusters.py — C2 BubbleMaps-style holder clustering (evm_transfers + dex_events + wallet_labels)
  - new_token_radar.py — C3 new token discovery + early warning (deployer_events)
  - hype_score.py — C5 composite hype score (dex_events + evm_transfers + trending)
  - cross_chain_entity.py — C4 cross-chain entity resolution v1 (deployer_events + evm_transfers)
"""

# Public API exports
from app.domains.scanner.engines.cross_chain_entity import resolve_cross_chain_entity
from app.domains.scanner.engines.holder_clusters import analyze_holder_clusters
from app.domains.scanner.engines.own_deployer_reputation import get_own_deployer_reputation
from app.domains.scanner.engines.own_dex_data import check_data_freshness, get_own_dex_data
from app.domains.scanner.engines.own_holder_age import get_own_holder_age
from app.domains.scanner.engines.own_holder_analysis import get_holder_count, get_own_holder_analysis
from app.domains.scanner.engines.own_price_oracle import get_batch_prices, get_token_price
from app.domains.scanner.engines.own_security_analysis import get_own_security
from app.domains.scanner.engines.own_volume_authenticity import get_own_volume_authenticity
from app.domains.scanner.engines.own_whale_detect import get_own_whale_detection

__all__ = [
    # Phase 1
    "get_token_price",
    "get_batch_prices",
    "get_own_dex_data",
    "check_data_freshness",
    # Phase 2
    "get_own_holder_analysis",
    "get_holder_count",
    "get_own_security",
    # Phase 4
    "get_own_deployer_reputation",
    "get_own_holder_age",
    "get_own_volume_authenticity",
    "get_own_whale_detection",
    # Phase 5 — RugMunchScan capabilities
    "analyze_holder_clusters",
    "resolve_cross_chain_entity",
]
