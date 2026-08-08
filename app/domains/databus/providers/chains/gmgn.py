"""
GMGN providers — free, no API key required (undocumented public JSON API).

Provides token info, wallet activity (smart-money stats), holder data,
new pump.fun launches, bonded pairs, and token trades from GMGN's public
``gmgn.ai`` endpoints. GMGN.ai is a free memecoin analytics site; its JSON
API is undocumented but stable and used widely by open-source trackers.

Endpoint provenance (verified against public open-source usage 2026-08-09):
  - token info:      GET /defi/quotation/v1/tokens/{chain}/{address}
                     → {"code":0,"data":{"token":{...}}}  (handi-cat_wallet-tracker, PumpFun)
  - wallet activity: GET /defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet}?period=7d
                     → {"data":{...}}  (Dragon, handi-cat_wallet-tracker, solanaWallet-checker)
  - token holders:   GET /vas/api/v1/token_holders/{chain}/{address}?orderby=amount_percentage&direction=desc&limit=100
                     → {"data":{"list":[...]}}  (Dragon)
  - new pump pairs:  GET /defi/quotation/v1/rank/{chain}/pump?limit=N&orderby=created_timestamp&direction=desc&new_creation=true
                     → {"code":0,"data":{"rank":[...]}}  (Dragon, PumpFun)
  - bonded pairs:    GET /defi/quotation/v1/pairs/{chain}/new_pairs/1h?limit=N&orderby=open_timestamp&direction=desc&launchpad=pump&period=1h&filters[]=not_honeypot&filters[]=pump
                     → {"data":{"pairs":[...]}}  (Dragon)
  - token trades:    GET /defi/quotation/v1/trades/{chain}/{address}?limit=100
                     → {"data":{"history":[...]}}  (Dragon)

Rate-limit notes: GMGN aggressively rate-limits datacenter IPs (Cloudflare
in front). These providers are registered as Tier-3 fallbacks and use a
browser-like User-Agent + referer header to maximize success.

Each provider returns ``dict | None`` per the DataBus provider contract:
return ``None`` to fall through to the next tier in the chain.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.gmgn")

GMGN_BASE = "https://gmgn.ai"

# Browser-like headers — GMGN's public API rejects plain curl/httpx UAs.
_BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://gmgn.ai/?chain=sol",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

_TIMEOUT = 12.0


def _normalize_chain(chain: str) -> str:
    """Map chain names to GMGN's short chain slugs (sol, eth, bsc, base...)."""
    aliases = {
        "solana": "sol",
        "sol": "sol",
        "ethereum": "eth",
        "eth": "eth",
        "mainnet": "eth",
        "bsc": "bsc",
        "bnb": "bsc",
        "binance": "bsc",
        "base": "base",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "avalanche": "avax",
        "avax": "avax",
        "fantom": "fantom",
        "ftm": "fantom",
    }
    return aliases.get(chain.lower(), chain.lower())


async def _gmgn_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Perform a GET against the GMGN public API with browser headers."""
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers=_BROWSER_HEADERS,
        ) as c:
            r = await c.get(f"{GMGN_BASE}{path}", params=params)
            if r.status_code == 200:
                data = r.json()
                # code == 0 indicates success on the quotation endpoints
                if isinstance(data, dict) and data.get("code") not in (None, 0, 200):
                    logger.debug("GMGN API error code=%s path=%s", data.get("code"), path)
                    return None
                return data
            logger.debug("GMGN HTTP %s path=%s", r.status_code, path)
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError) as e:
        logger.debug("GMGN request failed path=%s err=%s", path, e)
    return None


# ═══════════════════════════════════════════════════════════════════
# TOKEN INFO
# ═══════════════════════════════════════════════════════════════════


async def _gmgn_token_info(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN token overview — price, market cap, liquidity, holders, launchpad.

    Parameters (from DataBus fetch kwargs):
      mint/address: str — token address
      chain: str — chain name (default solana)
    """
    mint = kwargs.get("mint", "") or kwargs.get("address", "") or kwargs.get("token", "")
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    if not mint:
        return None
    data = await _gmgn_get(f"/defi/quotation/v1/tokens/{chain}/{mint}")
    if not data:
        return None
    token = (data.get("data") or {}).get("token") or {}
    if not token:
        return None
    return {
        "address": token.get("address", mint),
        "chain": chain,
        "symbol": token.get("symbol", ""),
        "name": token.get("name", ""),
        "decimals": token.get("decimals"),
        "price": token.get("price"),
        "market_cap": token.get("market_cap"),
        "fdv": token.get("fdv"),
        "liquidity": token.get("liquidity"),
        "holder_count": token.get("holder_count"),
        "top_10_holder_rate": token.get("top_10_holder_rate"),
        "creation_timestamp": token.get("creation_timestamp"),
        "open_timestamp": token.get("open_timestamp"),
        "launchpad": token.get("launchpad"),
        "launchpad_progress": token.get("launchpad_progress"),
        "creator_address": token.get("creator_address"),
        "creator_token_status": token.get("creator_token_status"),
        "is_honeypot": token.get("is_honeypot"),
        "renounced": token.get("renounced"),
        "burn_ratio": token.get("burn_ratio"),
        "buy_tax": token.get("buy_tax"),
        "sell_tax": token.get("sell_tax"),
        "swaps_24h": token.get("swaps_24h"),
        "volume_24h": token.get("volume_24h"),
        "price_change_1m": token.get("price_1m"),
        "price_change_5m": token.get("price_5m"),
        "price_change_1h": token.get("price_1h"),
        "price_change_6h": token.get("price_6h"),
        "price_change_24h": token.get("price_24h"),
        "pool_info": token.get("pool_info"),
        "source": "gmgn",
    }


