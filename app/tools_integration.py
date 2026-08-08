"""
RMI Tools Integration - Wires all installed open-source tools into the backend.
Tools wired: Ollama, Foundry, Slither, ccxt, web3, blogwatcher, LiteLLM, Vault.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════
# OLLAMA - Local LLM (port 11434, 2 models)
# ═══════════════════════════════════════════════════════════
_DOCKER_HOST = os.getenv("DOCKER_HOST_IP", "172.17.0.1")
OLLAMA_URL = os.getenv("OLLAMA_URL", f"http://{_DOCKER_HOST}:11434")


async def ollama_chat(prompt: str, model: str = "phi3:mini", system: str = "") -> dict:
    """Use local Ollama for free AI inference."""
    try:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                },
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "text": data.get("message", {}).get("content", ""),
                    "model": model,
                    "provider": "ollama-local",
                    "usage": {
                        "prompt_tokens": data.get("prompt_eval_count", 0),
                        "completion_tokens": data.get("eval_count", 0),
                    },
                }
    except (httpx.HTTPError, TimeoutError, OSError) as e:
        logger.debug(f"Ollama unavailable: {e}")
    return {"error": "Ollama unavailable"}


async def ollama_list_models() -> list[str]:
    """Get available Ollama models."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, TimeoutError, OSError):
        pass
    return []


# ═══════════════════════════════════════════════════════════
# FOUNDRY - EVM smart contract analysis (cast)
# ═══════════════════════════════════════════════════════════
CAST_PATH = "/root/.foundry/bin/cast"


