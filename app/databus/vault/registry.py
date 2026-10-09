"""Vault key registry - all known API keys grouped by provider."""

# Every API key the system knows about, grouped by provider.
# Format: (env_var_name, vault_path)
# vault_path is relative to rmi/ prefix in pass store.
KEY_REGISTRY = {
    # ── Solana RPC (3 keys = 75 RPS free tier combined) ──
    "helius": [
        ("HELIUS_API_KEY", "infra/helius_api_key"),
        ("HELIUS_API_KEY_2", "infra/helius_api_key_2"),
        ("HELIUS_API_KEY_3", "infra/helius_api_key_3"),
    ],
    # ── Indexed Data ──
    "solana_tracker": [
        ("SOLANATRACKER_API_KEY", "infra/soltracker_api_key"),
    ],
    "birdeye": [
        ("BIRDEYE_API_KEY", "infra/birdeye_api_key"),
    ],
    "solscan": [
        ("SOLSCAN_API_KEY", "infra/solscan_api_key"),
    ],
    # ── EVM ──
    "moralis": [
        ("MORALIS_API_KEY", "infra/moralis_api_key"),
        ("MORALIS_API_KEY_2", "infra/moralis_api_key_2"),
        ("MORALIS_API_KEY_3", "infra/moralis_api_key_3"),
    ],
    "etherscan": [
        ("ETHERSCAN_API_KEY", "infra/etherscan_api_key"),
        ("BSCSCAN_API_KEY", "api/bscscan_api_key"),
        ("POLYGONSCAN_API_KEY", "api/polygonscan_api_key"),
        ("SNOWTRACE_API_KEY", "api/snowtrace_api_key"),
        ("FTMSCAN_API_KEY", "api/ftmscan_api_key"),
    ],
    "alchemy": [
        ("ALCHEMY_API_KEY", "infra/alchemy_api_key"),
    ],
    "quicknode": [
        ("QUICKNODE_KEY", "infra/quicknode_api_key"),
    ],
    # ── Market Data ──
    "coingecko_pro": [
        ("COINGECKO_API_KEY", "infra/coingecko_api_key_pro"),
    ],
    "coinmarketcap": [
        ("COINMARKETCAP_API_KEY", "infra/coinmarketcap_api_key"),
    ],
    # ── Intelligence ──
    "arkham": [
        ("ARKHAM_API_KEY", "infra/arkham_api_key"),
        ("ARKHAM_WS_KEY", "infra/arkham_ws_key"),
    ],
    "goplus": [
        ("GOPLUS_API_KEY", "api/goplus_api_key"),
    ],
    "nansen": [
        ("NANSEN_API_KEY", "infra/nansen_api_key"),
    ],
    "dune": [
        ("DUNE_API_KEY", "infra/dune_api_key"),
        ("DUNE_API_KEY_2", "infra/dune_api_key_2"),
    ],
    # ── LLM / AI ──
    "openrouter": [
        ("OPENROUTER_API_KEY", "backend/openrouter_api_key"),
    ],
    "deepseek": [
        ("DEEPSEEK_API_KEY", "backend/deepseek_api_key"),
    ],
    "gemini": [
        ("GEMINI_API_KEY", "backend/gemini_api_key"),
    ],
    # ── Infra ──
    "blockscout": [
        ("BLOCKSCOUT_API_KEY", "api/blockscout_api_key"),
    ],
    "bitquery": [
        ("BITQUERY_API_KEY", "backend/bitquery_api_key"),
        ("BITQUERY_OAUTH_TOKEN", "backend/bitquery_oauth_token"),
    ],
    # ── Social ──
    "lunarcrush": [
        ("LUNARCRUSH_API_KEY", "infra/lunarcrush_api_key"),
    ],
    # ── Web3 ──
    "thegraph": [
        ("THEGRAPH_API_KEY", "infra/thegraph_api_key"),
    ],
    "apify": [
        ("APIFY_TOKEN", "infra/apify_token"),
    ],
    # ── GMGN ──
    "gmgn": [
        ("GMGN_API_KEY", "api/gmgn_api_key"),
    ],
    # ── Alibaba / DashScope LLM ──
    "dashscope": [
        ("DASHSCOPE_API_KEY", "api/dashscope_api_key"),
    ],
    # ── News / media data ──
    "cryptopanic": [
        ("CRYPTOPANIC_API_KEY", "api/cryptopanic_api_key"),
    ],
    "news_data": [
        ("MESSARI_API_KEY", "api/messari_api_key"),
        ("COINDESK_API_KEY", "api/coindesk_api_key"),
        ("SANTIMENT_API_KEY", "api/santiment_api_key"),
        ("VIRUSTOTAL_API_KEY", "api/virustotal_api_key"),
    ],
    "x_api": [
        ("X_API_KEY", "api/x_api_key"),
        ("TWITTER_BEARER_TOKEN", "api/twitter_bearer_token"),
    ],
    "getxapi": [
        ("GETXAPI_API_KEY", "getxapi/api_key"),
    ],
    # ── Social attribution ──
    "lens": [
        ("LENS_API_KEY", "api/lens_api_key"),
    ],
    "lookonchain": [
        ("LOOKONCHAIN_API_KEY", "api/lookonchain_api_key"),
    ],
    # ── LLM providers ──
    "groq": [
        ("GROQ_API_KEY", "backend/groq_api_key"),
    ],
    "mistral": [
        ("MISTRAL_API_KEY", "backend/mistral_api_key"),
    ],
    # ── Farcaster / social ──
    "neynar": [
        ("NEYNAR_API_KEY", "api/neynar_api_key"),
        ("FARCASTER_SIGNER_UUID", "api/farcaster_signer_uuid"),
    ],
    # ── CMS / bot ──
    "ghost": [
        ("GHOST_ADMIN_API_KEY", "api/ghost_admin_api_key"),
        ("GHOST_CONTENT_API_KEY", "api/ghost_content_api_key"),
    ],
    "telegram": [
        ("TELEGRAM_BOT_TOKEN", "infra/telegram_bot_token"),
    ],
    # ── RAG / embedding ──
    "rugbrain": [
        ("RMI_RAG_API_KEY", "backend/rmi_rag_api_key"),
    ],
    "deepinfra": [
        ("DEEPINFRA_API_KEY", "backend/deepinfra_api_key"),
    ],
    # ── Internal admin ──
    "admin": [
        ("ADMIN_API_KEY", "infra/admin_api_key"),
    ],
}
