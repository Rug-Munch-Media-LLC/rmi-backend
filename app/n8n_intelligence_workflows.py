"""
n8n Workflow Automation - Market Intelligence & Premium Data Pulls.
Scheduled workflows for efficient data collection from multiple sources.
Runs every 30-60 minutes based on scan volume and data freshness needs.

Integrations:
- CoinGecko: trending, global metrics, top gainers/losers
- DexScreener: new pairs, trending tokens, volume spikes
- Dune Analytics: custom queries for whale tracking, scam patterns
- Helius: token mints, large transfers, new token deployments
- Moralis: EVM whale movements, new contract deployments
- Arkham: entity labeling, exchange flows
- Nansen: smart money tracking (if available)
- Forta: real-time threat detection
"""

from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── n8n Workflow Definitions ─────────────────────────────────

N8N_BASE_URL = "https://n8n.rugmunch.io"
N8N_WEBHOOK_URL = f"{N8N_BASE_URL}/webhook"

# Market Intelligence Workflows
MARKET_INTEL_WORKFLOWS = {
    "trending_tokens": {
        "name": "Trending Tokens Collector",
        "schedule": "*/30 * * * *",  # Every 30 minutes
        "sources": ["coingecko", "dexscreener", "geckoterminal"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/trending",
        "description": "Collect trending tokens from multiple DEXs",
        "output_table": "market_trending_tokens",
    },
    "whale_movements": {
        "name": "Whale Movement Tracker",
        "schedule": "*/15 * * * *",  # Every 15 minutes (high priority)
        "sources": ["helius", "moralis", "arkham"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/whales",
        "description": "Track large transfers across chains",
        "output_table": "whale_movements",
    },
    "new_token_deployments": {
        "name": "New Token Deployments",
        "schedule": "*/10 * * * *",  # Every 10 minutes (fast detection)
        "sources": ["helius", "moralis", "dexscreener"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/new-tokens",
        "description": "Detect new token deployments in real-time",
        "output_table": "new_token_deployments",
    },
    "volume_spikes": {
        "name": "Volume Spike Detector",
        "schedule": "*/5 * * * *",  # Every 5 minutes (critical)
        "sources": ["dexscreener", "geckoterminal", "birdeye"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/volume-spikes",
        "description": "Detect unusual volume increases",
        "output_table": "volume_spikes",
    },
    "global_metrics": {
        "name": "Market Global Metrics",
        "schedule": "0 * * * *",  # Every hour
        "sources": ["coingecko"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/global",
        "description": "Global crypto market metrics",
        "output_table": "market_global_metrics",
    },
    "defi_protocols": {
        "name": "DeFi Protocol Analytics",
        "schedule": "0 */2 * * *",  # Every 2 hours
        "sources": ["defillama", "dune"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/defi",
        "description": "DeFi TVL, volume, user metrics",
        "output_table": "defi_protocol_metrics",
    },
    "nft_market": {
        "name": "NFT Market Intelligence",
        "schedule": "0 */4 * * *",  # Every 4 hours
        "sources": ["alchemy", "opensea_api"],
        "endpoint": f"{N8N_WEBHOOK_URL}/market/nft",
        "description": "NFT floor prices, volume, trends",
        "output_table": "nft_market_metrics",
    },
}

# Premium Intelligence Workflows
PREMIUM_INTEL_WORKFLOWS = {
    "smart_money_tracking": {
        "name": "Smart Money Tracker",
        "schedule": "*/30 * * * *",  # Every 30 minutes
        "sources": ["nansen", "arkham", "dune"],
        "endpoint": f"{N8N_WEBHOOK_URL}/premium/smart-money",
        "description": "Track known smart money wallets",
        "output_table": "smart_money_activities",
        "tier": "premium",
    },
    "exchange_flows": {
        "name": "Exchange Flow Analysis",
        "schedule": "*/15 * * * *",  # Every 15 minutes
        "sources": ["arkham", "dune", "glassnode"],
        "endpoint": f"{N8N_WEBHOOK_URL}/premium/exchange-flows",
        "description": "Track exchange inflows/outflows",
        "output_table": "exchange_flows",
        "tier": "premium",
    },
    "insider_trading": {
        "name": "Insider Trading Detection",
        "schedule": "*/20 * * * *",  # Every 20 minutes
        "sources": ["dune", "arkham", "helius"],
        "endpoint": f"{N8N_WEBHOOK_URL}/premium/insider",
        "description": "Detect potential insider trading patterns",
        "output_table": "insider_trading_alerts",
        "tier": "premium_plus",
    },
    "launchpad_monitor": {
        "name": "Launchpad Monitor",
        "schedule": "*/10 * * * *",  # Every 10 minutes
        "sources": ["dexscreener", "pumpfun_api", "geckoterminal"],
        "endpoint": f"{N8N_WEBHOOK_URL}/premium/launchpad",
        "description": "Monitor new launches across platforms",
        "output_table": "launchpad_monitoring",
        "tier": "premium",
    },
    "cluster_analysis": {
        "name": "Cluster Pattern Analysis",
        "schedule": "0 */2 * * *",  # Every 2 hours
        "sources": ["internal_clustering", "dune"],
        "endpoint": f"{N8N_WEBHOOK_URL}/premium/clusters",
        "description": "Deep cluster pattern analysis",
        "output_table": "cluster_patterns",
        "tier": "premium_plus",
    },
}

# Security Intelligence Workflows
SECURITY_INTEL_WORKFLOWS = {
    "scam_detection": {
        "name": "Real-time Scam Detection",
        "schedule": "*/5 * * * *",  # Every 5 minutes (critical)
        "sources": ["forta", "internal_ml", "community_reports"],
        "endpoint": f"{N8N_WEBHOOK_URL}/security/scams",
        "description": "Detect new scam patterns",
        "output_table": "scam_alerts",
        "priority": "critical",
    },
    "rugpull_detection": {
        "name": "Rugpull Detection",
        "schedule": "*/2 * * * *",  # Every 2 minutes (ultra-critical)
        "sources": ["internal_ml", "liquidity_monitor"],
        "endpoint": f"{N8N_WEBHOOK_URL}/security/rugpulls",
        "description": "Detect rugpull patterns in real-time",
        "output_table": "rugpull_alerts",
        "priority": "critical",
    },
    "contract_vulnerabilities": {
        "name": "Contract Vulnerability Scanner",
        "schedule": "0 * * * *",  # Every hour
        "sources": ["slither", "mythril", "internal_scanner"],
        "endpoint": f"{N8N_WEBHOOK_URL}/security/vulnerabilities",
        "description": "Scan new contracts for vulnerabilities",
        "output_table": "contract_vulnerabilities",
        "priority": "high",
    },
    "phishing_detection": {
        "name": "Phishing Domain Detection",
        "schedule": "0 */6 * * *",  # Every 6 hours
        "sources": ["guardian", "cryptoscamdb", "community"],
        "endpoint": f"{N8N_WEBHOOK_URL}/security/phishing",
        "description": "Detect new phishing domains",
        "output_table": "phishing_domains",
        "priority": "high",
    },
}


# ── n8n Workflow Execution ────────────────────────────────────


async def trigger_n8n_workflow(workflow_name: str, data: dict | None = None) -> bool:
    """Trigger an n8n workflow via webhook."""
    import httpx

    # Find workflow config
    all_workflows = {
        **MARKET_INTEL_WORKFLOWS,
        **PREMIUM_INTEL_WORKFLOWS,
        **SECURITY_INTEL_WORKFLOWS,
    }
    workflow = all_workflows.get(workflow_name)

    if not workflow:
        logger.error(f"Workflow not found: {workflow_name}")
        return False

    webhook_url = workflow["endpoint"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                webhook_url,
                json={
                    "workflow": workflow_name,
                    "triggered_at": datetime.now(UTC).isoformat(),
                    "data": data or {},
                },
            )

            if response.status_code in (200, 201, 202):
                logger.info(f"Triggered workflow: {workflow_name}")
                return True
            else:
                logger.error(f"Workflow trigger failed: {workflow_name} - {response.status_code}")
                return False

    except Exception as e:
        logger.error(f"Workflow trigger error: {workflow_name} - {e}")
        return False


async def execute_market_intelligence_pull(workflow_name: str):
    """Execute a market intelligence data pull."""
    workflow = MARKET_INTEL_WORKFLOWS.get(workflow_name)
    if not workflow:
        return

    logger.info(f"Executing market intel pull: {workflow['name']}")

    # Collect data from sources
    collected_data = {
        "workflow": workflow_name,
        "sources": workflow["sources"],
        "collected_at": datetime.now(UTC).isoformat(),
        "data": {},
    }

    # Source-specific collection logic
    for source in workflow["sources"]:
        try:
            if source == "coingecko":
                from app.coingecko_connector import get_coingecko_connector

                connector = get_coingecko_connector()

                if "trending" in workflow_name.lower():
                    trending = await connector.get_trending()
                    collected_data["data"]["coingecko_trending"] = trending
                elif "global" in workflow_name.lower():
                    global_data = await connector.get_global_metrics()
                    collected_data["data"]["coingecko_global"] = global_data

            elif source == "dexscreener":
                from app.unified_provider import get_unified_provider

                provider = get_unified_provider()

                if "trending" in workflow_name.lower():
                    trending = await provider.get_dexscreener_trending()
                    collected_data["data"]["dexscreener_trending"] = trending
                elif "volume" in workflow_name.lower():
                    spikes = await provider.get_volume_spikes()
                    collected_data["data"]["dexscreener_spikes"] = spikes

            elif source == "helius":
                from app.chain_client import get_chain_client

                client = get_chain_client()

                if "new" in workflow_name.lower():
                    new_tokens = await client.get_new_token_mints(limit=50)
                    collected_data["data"]["helius_new_tokens"] = new_tokens
                elif "whale" in workflow_name.lower():
                    whales = await client.get_large_transfers(limit=20)
                    collected_data["data"]["helius_whales"] = whales

        except Exception as e:
            logger.error(f"Error collecting from {source}: {e}")
            collected_data["data"][source] = {"error": str(e)}

    # Store in Supabase
    await _store_intelligence_data(workflow["output_table"], collected_data)

    # Trigger alerts if needed
    await _process_intelligence_alerts(workflow_name, collected_data)


async def _store_intelligence_data(table_name: str, data: dict):
    """Store intelligence data in Supabase."""
    try:
        import os

        from supabase import create_client

        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

        # Insert data
        supabase.table(table_name).insert(
            {
                "data": data,
                "collected_at": data.get("collected_at"),
                "workflow": data.get("workflow"),
            }
        ).execute()

        logger.info(f"Stored intelligence data in {table_name}")

    except Exception as e:
        logger.error(f"Failed to store intelligence data: {e}")


async def _process_intelligence_alerts(workflow_name: str, data: dict):
    """Process intelligence data and trigger alerts."""
    # Check for significant findings
    if "whale" in workflow_name.lower():
        # Check for unusually large transfers
        whales = data.get("data", {}).get("helius_whales", [])
        for whale in whales:
            amount = whale.get("amount", 0)
            if amount > 1000:  # >1000 SOL
                await _create_alert("whale_movement", whale)

    elif "scam" in workflow_name.lower() or "rugpull" in workflow_name.lower():
        # Immediate alert for security issues
        await _create_alert("security_critical", data)

    elif "volume" in workflow_name.lower():
        # Check for unusual volume spikes
        spikes = data.get("data", {}).get("dexscreener_spikes", [])
        for spike in spikes:
            if spike.get("volume_change_pct", 0) > 500:  # >500% increase
                await _create_alert("volume_spike", spike)


async def _create_alert(alert_type: str, data: dict):
    """Create an intelligence alert."""
    try:
        import os

        from supabase import create_client

        supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

        alert_data = {
            "alert_type": alert_type,
            "severity": "critical" if "security" in alert_type else "high",
            "data": data,
            "created_at": datetime.now(UTC).isoformat(),
            "is_read": False,
        }

        supabase.table("intelligence_alerts").insert(alert_data).execute()

        logger.info(f"Created intelligence alert: {alert_type}")

    except Exception as e:
        logger.error(f"Failed to create alert: {e}")


# ── Dune Analytics Integration ────────────────────────────────


async def execute_dune_query(query_id: str, params: dict | None = None) -> dict | None:
    """Execute a Dune Analytics query."""
    import os

    import httpx

    dune_api_key = os.getenv("DUNE_API_KEY", "")
    if not dune_api_key:
        logger.warning("DUNE_API_KEY not configured")
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Execute query
            response = await client.post(
                f"https://api.dune.com/api/v1/query/{query_id}/execute",
                headers={"X-Dune-API-Key": dune_api_key},
                json=params or {},
            )

            if response.status_code != 200:
                logger.error(f"Dune query failed: {response.status_code}")
                return None

            execution_id = response.json().get("execution_id")

            # Wait for results
            import asyncio

            for _ in range(10):  # Max 10 attempts
                await asyncio.sleep(2)

                result_response = await client.get(
                    f"https://api.dune.com/api/v1/execution/{execution_id}/results",
                    headers={"X-Dune-API-Key": dune_api_key},
                )

                if result_response.status_code == 200:
                    result = result_response.json()
                    if result.get("state") == "QUERY_STATE_COMPLETED":
                        return result.get("result", {}).get("rows", [])

            return None

    except Exception as e:
        logger.error(f"Dune query error: {e}")
        return None


# Pre-configured Dune queries for RMI
DUNE_QUERIES = {
    "ethereum_whale_transfers": {
        "query_id": "1234567",  # Replace with actual query ID
        "description": "Track large ETH transfers from known whale wallets",
        "schedule": "*/15 * * * *",
    },
    "defi_protocol_volumes": {
        "query_id": "2345678",
        "description": "Daily DEX volumes across major protocols",
        "schedule": "0 * * * *",
    },
    "nft_wash_trading": {
        "query_id": "3456789",
        "description": "Detect potential NFT wash trading patterns",
        "schedule": "0 */6 * * *",
    },
    "stablecoin_flows": {
        "query_id": "4567890",
        "description": "Track USDC/USDT flows to/from exchanges",
        "schedule": "*/30 * * * *",
    },
    "new_contract_deployments": {
        "query_id": "5678901",
        "description": "New contract deployments with large funding",
        "schedule": "*/10 * * * *",
    },
}
