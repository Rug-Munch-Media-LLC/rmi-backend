"""Service layer — orchestration, external integrations, chat, and intelligence pipelines."""

from __future__ import annotations

# ChatService is not exported from app.services.chat (only BridgeManager, MatrixClient, SignalProtocol, etc.)
# from .chat import ChatService
# SupabaseService is not a class — supabase_service.py exports standalone functions (get_alerts, get_wallets, etc.)
# from .supabase_service import SupabaseService

__all__: list[str] = []