def cast_contract_info(address: str, chain: str = "ethereum") -> dict:
    """Get contract bytecode, ABI, and metadata using cast."""
    rpcs = {
        "ethereum": os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com"),
        "base": os.getenv("BASE_RPC_URL", "https://base.llamarpc.com"),
        "arbitrum": os.getenv("ARB_RPC_URL", "https://arb1.arbitrum.io/rpc"),
        "polygon": os.getenv("POLY_RPC_URL", "https://polygon.llamarpc.com"),
        "bsc": os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
        "avalanche": os.getenv("AVAX_RPC_URL", "https://api.avax.network/ext/bc/C/rpc"),
    }
    rpc = rpcs.get(chain, rpcs["ethereum"])
    try:
        env = os.environ.copy()
        env["ETH_RPC_URL"] = rpc

        # Get bytecode
        code = subprocess.run(
            [CAST_PATH, "code", address, "--rpc-url", rpc],
            capture_output=True,
            text=True,
            timeout=15,
        shell=False)
        bytecode = code.stdout.strip()

        # Get nonce
        nonce = subprocess.run(
            [CAST_PATH, "nonce", address, "--rpc-url", rpc],
            capture_output=True,
            text=True,
            timeout=10,
        shell=False)

        # Get balance
        balance = subprocess.run(
            [CAST_PATH, "balance", address, "--rpc-url", rpc],
            capture_output=True,
            text=True,
            timeout=10,
        shell=False)

        return {
            "address": address,
            "chain": chain,
            "has_bytecode": len(bytecode) > 4,
            "bytecode_size": len(bytecode) // 2 if bytecode.startswith("0x") else 0,
            "is_contract": len(bytecode) > 4,
            "nonce": nonce.stdout.strip(),
            "balance_wei": balance.stdout.strip(),
            "balanced_checked": datetime.now(UTC).isoformat(),
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        return {
            "address": address,
            "chain": chain,
            "error": str(e),
            "cast_available": os.path.exists(CAST_PATH),
        }


def cast_tx_decode(tx_hash: str, chain: str = "ethereum") -> dict:
    """Decode a transaction using cast."""
    rpcs = {
        "ethereum": "https://eth.llamarpc.com",
        "base": "https://base.llamarpc.com",
        "arbitrum": "https://arb1.arbitrum.io/rpc",
        "bsc": "https://bsc-dataseed.binance.org",
    }
    rpc = rpcs.get(chain, rpcs["ethereum"])
    try:
        result = subprocess.run(
            [CAST_PATH, "tx", tx_hash, "--rpc-url", rpc, "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        shell=False)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return {"error": "Decode failed", "tx_hash": tx_hash}


# ═══════════════════════════════════════════════════════════
# SLITHER - Solidity vulnerability scanner
# ═══════════════════════════════════════════════════════════
def slither_analyze(contract_address: str, chain: str = "ethereum") -> dict:
    """Run Slither on a verified contract."""
    try:
        result = subprocess.run(
            ["slither", contract_address, "--print", "human-summary"],
            capture_output=True,
            text=True,
            timeout=60,
        shell=False)
        return {
            "address": contract_address,
            "chain": chain,
            "analysis": result.stdout[:2000] if result.returncode == 0 else result.stderr[:500],
            "success": result.returncode == 0,
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": str(e), "slither_available": False}


# ═══════════════════════════════════════════════════════════
# CCXT - 100+ exchange price feeds
# ═══════════════════════════════════════════════════════════
def ccxt_get_prices(symbols: list[str] | None = None) -> dict:
    """Get real-time prices from multiple exchanges via ccxt."""
    try:
        import ccxt

        exchanges = {
            "binance": ccxt.binance(),
            "kraken": ccxt.kraken(),
            "coinbase": ccxt.coinbase(),
            "bybit": ccxt.bybit(),
        }
        results = {}
        default_symbols = [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "BNB/USDT",
            "ARB/USDT",
            "MATIC/USDT",
            "AVAX/USDT",
        ]
        for sym in symbols or default_symbols:
            for ex_name, ex in exchanges.items():
                try:
                    ticker = ex.fetch_ticker(sym)
                    if ticker and ticker.get("last"):
                        results.setdefault(sym, {})[ex_name] = {
                            "price": ticker["last"],
                            "change_24h": ticker.get("percentage", ticker.get("change", 0)),
                            "volume_24h": ticker.get("quoteVolume", ticker.get("baseVolume", 0)),
                        }
                except (ConnectionError, TimeoutError, OSError):
                    pass
        return {
            "prices": results,
            "exchanges": list(exchanges.keys()),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    except ImportError:
        return {"error": "ccxt not installed", "prices": {}}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": str(e), "prices": {}}


def ccxt_arbitrage(symbol: str = "BTC/USDT") -> dict:
    """Find arbitrage opportunities across exchanges."""
    prices = ccxt_get_prices([symbol])
    price_data = prices.get("prices", {}).get(symbol, {})
    if len(price_data) >= 2:
        prices_list = [(ex, p["price"]) for ex, p in price_data.items()]
        lowest = min(prices_list, key=lambda x: x[1])
        highest = max(prices_list, key=lambda x: x[1])
        spread = ((highest[1] - lowest[1]) / lowest[1]) * 100
        return {
            "symbol": symbol,
            "lowest": {"exchange": lowest[0], "price": lowest[1]},
            "highest": {"exchange": highest[0], "price": highest[1]},
            "spread_pct": round(spread, 4),
            "profitable": spread > 0.5,
        }
    return {"symbol": symbol, "opportunities": 0}


# ═══════════════════════════════════════════════════════════
# WEB3.PY - Direct Ethereum interaction
# ═══════════════════════════════════════════════════════════
def web3_wallet_balance(address: str, chain: str = "ethereum") -> dict:
    """Get wallet balance and token holdings via web3."""
    try:
        from web3 import Web3

        rpcs = {
            "ethereum": "https://eth.llamarpc.com",
            "base": "https://base.llamarpc.com",
            "bsc": "https://bsc-dataseed.binance.org",
            "polygon": "https://polygon.llamarpc.com",
            "arbitrum": "https://arb1.arbitrum.io/rpc",
            "avalanche": "https://api.avax.network/ext/bc/C/rpc",
        }
        rpc = rpcs.get(chain, rpcs["ethereum"])
        w3 = Web3(Web3.HTTPProvider(rpc))

        if not w3.is_connected():
            return {"error": f"RPC unavailable for {chain}"}

        checksum = w3.to_checksum_address(address)
        balance = w3.eth.get_balance(checksum)
        tx_count = w3.eth.get_transaction_count(checksum)
        code = w3.eth.get_code(checksum)

        return {
            "address": address,
            "chain": chain,
            "balance_eth": round(w3.from_wei(balance, "ether"), 6),
            "balance_wei": balance,
            "transaction_count": tx_count,
            "is_contract": len(code) > 0,
            "rpc": rpc,
        }
    except ImportError:
        return {"error": "web3 not installed"}
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"error": str(e), "address": address, "chain": chain}


# ═══════════════════════════════════════════════════════════
# BLOGWATCHER - RSS/Atom feed monitoring
# ═══════════════════════════════════════════════════════════
BLOGWATCHER_PATH = "/root/go/bin/blogwatcher"


def blogwatcher_fetch(feed_url: str, limit: int = 10) -> dict:
    """Fetch RSS/Atom feed using blogwatcher."""
    try:
        result = subprocess.run(
            [
                BLOGWATCHER_PATH,
                "fetch",
                "--url",
                feed_url,
                "--limit",
                str(limit),
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        shell=False)
        if result.returncode == 0:
            posts = json.loads(result.stdout) if result.stdout.strip() else []
            return {"feed": feed_url, "posts": posts, "count": len(posts)}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.debug(f"blogwatcher failed: {e}")
    return {"feed": feed_url, "posts": [], "error": "blogwatcher unavailable"}


# ═══════════════════════════════════════════════════════════
# LITELLM - Multi-provider LLM proxy routing
# ═══════════════════════════════════════════════════════════
LITELLM_CONFIG = "/srv/rugmuncher-backend/litellm/config.yaml"


def litellm_status() -> dict:
    """Check LiteLLM proxy availability."""
    config_exists = os.path.exists(LITELLM_CONFIG)
    return {
        "available": config_exists,
        "config_path": LITELLM_CONFIG,
        "config_size": os.path.getsize(LITELLM_CONFIG) if config_exists else 0,
    }


# ═══════════════════════════════════════════════════════════
# VAULT - Secrets management
# ═══════════════════════════════════════════════════════════
VAULT_ADDR = os.getenv("VAULT_ADDR", f"http://{_DOCKER_HOST}:8200")


async def vault_get_secret(path: str) -> dict | None:
    """Retrieve a secret from HashiCorp Vault."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{VAULT_ADDR}/v1/{path}", headers={"X-Vault-Token": "root"})
            if r.status_code == 200:
                return r.json().get("data", {}).get("data", {})
    except (httpx.HTTPError, TimeoutError, OSError):
        pass
    return None


async def vault_list_secrets(path: str = "secret") -> list[str]:
    """List secrets in Vault."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{VAULT_ADDR}/v1/{path}?list=true", headers={"X-Vault-Token": "root"})
            if r.status_code == 200:
                return r.json().get("data", {}).get("keys", [])
    except (httpx.HTTPError, TimeoutError, OSError):
        pass
    return []


# ═══════════════════════════════════════════════════════════
# TOOL STATUS - Health check for all integrated tools
# ═══════════════════════════════════════════════════════════
async def tools_health() -> dict:
    """Health check for all integrated tools."""
    return {
        "ollama": {
            "available": bool(await ollama_list_models()),
            "models": await ollama_list_models(),
            "url": OLLAMA_URL,
        },
        "foundry": {"installed": os.path.exists(CAST_PATH), "path": CAST_PATH},
        "slither": {"installed": os.path.exists("/root/.local/bin/slither")},
        "ccxt": {
            "available": True  # pip installed
        },
        "web3": {"available": True},
        "blogwatcher": {"installed": os.path.exists(BLOGWATCHER_PATH), "path": BLOGWATCHER_PATH},
        "litellm": litellm_status(),
        "vault": {"available": bool(await vault_list_secrets()), "url": VAULT_ADDR},
        "file_upload": {
            "running": True,  # port 8001
            "url": "http://localhost:8001",
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
