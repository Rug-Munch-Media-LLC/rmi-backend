"""
Real-Time WebSocket Alerts System
==================================

Provides real-time notification via WebSocket for:
- Transaction monitoring
- Security alerts
- Entity changes
- New wallet discoveries
"""

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.logging import get_logger

logger = get_logger(__name__)


# ─── REAL-TIME ALERT TYPES ──────────────────────────────────────────────


class AlertType:
    """Supported alert types."""

    NEW_TRANSACTION = "new_transaction"
    SECURITY_ALERT = "security_alert"
    ENTITY_CHANGE = "entity_change"
    NEW_WALLET = "new_wallet"
    CROSS_CHAIN = "cross_chain_movement"
    RATE_LIMIT = "rate_limit_exceeded"
    SYSTEM_STATUS = "system_status"


class AlertSeverity:
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class RealTimeAlert:
    """Represents a real-time alert."""

    def __init__(
        self,
        alert_type: str,
        message: str,
        severity: str,
        data: dict[str, Any] | None = None,
        chain: str = "ethereum",
        wallet_address: str = "",
        related_entities: list[str] | None = None,
    ):
        self.alert_type = alert_type
        self.message = message
        self.severity = severity
        self.data = data or {}
        self.chain = chain
        self.wallet_address = wallet_address
        self.related_entities = related_entities or []
        self.timestamp = datetime.now(UTC).isoformat()
        self.id = f"{alert_type}_{datetime.now(UTC).timestamp()}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "type": self.alert_type,
            "message": self.message,
            "severity": self.severity,
            "chain": self.chain,
            "wallet_address": self.wallet_address,
            "related_entities": self.related_entities,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


# ─── ALERT BROADCASTER ────────────────────────────────────────────────


class AlertBroadcaster:
    """Broadcasts alerts to connected WebSocket clients."""

    def __init__(self):
        self.clients: dict[str, WebSocket] = {}
        self.subscriptions: dict[str, set[str]] = defaultdict(set)  # wallet -> client_id
        self.alert_queue: list[RealTimeAlert] = []
        self.max_queue_size = 1000

    async def connect(self, websocket: WebSocket, client_id: str | None = None):
        """Accept a WebSocket connection."""
        await websocket.accept()
        if not client_id:
            client_id = f"client_{id(websocket)}"
        self.clients[client_id] = websocket
        logger.info(f"WebSocket client connected: {client_id}")
        return client_id

    async def disconnect(self, client_id: str):
        """Remove a client."""
        if client_id in self.clients:
            del self.clients[client_id]
            # Remove subscriptions
            for wallet in self.subscriptions:
                self.subscriptions[wallet].discard(client_id)
            logger.info(f"WebSocket client disconnected: {client_id}")

    def subscribe(self, client_id: str, wallet_address: str):
        """Subscribe a client to wallet alerts."""
        self.subscriptions[wallet_address].add(client_id)

    def unsubscribe(self, client_id: str, wallet_address: str):
        """Unsubscribe a client from wallet alerts."""
        self.subscriptions[wallet_address].discard(client_id)

    async def broadcast(self, alert: RealTimeAlert):
        """Broadcast an alert to relevant clients."""
        self.alert_queue.append(alert)

        # Keep queue size manageable
        if len(self.alert_queue) > self.max_queue_size:
            self.alert_queue = self.alert_queue[-self.max_queue_size :]

        # Find interested clients
        interested_clients = set()
        interested_clients.update(self.clients.keys())  # Broadcast to all

        # Also add clients subscribed to related wallets
        if alert.wallet_address:
            interested_clients.update(self.subscriptions.get(alert.wallet_address, set()))

        if alert.related_entities:
            for entity in alert.related_entities:
                interested_clients.update(self.subscriptions.get(entity, set()))

        # Send to interested clients
        for client_id in list(interested_clients):
            if client_id in self.clients:
                try:
                    await self.clients[client_id].send_text(alert.to_json())
                except Exception as e:
                    logger.error(f"Failed to send alert to {client_id}: {e}")
                    await self.disconnect(client_id)

    async def broadcast_system_status(self, status: str, details: dict[str, Any] | None = None):
        """Broadcast system status update."""
        alert = RealTimeAlert(
            alert_type=AlertType.SYSTEM_STATUS,
            message=f"System {status}",
            severity=AlertSeverity.INFO,
            data=details or {},
        )
        await self.broadcast(alert)

    async def broadcast_security_alert(
        self, wallet: str, severity: str, message: str, chain: str = "ethereum"
    ):
        """Broadcast a security alert."""
        alert = RealTimeAlert(
            alert_type=AlertType.SECURITY_ALERT,
            message=message,
            severity=severity,
            chain=chain,
            wallet_address=wallet,
            data={"severity": severity, "category": "security"},
        )
        await self.broadcast(alert)

    async def broadcast_transaction(self, wallet: str, tx_hash: str, chain: str = "ethereum"):
        """Broadcast a new transaction."""
        alert = RealTimeAlert(
            alert_type=AlertType.NEW_TRANSACTION,
            message=f"New transaction for {wallet[:20]}... on {chain}",
            severity=AlertSeverity.INFO,
            chain=chain,
            wallet_address=wallet,
            data={"tx_hash": tx_hash, "category": "transaction"},
        )
        await self.broadcast(alert)

    async def broadcast_cross_chain(self, from_chain: str, to_chain: str, wallet: str):
        """Broadcast cross-chain movement."""
        alert = RealTimeAlert(
            alert_type=AlertType.CROSS_CHAIN,
            message=f"Cross-chain movement: {from_chain} -> {to_chain}",
            severity=AlertSeverity.WARNING,
            chain=to_chain,
            wallet_address=wallet,
            data={"from_chain": from_chain, "to_chain": to_chain, "category": "cross_chain"},
        )
        await self.broadcast(alert)

    def get_alerts_by_wallet(self, wallet: str) -> list[dict[str, Any]]:
        """Get recent alerts for a specific wallet."""
        return [alert.to_dict() for alert in self.alert_queue if alert.wallet_address == wallet]

    def get_alerts_by_type(self, alert_type: str) -> list[dict[str, Any]]:
        """Get alerts of a specific type."""
        return [alert.to_dict() for alert in self.alert_queue if alert.alert_type == alert_type]


