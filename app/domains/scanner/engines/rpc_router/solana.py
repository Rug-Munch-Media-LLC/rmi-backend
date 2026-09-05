"""Solana RPC endpoint registry."""

from __future__ import annotations

# ─── Solana ─────────────────────────────────────────────────────────

SOLANA_RPC_ENDPOINTS: list[str] = [
    "https://api.mainnet-beta.solana.com",  # Solana official public
    "https://solana-rpc.publicnode.com",  # PublicNode Solana
    "https://solana.drpc.org",  # dRPC Solana
    "https://rpc.ankr.com/solana",  # Ankr Solana
    "https://solana.meowrpc.com",  # MeowRPC Solana
    "https://solana.blockpi.network/v1/rpc/public",  # BlockPI Solana
    "https://solana-api.projectserum.com",  # Project Serum — long-standing free
    "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Solana (key)
    "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius (key, paid fallback)
    # ── Freemium (keyed) ──
    "https://lb.drpc.org/ogrpc?network=solana&dkey=${DRPC_API_KEY}",
    "https://rpc.ankr.com/solana/${ANKR_API_KEY}",
    "${QUICKNODE_SOLANA_HTTP}",
    "${CHAINSTACK_SOLANA_URL}",  # Chainstack shared node (verified 2026-08-23)
    "${GETBLOCK_SOLANA_URL}",  # GetBlock shared (verified 2026-08-23)
    "https://sol.nownodes.io/${NOWNODES_API_KEY}",  # NOWNodes (verified 2026-08-23)
]
