"""RMI Supabase Service — facade over the split Supabase persistence layer.

The service was split into single-purpose modules (transport, alerts, wallets,
content, records, analytics, health, x402). This module preserves the original
public import surface, so `from app.services.supabase_service import <name>`
keeps working unchanged.

SERVICE KEY: uses SUPABASE_SERVICE_KEY (service_role) intentionally — the
backend needs full CRUD access that bypasses RLS (wallet_intel, x402_payments,
entity_clusters are backend-only; upsert/delete/bulk inserts need service_role).
"""

from __future__ import annotations

from app.services.supabase_alerts import acknowledge_alert, create_alert, get_alerts
from app.services.supabase_analytics import get_subscription, track_event
from app.services.supabase_content import (
    award_badge,
    create_bulletin_post,
    get_badges,
    get_bulletin_posts,
    get_token,
    get_tokens,
    get_user_badges,
    upsert_token,
    vote_post,
)
from app.services.supabase_health import _supabase_cb, check_health
from app.services.supabase_records import (
    get_cluster_wallets,
    get_contract_analysis,
    get_entity_clusters,
    get_market_data,
    get_profile,
    get_transactions,
    store_market_data,
    update_profile,
    upsert_contract_analysis,
)
from app.services.supabase_wallets import (
    add_to_watchlist,
    get_syndicate_wallets,
    get_wallet,
    get_wallet_connections,
    get_wallet_intel,
    get_wallets,
    get_watchlist,
    remove_from_watchlist,
    upsert_wallet,
)
from app.services.supabase_x402 import (
    ensure_x402_payments_table,
    get_x402_earnings_summary,
    get_x402_payment_by_tx,
    get_x402_payments,
    record_x402_payment_supabase,
)

__all__ = [
    "_supabase_cb",
    "acknowledge_alert",
    "add_to_watchlist",
    "award_badge",
    "check_health",
    "create_alert",
    "create_bulletin_post",
    "ensure_x402_payments_table",
    "get_alerts",
    "get_badges",
    "get_bulletin_posts",
    "get_cluster_wallets",
    "get_contract_analysis",
    "get_entity_clusters",
    "get_market_data",
    "get_profile",
    "get_subscription",
    "get_syndicate_wallets",
    "get_token",
    "get_tokens",
    "get_transactions",
    "get_user_badges",
    "get_wallet",
    "get_wallet_connections",
    "get_wallet_intel",
    "get_wallets",
    "get_watchlist",
    "get_x402_earnings_summary",
    "get_x402_payment_by_tx",
    "get_x402_payments",
    "record_x402_payment_supabase",
    "remove_from_watchlist",
    "store_market_data",
    "track_event",
    "update_profile",
    "upsert_contract_analysis",
    "upsert_token",
    "upsert_wallet",
    "vote_post",
]