# ─── WEBSOCKET HANDLER ────────────────────────────────────────────────


class WebSocketManager:
    """Manages WebSocket connections and events."""

    def __init__(self):
        self.broadcaster = AlertBroadcaster()
        self.active_connections: dict[str, WebSocket] = {}
        self.connection_lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str | None = None):
        """Connect a new WebSocket client."""
        async with self.connection_lock:
            client_id = await self.broadcaster.connect(websocket, client_id)
            self.active_connections[client_id] = websocket
            await websocket.send_json({"type": "connected", "client_id": client_id})

    async def disconnect(self, client_id: str):
        """Disconnect a WebSocket client."""
        async with self.connection_lock:
            await self.broadcaster.disconnect(client_id)
            if client_id in self.active_connections:
                del self.active_connections[client_id]

    async def receive_message(self, client_id: str, message: str):
        """Handle incoming message from client."""
        try:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "subscribe":
                wallet = data.get("wallet")
                if wallet:
                    self.broadcaster.subscribe(client_id, wallet)
                    await self.active_connections[client_id].send_json(
                        {"type": "subscribed", "wallet": wallet}
                    )

            elif msg_type == "unsubscribe":
                wallet = data.get("wallet")
                if wallet:
                    self.broadcaster.unsubscribe(client_id, wallet)

            elif msg_type == "ping":
                await self.active_connections[client_id].send_json(
                    {"type": "pong", "timestamp": datetime.now(UTC).isoformat()}
                )

            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from {client_id}")

    async def broadcast(self, alert: RealTimeAlert):
        """Broadcast an alert to all connected clients."""
        await self.broadcaster.broadcast(alert)

    async def broadcast_security_alert(
        self, wallet: str, severity: str, message: str, chain: str = "ethereum"
    ):
        """Broadcast a security alert."""
        await self.broadcaster.broadcast_security_alert(wallet, severity, message, chain)

    async def broadcast_transaction(self, wallet: str, tx_hash: str, chain: str = "ethereum"):
        """Broadcast a new transaction."""
        await self.broadcaster.broadcast_transaction(wallet, tx_hash, chain)

    async def broadcast_cross_chain(self, from_chain: str, to_chain: str, wallet: str):
        """Broadcast cross-chain movement."""
        await self.broadcaster.broadcast_cross_chain(from_chain, to_chain, wallet)


# ─── FastAPI Integration ──────────────────────────────────────────────

# Global instance
ws_manager = WebSocketManager()


async def start_websocket_handler():
    """Start the WebSocket manager."""
    logger.info("WebSocket manager started")
    return ws_manager


def get_websocket_manager() -> WebSocketManager:
    """Get the global WebSocket manager instance."""
    return ws_manager


async def websocket_alert_endpoint(websocket: WebSocket, client_id: str = "default"):
    """WebSocket endpoint for real-time alerts."""
    await ws_manager.connect(websocket, client_id)

    try:
        while True:
            data = await websocket.receive_text()
            await ws_manager.receive_message(client_id, data)
    except WebSocketDisconnect:
        await ws_manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await ws_manager.disconnect(client_id)


# ─── TEST HELPER ──────────────────────────────────────────────────────


async def test_websocket_alerts():
    """Test the WebSocket alert system."""
    import asyncio

    async def mock_client():
        """Simulate a WebSocket client."""
        import websockets

        uri = "ws://localhost:8000/ws/alerts"

        async with websockets.connect(uri) as ws:
            # Subscribe to a wallet
            await ws.send(json.dumps({"type": "subscribe", "wallet": "0x1234..."}))
            response = await ws.recv()
            print(f"Subscribed: {response}")

            # Wait for alerts
            for _ in range(3):
                try:
                    alert = await asyncio.wait_for(ws.recv(), timeout=5)
                    print(f"Alert received: {alert}")
                except TimeoutError:
                    print("No alert received")

    # Run test
    await mock_client()


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_websocket_alerts())
