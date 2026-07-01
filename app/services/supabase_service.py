#!/usr/bin/env python3
"""
RMI Supabase Service — Unified backend layer for all Supabase tables.
Replaces scattered REST calls with a clean, typed service interface.
"""

import os
from datetime import UTC, datetime

import httpx
from dotenv import load_dotenv

# Ensure .env is loaded before reading env vars — override stale Docker env
load_dotenv("/app/.env", override=True)


def _get_url() -> str:
    return os.environ.get("SUPABASE_URL", "")


def _get_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""


def _get_headers() -> dict:
    key = _get_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _url(table: str) -> str:
    return f"{_get_url()}/rest/v1/{table}"


async def _get(
    table: str,
    params: dict | None = None,
    select: str = "*",
    order: str | None = None,
    limit: int | None = None,
) -> list:
    """Generic GET query."""
    url = _url(table)
    query_parts = [f"select={select}"]
    if params:
        for k, v in params.items():
            query_parts.append(f"{k}=eq.{v}")
    if order:
        query_parts.append(f"order={order}")
    if limit:
        query_parts.append(f"limit={limit}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        if r.status_code == 200:
            return r.json()
        return []


async def _get_in(table: str, field: str, values: list, select: str = "*") -> list:
    """GET with IN filter."""
    vals = ",".join(str(v) for v in values)
    url = f"{_url(table)}?select={select}&{field}=in.({vals})"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def _post(table: str, data: dict) -> dict:
    """Insert a row."""
    url = f"{_url(table)}"
    headers = dict(_get_headers())
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        if r.status_code in (200, 201):
            return r.json()[0] if r.json() else {}
        return None


async def _patch(table: str, match: dict, data: dict) -> bool:
    """Update row(s)."""
    url = _url(table)
    query_parts = [f"{k}=eq.{v}" for k, v in match.items()]
    url += "?" + "&".join(query_parts)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.patch(url, json=data, headers=_get_headers())
        return r.status_code in (200, 204)


async def _delete(table: str, match: dict) -> bool:
    """Delete row(s)."""
    url = _url(table)
    query_parts = [f"{k}=eq.{v}" for k, v in match.items()]
    url += "?" + "&".join(query_parts)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(url, headers=_get_headers())
        return r.status_code in (200, 204)


async def _rpc(fn: str, params: dict | None = None) -> dict:
    """Call a stored procedure."""
    url = f"{_get_url()}/rest/v1/rpc/{fn}"
    headers = dict(_get_headers())
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=params or {}, headers=headers)
        return r.json() if r.status_code == 200 else None


# ═════════════════════════════════════════════════════════
# ALERTS
# ═════════════════════════════════════════════════════════