# ═══════════════════════════════════════════════════════════════════
# WALLET ACTIVITY / TRANSACTION HISTORY
# ═══════════════════════════════════════════════════════════════════


async def _gmgn_wallet_tx_history(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN wallet smart-money stats — PnL, winrate, buys, balance.

    Note: this is the wallet activity endpoint (verified). GMGN does not
    expose a raw per-tx list on the public API; the ``walletNew`` endpoint
    returns aggregated activity + PnL which is what downstream scanners use
    to tag smart-money wallets.

    Parameters (from DataBus fetch kwargs):
      address/wallet: str — wallet address
      chain: str — chain name (default solana)
      period: str — 7d, 30d (default 7d)
    """
    address = kwargs.get("address", "") or kwargs.get("wallet", "")
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    period = kwargs.get("period", "7d")
    if not address:
        return None
    data = await _gmgn_get(
        f"/defi/quotation/v1/smartmoney/{chain}/walletNew/{address}",
        params={"period": period},
    )
    if not data:
        return None
    d = data.get("data") or {}
    if not d:
        return None
    return {
        "wallet": address,
        "chain": chain,
        "period": period,
        "total_profit_pnl": d.get("total_profit_pnl"),
        "realized_profit_7d": d.get("realized_profit_7d"),
        "realized_profit_30d": d.get("realized_profit_30d"),
        "winrate": d.get("winrate"),
        "sol_balance": d.get("sol_balance"),
        "buy_7d": d.get("buy_7d"),
        "buy_30d": d.get("buy_30d"),
        "avg_holding_peroid": d.get("avg_holding_peroid"),
        "tags": d.get("tags", []),
        "source": "gmgn",
    }


# ═══════════════════════════════════════════════════════════════════
# HOLDER DATA
# ═══════════════════════════════════════════════════════════════════


async def _gmgn_token_holders(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN top holders for a token (by amount percentage).

    Parameters (from DataBus fetch kwargs):
      mint/address: str — token address
      chain: str — chain name (default solana)
      limit: int — max holders (default 100)
    """
    mint = kwargs.get("mint", "") or kwargs.get("address", "") or kwargs.get("token", "")
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    limit = min(int(kwargs.get("limit", 100)), 200)
    if not mint:
        return None
    data = await _gmgn_get(
        f"/vas/api/v1/token_holders/{chain}/{mint}",
        params={"orderby": "amount_percentage", "direction": "desc", "limit": limit},
    )
    if not data:
        return None
    rows = (data.get("data") or {}).get("list") or []
    holders = []
    for h in rows:
        holders.append(
            {
                "address": h.get("address", ""),
                "amount_percentage": h.get("amount_percentage"),
                "cost_cur": h.get("cost_cur"),
                "realized_profit": h.get("realized_profit"),
                "unrealized_profit": h.get("unrealized_profit"),
                "profit_change": h.get("profit_change"),
                "buy_tx_count_cur": h.get("buy_tx_count_cur"),
                "sell_tx_count_cur": h.get("sell_tx_count_cur"),
                "is_insider": bool(h.get("insider", False)),
                "is_smart_money": bool(h.get("is_smart_dealer", h.get("smart_money", False))),
            }
        )
    return {
        "address": mint,
        "chain": chain,
        "total": len(holders),
        "holders": holders,
        "source": "gmgn",
    }


# ═══════════════════════════════════════════════════════════════════
# NEW LAUNCHES (pump.fun)
# ═══════════════════════════════════════════════════════════════════


async def _gmgn_new_pairs(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN new pump.fun token launches (new_creation rank).

    Parameters (from DataBus fetch kwargs):
      chain: str — chain name (default solana)
      limit: int — max tokens (default 50)
    """
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    limit = min(int(kwargs.get("limit", 50)), 200)
    data = await _gmgn_get(
        f"/defi/quotation/v1/rank/{chain}/pump",
        params={
            "limit": limit,
            "orderby": "created_timestamp",
            "direction": "desc",
            "new_creation": "true",
        },
    )
    if not data:
        return None
    rows = (data.get("data") or {}).get("rank") or []
    launches = []
    for t in rows:
        launches.append(
            {
                "address": t.get("address", ""),
                "chain": chain,
                "symbol": t.get("symbol", ""),
                "name": t.get("name", ""),
                "price": t.get("price"),
                "market_cap": t.get("market_cap"),
                "liquidity": t.get("liquidity"),
                "holder_count": t.get("holder_count"),
                "creation_timestamp": t.get("creation_timestamp"),
                "launchpad": t.get("launchpad", "pump"),
                "is_honeypot": t.get("is_honeypot"),
                "creator_address": t.get("creator_address"),
                "source": "gmgn",
            }
        )
    return {
        "data": {"launches": launches, "count": len(launches), "chain": chain},
        "source": "gmgn",
        "tier": "free_api",
    }


async def _gmgn_bonded_pairs(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN pump.fun pairs that have completed their bonding curve.

    Parameters (from DataBus fetch kwargs):
      chain: str — chain name (default solana)
      limit: int — max pairs (default 50)
    """
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    limit = min(int(kwargs.get("limit", 50)), 200)
    data = await _gmgn_get(
        f"/defi/quotation/v1/pairs/{chain}/new_pairs/1h",
        params={
            "limit": limit,
            "orderby": "open_timestamp",
            "direction": "desc",
            "launchpad": "pump",
            "period": "1h",
            "filters[]": ["not_honeypot", "pump"],
        },
    )
    if not data:
        return None
    rows = (data.get("data") or {}).get("pairs") or []
    launches = []
    for p in rows:
        launches.append(
            {
                "address": p.get("base_address", ""),
                "pair_address": p.get("address", ""),
                "chain": chain,
                "symbol": p.get("symbol", ""),
                "name": p.get("name", ""),
                "price": p.get("price"),
                "market_cap": p.get("market_cap"),
                "liquidity": p.get("liquidity"),
                "open_timestamp": p.get("open_timestamp"),
                "creation_timestamp": p.get("creation_timestamp"),
                "launchpad": p.get("launchpad", "pump"),
                "source": "gmgn",
            }
        )
    return {
        "data": {"launches": launches, "count": len(launches), "chain": chain},
        "source": "gmgn",
        "tier": "free_api",
    }


# ═══════════════════════════════════════════════════════════════════
# TOKEN TRADES
# ═══════════════════════════════════════════════════════════════════


async def _gmgn_token_trades(**kwargs: Any) -> dict[str, Any] | None:
    """GMGN recent trades for a token (history list).

    Parameters (from DataBus fetch kwargs):
      mint/address: str — token address
      chain: str — chain name (default solana)
      limit: int — max trades (default 100)
    """
    mint = kwargs.get("mint", "") or kwargs.get("address", "") or kwargs.get("token", "")
    chain = _normalize_chain(kwargs.get("chain", "solana"))
    limit = min(int(kwargs.get("limit", 100)), 500)
    if not mint:
        return None
    data = await _gmgn_get(
        f"/defi/quotation/v1/trades/{chain}/{mint}",
        params={"limit": limit},
    )
    if not data:
        return None
    rows = (data.get("data") or {}).get("history") or []
    trades = []
    for t in rows:
        trades.append(
            {
                "tx_hash": t.get("tx_hash", ""),
                "timestamp": t.get("timestamp"),
                "event": t.get("event"),
                "maker": t.get("maker", ""),
                "price": t.get("price"),
                "amount": t.get("amount"),
                "total": t.get("total"),
                "profit": t.get("profit"),
                "is_buy": str(t.get("event", "")).lower() in ("buy", "b"),
                "source": "gmgn",
            }
        )
    return {
        "data": {"trades": trades, "count": len(trades), "address": mint, "chain": chain},
        "source": "gmgn",
        "tier": "free_api",
    }
