"""Shared constants for the risk_cluster package.

Query / analysis limits (time-boxing — matches sibling engines) and the
zero/dead address markers used across the graph builders and ClickHouse
queries. Kept in one module so every submodule reads the same values.
"""

from __future__ import annotations

# ── Query / analysis limits (time-boxing — matches sibling engines) ───────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_HOLDERS = 50  # top-N holders fed into the cross-token overlap scan
_MAX_OVERLAP_TOKENS = 20  # other tokens considered for cross-token clusters
_MIN_FUNDER_CHILDREN = 2  # funder with >= 2 holders of this token = tree root
_MIN_CROSS_TOKEN_MEMBERS = 2  # wallet group size for a cross-token cluster
_MIN_SHARED_DEPLOYER_MEMBERS = 2  # wallet overlap size for shared-deployer net

# ── Deepened cluster limits (legs 4-8) ────────────────────────────────────
_MAX_FUNDER_ROWS = 100  # funder×token rows fed into the cross-token funder graph
_MIN_CROSS_TOKEN_FUNDER_TOKENS = 2  # launches a funder must seed to be a graph node
_MIN_TEMPORAL_MEMBERS = 2  # wallets entering in the same block = lockstep
_TEMPORAL_BLOCK_WINDOW = 3  # consecutive blocks merged into a rapid sequence
_LAUNCH_WINDOW_BLOCKS = 30  # blocks after deploy treated as the launch window
_MAX_LAUNCH_ROWS = 200  # launch-window rows fed into sniper / MEV detection
_BOT_CV_THRESHOLD = 0.25  # coefficient of variation → uniform (bot) amounts
_MEV_AMOUNT_BAND = 0.30  # CV of atomic bundle-buy amounts
_MEV_TIP_BAND_PCT = 5.0  # tips within 5% of each other = same bundle
_WASH_LOOKBACK_HOURS = 72  # coordination window for wash-trade cycles
_MAX_WASH_TRANSFER_ROWS = 500  # transfer rows fed into wash-cycle detection
_MIN_WASH_CYCLE_LEN = 2  # shortest cycle (A→B→A roundtrip)
_MAX_WASH_CYCLE_LEN = 5  # longest cycle considered coordinated

_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"
