"""Bridge Manager — Cross-protocol bridge orchestration.

All bridges route through Matrix (hub-and-spoke architecture):
- Each protocol has a bridge adapter
- Messages flow: Source Protocol → Bridge Adapter → Matrix Room → Bridge Adapter → Target Protocol
- Encryption is maintained end-to-end via the Signal Protocol layer
- Bridge health is monitored continuously

Supported bridges (all bidirectional):
  Matrix ↔ Nostr (NIP-17)
  Matrix ↔ Farcaster
  Matrix ↔ Bluesky (AT Protocol)
  Matrix ↔ Mastodon/ActivityPub
  Matrix ↔ Lens Protocol
  Matrix ↔ XMTP
  Matrix ↔ Telegram
  Matrix ↔ Discord
  Matrix ↔ IRC
  Matrix ↔ XMPP
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.logging import get_logger
from app.services.chat.models import (
    BridgeConfig,
    BridgeStatus,
    ChatMessage,
    MessageStatus,
    Protocol,
)

logger = get_logger(__name__)


class BridgeAdapter:
    """Base class for protocol bridge adapters.

    Each protocol implements its own adapter with the same interface.
    """

    def __init__(self, protocol: Protocol) -> None:
        self.protocol = protocol
        self.config = BridgeConfig(protocol=protocol)

    async def connect(self, config: dict[str, Any]) -> bool:
        """Connect to the remote protocol."""
        raise NotImplementedError

    async def disconnect(self) -> bool:
        """Disconnect from the remote protocol."""
        raise NotImplementedError

    async def send_message(self, message: ChatMessage) -> bool:
        """Send a message to the remote protocol."""
        raise NotImplementedError

    async def receive_messages(self) -> list[ChatMessage]:
        """Receive messages from the remote protocol."""
        raise NotImplementedError

    async def health_check(self) -> BridgeStatus:
        """Check if the bridge is healthy."""
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return self.config.status == BridgeStatus.ONLINE


class BridgeManager:
    """Manages all protocol bridges from Matrix.

    Hub-and-spoke architecture:
      Matrix (canonical) ←→ Bridge Adapter ←→ Remote Protocol

    The bridge manager:
    - Creates/maintains Matrix rooms bridged to remote protocols
    - Routes messages bidirectionally
    - Monitors bridge health
    - Handles reconnection
    """

    def __init__(self) -> None:
        self._bridges: dict[Protocol, BridgeConfig] = {}
        self._message_queue: list[ChatMessage] = []
        self._health_cache: dict[Protocol, tuple[BridgeStatus, float]] = {}

        logger.info("bridge_manager_initialized")

    # ── Bridge Lifecycle ──────────────────────────────────────────────

    async def enable_bridge(
        self,
        protocol: Protocol,
        matrix_room_id: str,
        remote_room_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> BridgeConfig:
        """Enable and connect a bridge for a protocol to a Matrix room."""
        bridge_config = BridgeConfig(
            protocol=protocol,
            enabled=True,
            status=BridgeStatus.CONNECTING,
            matrix_room_id=matrix_room_id,
            remote_room_id=remote_room_id,
            config=config or {},
        )

        # Simulate bridge connection
        # In production, this would connect to the actual bridge
        success = await self._connect_adapter(protocol, config or {})

        if success:
            bridge_config.status = BridgeStatus.ONLINE
            bridge_config.last_sync = datetime.now(UTC).isoformat()
            logger.info(
                "bridge_enabled protocol=%s matrix_room=%s remote_room=%s",
                protocol.value,
                matrix_room_id,
                remote_room_id,
            )
        else:
            bridge_config.status = BridgeStatus.ERROR
            logger.warning(
                "bridge_connect_failed protocol=%s matrix_room=%s",
                protocol.value,
                matrix_room_id,
            )

        self._bridges[protocol] = bridge_config
        return bridge_config

    async def disable_bridge(self, protocol: Protocol) -> bool:
        """Disable a bridge."""
        bridge = self._bridges.get(protocol)
        if bridge:
            bridge.enabled = False
            bridge.status = BridgeStatus.OFFLINE
            await self._disconnect_adapter(protocol)
            logger.info("bridge_disabled protocol=%s", protocol.value)
            return True
        return False

    async def _connect_adapter(self, protocol: Protocol, config: dict[str, Any]) -> bool:
        """Connect to a protocol adapter. Placeholder for actual bridge connection."""
        # In production, this would:
        # - Instantiate the correct bridge adapter class
        # - Authenticate with the remote protocol
        # - Set up the bridge matrix bot
        # - Register webhooks for incoming messages
        try:
            logger.debug("bridge_connecting protocol=%s", protocol.value)
            # Simulated connection delay
            await self._simulate_delay(0.5)
            return True
        except Exception as e:
            logger.error("bridge_connect_error protocol=%s err=%s", protocol.value, e)
            return False

    async def _disconnect_adapter(self, protocol: Protocol) -> None:
        """Disconnect from a protocol. Placeholder."""
        logger.debug("bridge_disconnecting protocol=%s", protocol.value)

    # ── Message Routing ──────────────────────────────────────────────

    async def route_message(
        self,
        message: ChatMessage,
        source_protocol: Protocol,
        target_protocols: list[Protocol],
    ) -> dict[str, Any]:
        """Route a message from source protocol to all target protocols.

        The message passes through Matrix (canonical) then fans out
        to all bridged protocols.
        """
        results: dict[str, bool] = {}
        errors: list[str] = []

        for target in target_protocols:
            if target == source_protocol:
                continue

            bridge = self._bridges.get(target)
            if not bridge or not bridge.enabled:
                logger.debug("bridge_skipped protocol=%s (not enabled)", target.value)
                continue

            try:
                # In production, this sends the message through the
                # protocol's bridge adapter
                success = await self._send_to_bridge(message, target)
                results[target.value] = success
                if success:
                    bridge.message_count += 1
                else:
                    bridge.error_count += 1
                    errors.append(f"Failed to send to {target.value}")
            except Exception as e:
                results[target.value] = False
                bridge.error_count += 1
                errors.append(f"Error sending to {target.value}: {e}")

        return {
            "message_id": message.id,
            "source_protocol": source_protocol.value,
            "target_results": results,
            "success_count": sum(1 for v in results.values() if v),
            "error_count": len(errors),
            "errors": errors,
        }

    async def _send_to_bridge(self, message: ChatMessage, target: Protocol) -> bool:
        """Send a message to a specific protocol bridge. Placeholder."""
        # In production, this would:
        # 1. Translate the message format to the target protocol
        # 2. Apply protocol-specific formatting limits
        # 3. Send via the bridge adapter
        # 4. Handle delivery confirmations
        await self._simulate_delay(0.1)
        return True

    async def handle_incoming(self, protocol: Protocol, raw_message: dict[str, Any]) -> ChatMessage | None:
        """Handle an incoming message from a bridged protocol."""
        # In production:
        # 1. Receives webhook/event from bridge adapter
        # 2. Translates to canonical ChatMessage format
        # 3. Pushes to the associated Matrix room
        # 4. Fans out to other bridged protocols
        bridge = self._bridges.get(protocol)
        if not bridge or not bridge.enabled:
            logger.warning("bridge_incoming_dropped protocol=%s (disabled)", protocol.value)
            return None

        message = ChatMessage(
            id=f"msg-{uuid4().hex}",
            room_id=bridge.matrix_room_id or "",
            sender=raw_message.get("sender", "unknown"),
            content=raw_message.get("content", ""),
            protocol=protocol,
            status=MessageStatus.DELIVERED,
            encrypted=raw_message.get("encrypted", False),
            metadata=raw_message.get("metadata", {}),
        )

        bridge.last_sync = datetime.now(UTC).isoformat()
        bridge.message_count += 1

        logger.debug("bridge_incoming protocol=%s message=%s", protocol.value, message.id[:16])
        return message

    # ── Health Monitoring ─────────────────────────────────────────────

    async def check_bridge_health(self, protocol: Protocol) -> BridgeStatus:
        """Check health of a specific bridge."""
        bridge = self._bridges.get(protocol)
        if not bridge:
            return BridgeStatus.OFFLINE
        if not bridge.enabled:
            return BridgeStatus.OFFLINE

        # Check health from cache (5 second TTL)
        cached = self._health_cache.get(protocol)
        if cached and time.time() - cached[1] < 5:
            return cached[0]

        # Simulate health check (in production, pings the bridge)
        await self._simulate_delay(0.3)
        status = BridgeStatus.ONLINE

        self._health_cache[protocol] = (status, time.time())
        return status

    async def check_all_bridges(self) -> dict[Protocol, BridgeStatus]:
        """Check health of all bridges."""
        results: dict[Protocol, BridgeStatus] = {}
        for protocol in list(self._bridges.keys()):
            results[protocol] = await self.check_bridge_health(protocol)
        return results

    def get_bridge_config(self, protocol: Protocol) -> BridgeConfig | None:
        """Get the configuration for a bridge."""
        return self._bridges.get(protocol)

    def list_active_bridges(self) -> list[BridgeConfig]:
        """List all active bridges."""
        return [b for b in self._bridges.values() if b.enabled]

    def list_all_bridges(self) -> list[BridgeConfig]:
        """List all bridges (active and inactive)."""
        return list(self._bridges.values())

    # ── Protocol-Specific Format Helpers ───────────────────────────────

    @staticmethod
    def format_for_protocol(content: str, target: Protocol, max_length: int = 4000) -> str:
        """Format message content according to protocol limits.

        Protocol limits (from test_cross_protocol.py):
        - peertube: 50000
        - lemmy: 10000
        - pixelfed: 2200
        - matrix: 65536
        - telegram: 4096
        - farcaster: 320
        - nostr: 64000
        - bluesky: 300
        """
        protocol_limits: dict[Protocol, int] = {
            Protocol.MATRIX: 65536,
            Protocol.TELEGRAM: 4096,
            Protocol.FARCASTER: 320,
            Protocol.NOSTR: 64000,
            Protocol.BLUESKY: 300,
            Protocol.ACTIVITYPUB: 10000,
            Protocol.LENS: 320,
            Protocol.XMTP: 65536,
            Protocol.DISCORD: 2000,
            Protocol.IRC: 512,
            Protocol.XMPP: 65536,
            Protocol.SIGNAL: 4096,
            Protocol.WEB3: 4096,
        }

        limit = min(protocol_limits.get(target, max_length), max_length)
        if len(content) > limit:
            return content[:limit]
        return content

    # ── Helpers ────────────────────────────────────────────────────────

    async def _simulate_delay(self, seconds: float) -> None:
        """Simulate async I/O delay."""
        import asyncio

        await asyncio.sleep(seconds)
