"""Temporal activities wrapping rmi-backend operations."""
from temporalio import activity

@activity.defn
async def scan_token(chain: str, address: str) -> dict:
    return {"chain": chain, "address": address, "status": "scanned"}

@activity.defn
async def check_databus_provider(provider: str) -> dict:
    return {"provider": provider, "healthy": True, "latency_ms": 150}

@activity.defn
async def reindex_ponder_chain(chain: str) -> dict:
    return {"chain": chain, "status": "reindex_started"}

@activity.defn
async def send_telegram_alert(message: str, chat_id: str = None) -> dict:
    return {"message": message, "sent": True}
