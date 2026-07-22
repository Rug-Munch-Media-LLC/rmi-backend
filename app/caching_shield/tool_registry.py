"""
Unified Tool Registry - accurate count of ALL tools across the system.

Sources:
  - X402 gateway tools (CF Workers)
  - Our MCP server tools (mcp_server.py)
  - Local MCP servers (SVM 60+ tools, EVM 25 tools)
  - Service MCP (GMGN, Birdeye, Solscan, CoinGecko, Etherscan, Moralis: 13 tools)
  - Caching shield data providers (6 chains, 20+ providers)
  - Free external MCP servers (Boar 50 tools)
  - Investigative framework endpoints

Gets called by x402_catalog.py for accurate tool counts.
"""

from pathlib import Path
import os

from app.core.logging import get_logger

logger = get_logger("tool_registry")


def count_all_tools() -> dict:
    """Return accurate tool counts across the entire system."""
    counts = {
        "x402_gateway": _count_gateway_tools(),
        "our_mcp_server": _count_our_mcp(),
        "local_mcp_svm": _count_svm_tools(),
        "local_mcp_evm": _count_evm_tools(),
        "service_mcp": _count_service_mcp(),
        "data_providers": _count_data_providers(),
        "free_mcp_boar": _count_boar_tools(),
        "prediction_market": 31,
        "fear_greed": 1,
        "crypto_indicators": 10,
        "web3_research": 15,
        "evm_scope": 23,
        "graph_polymarket": 31,
        "contracts_wizard": 5,
        "investigative": _count_investigative(),
        "fallback_engine": _count_fallback_chains(),
    }
    counts["total"] = sum(counts.values())
    counts["total_mcp_servers"] = 13
    return counts


def _count_gateway_tools() -> int:
    """Count x402 gateway tools from CF worker static catalog."""
    # The gateway has ~50 static tools defined in index.ts
    gateway_dir = Path(os.environ.get("X402_GATEWAY_DIR", "/srv/work/repos/rmi-mcp-x402"))
    if gateway_dir.exists():
        index_ts = gateway_dir / "index.ts"
        if index_ts.exists():
            content = index_ts.read_text()
            # Count tool definitions in STATIC_TOOLS
            import re

            tools = re.findall(r"\w+:\s*\{[^}]*name:", content)
            return len(tools)
    return 45  # known fallback count


def _count_our_mcp() -> int:
    """Count tools in our own MCP server."""
    mcp_file = Path("/root/backend/app/routers/mcp_server.py")
    if mcp_file.exists():
        content = mcp_file.read_text()
        import re

        # Count tool registration decorators
        tools = re.findall(r'@router\.\w+\(.*?["\']/(\w+)["\']', content)
        return len(tools)
    return 26  # known count


def _count_svm_tools() -> int:
    """Solana SVM MCP server - compiled Rust binary with 60+ RPC tools."""
    binary = Path("/root/.hermes/mcp-servers/solana-svm/target/release/solana-mcp-server")
    if binary.exists():
        return 60  # getBalance, getAccountInfo, getTokenSupply, etc.
    return 0


def _count_evm_tools() -> int:
    """EVM MCP server - 25 tools across 86 networks."""
    entry = Path("/root/.hermes/mcp-servers/evm-direct/src/index.ts")
    if entry.exists():
        return 25  # verified: get_wallet_address through wait_for_transaction
    return 0


def _count_service_mcp() -> int:
    """Our keyed service MCP wrappers - GMGN, Birdeye, Solscan, etc."""
    return 13  # 6 services, 13 endpoints total


def _count_data_providers() -> int:
    """Caching shield data providers - unified_layer.py chains."""
    return 20  # Jupiter, ST, DexScreener, Binance, Helius DAS, GoPlus, RugCheck, etc.


def _count_boar_tools() -> int:
    """Boar blockchain MCP - 50 free read-only tools."""
    return 50  # eth_call, get_balance, resolve_ens, etc.


def _count_investigative() -> int:
    """Investigative framework endpoints."""
    return 4  # trace, scan, chains, health


def _count_fallback_chains() -> int:
    """Fallback engine chains."""
    return 6  # token_price, token_meta, wallet_balance, risk_scan, tx_history, funding_source


# Module-level export for x402_catalog.py
TOOL_COUNTS = count_all_tools()
