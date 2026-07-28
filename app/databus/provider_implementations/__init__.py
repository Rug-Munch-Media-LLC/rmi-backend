"""
DataBus Provider Implementations — Split into focused modules.
Re-exports all functions from submodules for backward compatibility.
"""

# Price providers
# Alchemy
from .alchemy import (
    _alchemy_token_balances,
    _alchemy_token_metadata,
)

# Arkham
from .arkham import (
    _arkham_counterparties,
    _arkham_entity,
    _arkham_intel_search,
    _arkham_labels,
    _arkham_portfolio,
    _arkham_transfers,
)

# Free data providers
from .free_data import (
    _birdeye_overview,
    _blockchair_address,
    _blockchair_stats,
    _defillama_chains,
    _defillama_tvl,
    _dexscreener_holders,
    _dexscreener_token_metadata,
    _dexscreener_top_traders,
    _dexscreener_trades,
    _etherscan_tx_trace,
)

# MCP Bridge
from .mcp_bridge import (
    _mcp_bridge,
)

# Moralis
from .moralis import (
    _moralis_search_tokens,
    _moralis_token_metadata,
    _moralis_token_price,
    _moralis_wallet_net_worth,
    _moralis_wallet_nfts,
    _moralis_wallet_tokens,
    _moralis_wallet_transactions,
)

# News & social
from .news import (
    _coindesk_news,
    _dune_early_buyers,
    _messari_news,
    _santiment_dev_activity,
    _virustotal_url_scan,
)

# Passthrough (local backend endpoints)
from .passthrough import (
    _local_wallet_labels,
    _local_wallet_labels_batch,
    _passthrough_alerts,
    _passthrough_market_overview,
    _passthrough_news,
    _passthrough_rag,
    _passthrough_scanner,
    _passthrough_trending,
)

# Extra passthrough
from .passthrough_extra import (
    _passthrough_hyperliquid,
    _passthrough_hyperliquid_action,
    _passthrough_insider_wallets,
    _passthrough_prediction_signals,
)
from .price import (
    _birdeye_price,
    _coingecko_price,
    _dexscreener_price,
    _local_token_price,
    _moralis_price,
    _solana_tracker_price,
    _solana_tracker_token,
    _solana_tracker_trending,
)

__all__ = [
    # Alchemy
    "_alchemy_token_balances",
    "_alchemy_token_metadata",
    "_arkham_counterparties",
    # Arkham
    "_arkham_entity",
    "_arkham_intel_search",
    "_arkham_labels",
    "_arkham_portfolio",
    "_arkham_transfers",
    "_birdeye_overview",
    "_birdeye_price",
    "_blockchair_address",
    "_blockchair_stats",
    "_coindesk_news",
    "_coingecko_price",
    "_defillama_chains",
    "_defillama_tvl",
    "_dexscreener_holders",
    "_dexscreener_price",
    # Free data
    "_dexscreener_token_metadata",
    "_dexscreener_top_traders",
    "_dexscreener_trades",
    "_dune_early_buyers",
    "_etherscan_tx_trace",
    # Price
    "_local_token_price",
    "_local_wallet_labels",
    "_local_wallet_labels_batch",
    # MCP Bridge
    "_mcp_bridge",
    # News
    "_messari_news",
    "_moralis_price",
    "_moralis_search_tokens",
    "_moralis_token_metadata",
    "_moralis_token_price",
    "_moralis_wallet_net_worth",
    "_moralis_wallet_nfts",
    # Moralis
    "_moralis_wallet_tokens",
    "_moralis_wallet_transactions",
    "_passthrough_alerts",
    # Extra passthrough
    "_passthrough_hyperliquid",
    "_passthrough_hyperliquid_action",
    "_passthrough_insider_wallets",
    # Passthrough
    "_passthrough_market_overview",
    "_passthrough_news",
    "_passthrough_prediction_signals",
    "_passthrough_rag",
    "_passthrough_scanner",
    "_passthrough_trending",
    "_santiment_dev_activity",
    "_solana_tracker_price",
    "_solana_tracker_token",
    "_solana_tracker_trending",
    "_virustotal_url_scan",
]