async def get_alerts(
    severity: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    unread_only: bool = False,
) -> list:
    params = {}
    if severity:
        params["severity"] = severity
    if user_id:
        params["user_id"] = user_id
    order = "created_at.desc"

    url = _url("alerts")
    query_parts = ["select=*", f"order={order}", f"limit={limit}"]
    for k, v in params.items():
        query_parts.append(f"{k}=eq.{v}")
    if unread_only:
        query_parts.append("is_read=eq.false")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def create_alert(
    title: str,
    description: str,
    severity: str = "medium",
    alert_type: str = "security",
    chain: str | None = None,
    token: str | None = None,
    wallet: str | None = None,
    amount_usd: float | None = None,
    tx_hash: str | None = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    data = {
        "title": title,
        "description": description,
        "severity": severity,
        "type": alert_type,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if chain:
        data["chain"] = chain
    if token:
        data["token"] = token
    if wallet:
        data["wallet"] = wallet
    if amount_usd:
        data["amount_usd"] = amount_usd
    if tx_hash:
        data["tx_hash"] = tx_hash
    if user_id:
        data["user_id"] = user_id
    if metadata:
        data["metadata"] = metadata

    return await _post("alerts", data)


async def acknowledge_alert(alert_id: str) -> bool:
    return await _patch("alerts", {"id": alert_id}, {"acknowledged": True, "is_read": True})


# ═════════════════════════════════════════════════════════
# WALLETS
# ═════════════════════════════════════════════════════════


async def get_wallets(
    chain: str | None = None,
    risk_min: int | None = None,
    risk_max: int | None = None,
    limit: int = 50,
) -> list:
    url = _url("wallets")
    query_parts = ["select=*", f"limit={limit}"]
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    if risk_min is not None:
        query_parts.append(f"risk_score=gte.{risk_min}")
    if risk_max is not None:
        query_parts.append(f"risk_score=lte.{risk_max}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_wallet(address: str, chain: str = "ethereum") -> dict:
    url = f"{_url('wallets')}?select=*&address=eq.{address}&chain=eq.{chain}&limit=1"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        data = r.json() if r.status_code == 200 else []
        return data[0] if data else None


async def upsert_wallet(address: str, chain: str, **fields) -> dict:
    data = {"address": address, "chain": chain, **fields}
    url = f"{_url('wallets')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


# ═════════════════════════════════════════════════════════
# WALLET INTEL (analysis results)
# ═════════════════════════════════════════════════════════


async def get_wallet_intel(address: str | None = None, chain: str | None = None, limit: int = 100) -> list:
    url = _url("wallet_intel")
    query_parts = ["select=*", f"limit={limit}"]
    if address:
        query_parts.append(f"address=eq.{address}")
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


async def get_wallet_connections(address: str) -> list:
    """Find all wallets connected to this address."""
    url = f"{_url('wallet_intel')}?select=*&related_addresses=cs.{{'{address}'}}&limit=50"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


# ═════════════════════════════════════════════════════════
# SYNDICATE WALLETS
# ═════════════════════════════════════════════════════════


async def get_syndicate_wallets(status: str | None = None, chain: str | None = None, limit: int = 200) -> list:
    url = _url("syndicate_wallets")
    query_parts = ["select=*", f"limit={limit}"]
    if status:
        query_parts.append(f"status=eq.{status}")
    if chain:
        query_parts.append(f"chain=eq.{chain}")
    url += "?" + "&".join(query_parts)

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url, headers=_get_headers())
        return r.json() if r.status_code == 200 else []


# ═════════════════════════════════════════════════════════
# WATCHLIST
# ═════════════════════════════════════════════════════════


async def get_watchlist(user_id: str) -> list:
    return await _get("watchlist", {"user_id": user_id}, order="created_at.desc")


async def add_to_watchlist(
    user_id: str,
    address: str,
    chain: str = "ethereum",
    tags: list | None = None,
    item_type: str = "wallet",
) -> dict:
    """Add an address to a user's watchlist. item_type: 'token' or 'wallet'."""
    return await _post(
        "watchlist",
        {
            "user_id": user_id,
            "address": address,
            "type": item_type,
            "chain": chain,
            "tags": tags or [],
        },
    )


async def remove_from_watchlist(user_id: str, address: str, chain: str = "ethereum") -> bool:
    return await _delete(
        "watchlist",
        {
            "user_id": user_id,
            "address": address,
            "chain": chain,
        },
    )


# ═════════════════════════════════════════════════════════
# TOKENS
# ═════════════════════════════════════════════════════════


async def get_tokens(chain: str | None = None, limit: int = 50, order_by: str = "volume_24h.desc") -> list:
    params = {}
    if chain:
        params["chain"] = chain
    return await _get("tokens", params, order=order_by, limit=limit)


async def get_token(address: str, chain: str = "ethereum") -> dict:
    data = await _get("tokens", {"address": address, "chain": chain}, limit=1)
    return data[0] if data else None


async def upsert_token(address: str, chain: str, **fields) -> dict:
    data = {
        "address": address,
        "chain": chain,
        "last_fetched": datetime.now(UTC).isoformat(),
        **fields,
    }
    url = f"{_url('tokens')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


# ═════════════════════════════════════════════════════════
# BULLETIN BOARD
# ═════════════════════════════════════════════════════════


async def get_bulletin_posts(category: str | None = None, sort: str = "created_at.desc", limit: int = 20) -> list:
    params = {}
    if category and category != "all":
        params["category"] = category
    return await _get("bulletin_posts", params, order=sort, limit=limit)


async def create_bulletin_post(
    title: str,
    content: str,
    author_id: str,
    category: str,
    chain: str | None = None,
    tags: list | None = None,
    risk_score: int | None = None,
    source: str | None = None,
) -> dict:
    data = {
        "title": title,
        "content": content,
        "author_id": author_id,
        "category": category,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if chain:
        data["chain"] = chain
    if tags:
        data["tags"] = tags
    if risk_score:
        data["risk_score"] = risk_score
    if source:
        data["source"] = source

    return await _post("bulletin_posts", data)


async def vote_post(post_id: int, vote_type: str) -> bool:
    """vote_type: 'up' or 'down'"""
    field = "upvotes" if vote_type == "up" else "downvotes"
    # Get current value
    posts = await _get("bulletin_posts", {"id": post_id}, select=f"id,{field}", limit=1)
    if not posts:
        return False
    current = posts[0].get(field, 0) or 0
    return await _patch("bulletin_posts", {"id": post_id}, {field: current + 1})


# ═════════════════════════════════════════════════════════
# BADGES
# ═════════════════════════════════════════════════════════


async def get_badges(rarity: str | None = None) -> list:
    params = {}
    if rarity:
        params["rarity"] = rarity
    return await _get("badges", params, order="rarity.asc")


async def get_user_badges(user_id: str) -> list:
    """Get user's earned badges with full badge details."""
    user_badges = await _get("user_badges", {"user_id": user_id})
    if not user_badges:
        return []

    badge_ids = [ub["badge_id"] for ub in user_badges]
    badges = await _get_in("badges", "id", badge_ids)
    badge_map = {b["id"]: b for b in badges}

    result = []
    for ub in user_badges:
        badge = badge_map.get(ub["badge_id"])
        if badge:
            result.append({**ub, "badge_detail": badge})
    return result


async def award_badge(user_id: str, badge_id: str, earned_via: str = "auto") -> dict:
    return await _post(
        "user_badges",
        {
            "user_id": user_id,
            "badge_id": badge_id,
            "earned_at": datetime.now(UTC).isoformat(),
            "earned_via": earned_via,
        },
    )


# ═════════════════════════════════════════════════════════
# PROFILES
# ═════════════════════════════════════════════════════════


async def get_profile(user_id: str) -> dict:
    data = await _get("profiles", {"id": user_id}, limit=1)
    return data[0] if data else None


async def update_profile(user_id: str, **fields) -> bool:
    return await _patch("profiles", {"id": user_id}, fields)


# ═════════════════════════════════════════════════════════
# CONTRACT ANALYSES
# ═════════════════════════════════════════════════════════


async def get_contract_analysis(address: str, chain: str = "ethereum") -> dict:
    data = await _get("contract_analyses", {"address": address, "chain": chain}, limit=1)
    return data[0] if data else None


async def upsert_contract_analysis(address: str, chain: str, **fields) -> dict:
    data = {
        "address": address,
        "chain": chain,
        "analyzed_at": datetime.now(UTC).isoformat(),
        **fields,
    }
    url = f"{_url('contract_analyses')}"
    headers = dict(_get_headers())
    headers["Prefer"] = "resolution=merge-duplicates"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data, headers=headers)
        return r.json()[0] if r.status_code in (200, 201) and r.json() else None


# ═════════════════════════════════════════════════════════
# ENTITY CLUSTERS
# ═════════════════════════════════════════════════════════


async def get_entity_clusters(limit: int = 50) -> list:
    return await _get("entity_clusters", order="created_at.desc", limit=limit)


async def get_cluster_wallets(cluster_id: str) -> list:
    clusters = await _get("entity_clusters", {"cluster_id": cluster_id}, limit=1)
    if not clusters:
        return []
    addresses = clusters[0].get("addresses", [])
    return addresses


# ═════════════════════════════════════════════════════════
# TRANSACTIONS
# ═════════════════════════════════════════════════════════


async def get_transactions(wallet_address: str, chain: str | None = None, limit: int = 50) -> list:
    params = {"wallet_address": wallet_address}
    if chain:
        params["chain"] = chain
    return await _get("wallet_transactions", params, order="timestamp.desc", limit=limit)


# ═════════════════════════════════════════════════════════
# MARKET DATA
# ═════════════════════════════════════════════════════════


async def get_market_data(data_type: str | None = None, limit: int = 10) -> list:
    params = {}
    if data_type:
        params["data_type"] = data_type
    return await _get("market_data", params, order="fetched_at.desc", limit=limit)


async def store_market_data(source: str, data_type: str, data: dict) -> dict:
    return await _post(
        "market_data",
        {
            "source": source,
            "data_type": data_type,
            "data": data,
            "fetched_at": datetime.now(UTC).isoformat(),
        },
    )


# ═════════════════════════════════════════════════════════
# SUBSCRIPTIONS
# ═════════════════════════════════════════════════════════


async def get_subscription(user_id: str) -> dict:
    data = await _get("subscriptions", {"user_id": user_id}, limit=1)
    return data[0] if data else None


# ═════════════════════════════════════════════════════════
# ANALYTICS
# ═════════════════════════════════════════════════════════


async def track_event(
    event_type: str,
    user_id: str | None = None,
    event_data: dict | None = None,
    page: str | None = None,
    session_id: str | None = None,
) -> dict:
    return await _post(
        "analytics_events",
        {
            "event_type": event_type,
            "user_id": user_id,
            "event_data": event_data or {},
            "page": page,
            "session_id": session_id,
        },
    )


# ═════════════════════════════════════════════════════════
# HEALTH CHECK
# ═════════════════════════════════════════════════════════


async def check_health() -> dict:
    """Verify Supabase connectivity."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{_get_url()}/rest/v1/profiles?limit=0", headers=_get_headers())
            return {
                "status": "healthy" if r.status_code == 200 else "degraded",
                "tables": 18,  # total tables we expect
                "timestamp": datetime.now(UTC).isoformat(),
            }
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


# ═════════════════════════════════════════════════════════
# X402 PAYMENTS (Supabase persistence layer)
# ═════════════════════════════════════════════════════════


async def record_x402_payment_supabase(
    tool: str,
    amount_atoms: str,
    chain: str,
    payer: str = "unknown",
    tx_hash: str = "",
    status: str = "fulfilled",
) -> dict:
    """Insert a payment record into x402_payments table."""
    return await _post(
        "x402_payments",
        {
            "tool": tool,
            "amount_atoms": str(amount_atoms),
            "chain": chain,
            "payer": payer,
            "tx_hash": tx_hash,
            "status": status,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )


async def get_x402_payments(
    tool: str | None = None,
    chain: str | None = None,
    payer: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list:
    """Query x402_payments with optional filters."""
    params = {}
    if tool:
        params["tool"] = tool
    if chain:
        params["chain"] = chain
    if payer:
        params["payer"] = payer
    if status:
        params["status"] = status
    return await _get("x402_payments", params, order="created_at.desc", limit=limit)


async def get_x402_payment_by_tx(tx_hash: str) -> dict:
    """Get a specific payment by tx_hash."""
    data = await _get("x402_payments", {"tx_hash": tx_hash}, limit=1)
    return data[0] if data else None


async def get_x402_earnings_summary() -> dict:
    """Get earnings summary from Supabase.

    Aggregates total by chain, tool, and day using Supabase data.
    """
    try:
        rows = await _get("x402_payments", select="tool,amount_atoms,chain,created_at,status", limit=5000)
        total_atoms = 0
        by_chain = {}
        by_tool = {}
        by_day = {}
        for row in rows:
            if row.get("status") != "fulfilled":
                continue
            try:
                amt = int(row.get("amount_atoms", "0") or "0")
            except (ValueError, TypeError):
                amt = 0
            total_atoms += amt
            chain = row.get("chain", "unknown")
            by_chain[chain] = by_chain.get(chain, 0) + amt
            tool = row.get("tool", "unknown")
            by_tool[tool] = by_tool.get(tool, 0) + amt
            # Day extraction
            created = row.get("created_at", "")
            day = created[:10] if created else "unknown"
            by_day[day] = by_day.get(day, 0) + amt

        return {
            "total_usdc": round(total_atoms / 1e6, 6),
            "by_chain_usdc": {k: round(v / 1e6, 6) for k, v in by_chain.items()},
            "by_tool_usdc": {k: round(v / 1e6, 6) for k, v in by_tool.items()},
            "by_day_usdc": {k: round(v / 1e6, 6) for k, v in sorted(by_day.items())[-30:]},
            "record_count": len(rows),
        }
    except Exception:
        return {
            "total_usdc": 0,
            "by_chain_usdc": {},
            "by_tool_usdc": {},
            "by_day_usdc": {},
            "record_count": 0,
        }


async def ensure_x402_payments_table():
    """Create x402_payments table in Supabase if it doesn't exist."""
    sql = """
    DO $$ BEGIN
        CREATE TABLE IF NOT EXISTS x402_payments (
            id BIGSERIAL PRIMARY KEY,
            tool TEXT NOT NULL DEFAULT 'unknown',
            amount_atoms TEXT NOT NULL DEFAULT '0',
            chain TEXT NOT NULL DEFAULT 'unknown',
            payer TEXT NOT NULL DEFAULT 'unknown',
            tx_hash TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status TEXT NOT NULL DEFAULT 'fulfilled'
        );
    END $$;
    CREATE INDEX IF NOT EXISTS idx_x402_payments_tool ON x402_payments(tool);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_chain ON x402_payments(chain);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_tx_hash ON x402_payments(tx_hash);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_created_at ON x402_payments(created_at);
    CREATE INDEX IF NOT EXISTS idx_x402_payments_payer ON x402_payments(payer);
    -- RLS: service_role only -- payment data is backend-only
    ALTER TABLE x402_payments ENABLE ROW LEVEL SECURITY;
    DO $$ BEGIN
        DROP POLICY IF EXISTS "Service role full access" ON x402_payments;
    EXCEPTION WHEN OTHERS THEN NULL; END $$;
    CREATE POLICY "Service role full access" ON x402_payments FOR ALL TO service_role USING (true) WITH CHECK (true);
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{_get_url()}/rest/v1/rpc/exec_sql",
                json={"sql": sql},
                headers=_get_headers(),
            )
            return r.status_code in (200, 204)
    except Exception:
        return False
