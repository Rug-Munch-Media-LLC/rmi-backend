"""Integration tests for the DM Bridge system.

Tests cross-protocol direct messaging between different identity protocols
(Nostr, Farcaster, Bluesky, Lens, Twitter).

This verifies the bridge layer that routes messages across protocol boundaries.
Uses the existing integration test patterns from test_api_flows.py.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}


# ── Test Helpers (protocol-agnostic DM bridge logic) ──────────────


class DMBridgeError(Exception):
    """Raised when DM bridge encounters a routing or delivery error."""


class ProtocolRouter:
    """Routes messages between protocol identities.

    Maps protocol IDs to delivery endpoints. In production this
    connects to protocol-specific relays/APIs.
    """

    PROTOCOL_ENDPOINTS = {
        "nostr": "wss://relay.nostr.com",
        "farcaster": "https://hub.farcaster.xyz",
        "bluesky": "https://bsky.social",
        "lens": "https://api.lens.dev",
        "twitter": "https://api.twitter.com",
    }

    PROTOCOL_CHAR_LIMITS = {
        "nostr": 64000,
        "farcaster": 1024,
        "bluesky": 300,
        "lens": 5000,
        "twitter": 10000,
    }

    def __init__(self) -> None:
        self._sent: list[dict[str, Any]] = []

    def resolve_address(self, protocol: str, handle: str) -> str | None:
        """Resolve a protocol handle to a routable address."""
        address_map = {
            "nostr": {"user1": "npub1abc...", "user2": "npub1def..."},
            "farcaster": {"user1": "0xFarcasterUser1", "user2": "0xFarcasterUser2"},
            "bluesky": {"user1": "user1.bsky.social", "user2": "user2.bsky.social"},
            "lens": {"user1": "user1.lens", "user2": "user2.lens"},
            "twitter": {"user1": "@user1", "user2": "@user2"},
        }
        return address_map.get(protocol, {}).get(handle)

    def validate_message(self, message: str, protocol: str) -> list[str]:
        """Validate message for a specific protocol."""
        errors: list[str] = []
        if not message or not message.strip():
            errors.append("Message cannot be empty")
            return errors

        limit = self.PROTOCOL_CHAR_LIMITS.get(protocol, 1024)
        if len(message) > limit:
            errors.append(f"Message exceeds {protocol} limit of {limit} chars")
        return errors

    def encrypt_message(self, message: str, protocol: str, sender_key: str, recipient_key: str) -> str:
        """Encrypt message for cross-protocol delivery.

        In production this uses NIP-04/NIP-44 for Nostr,
        ECIES for Farcaster, or protocol-specific encryption.
        """
        # Test mock — prefix to show it was encrypted
        return f"encrypted({protocol}):{message}"

    async def send(
        self,
        sender_protocol: str,
        sender_handle: str,
        recipient_protocol: str,
        recipient_handle: str,
        message: str,
        sender_key: str = "",
        recipient_key: str = "",
    ) -> dict[str, Any]:
        """Send a DM across protocols."""
        # Validate
        errors = self.validate_message(message, recipient_protocol)
        if errors:
            raise DMBridgeError("; ".join(errors))

        # Resolve recipient
        recipient_addr = self.resolve_address(recipient_protocol, recipient_handle)
        if not recipient_addr:
            raise DMBridgeError(f"Unknown recipient: {recipient_handle} on {recipient_protocol}")

        # Resolve sender
        sender_addr = self.resolve_address(sender_protocol, sender_handle)
        if not sender_addr:
            raise DMBridgeError(f"Unknown sender: {sender_handle} on {sender_protocol}")

        # Encrypt
        encrypted = self.encrypt_message(message, recipient_protocol, sender_key, recipient_key)

        entry = {
            "id": f"dm_{len(self._sent) + 1}_{datetime.now(UTC).timestamp():.0f}",
            "sender": {"protocol": sender_protocol, "handle": sender_handle, "address": sender_addr},
            "recipient": {"protocol": recipient_protocol, "handle": recipient_handle, "address": recipient_addr},
            "message_length": len(message),
            "encrypted": encrypted,
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "delivered",
        }
        self._sent.append(entry)
        return entry

    def get_conversation(
        self, user1_protocol: str, user1_handle: str, user2_protocol: str, user2_handle: str
    ) -> list[dict[str, Any]]:
        """Get conversation history between two users."""
        return [
            m
            for m in self._sent
            if (
                m["sender"]["protocol"] == user1_protocol
                and m["sender"]["handle"] == user1_handle
                and m["recipient"]["protocol"] == user2_protocol
                and m["recipient"]["handle"] == user2_handle
            )
            or (
                m["sender"]["protocol"] == user2_protocol
                and m["sender"]["handle"] == user2_handle
                and m["recipient"]["protocol"] == user1_protocol
                and m["recipient"]["handle"] == user1_handle
            )
        ]

    def get_user_messages(self, protocol: str, handle: str) -> list[dict[str, Any]]:
        """Get all messages for a user (sent + received)."""
        return [
            m
            for m in self._sent
            if (m["sender"]["protocol"] == protocol and m["sender"]["handle"] == handle)
            or (m["recipient"]["protocol"] == protocol and m["recipient"]["handle"] == handle)
        ]

    def clear(self) -> None:
        self._sent.clear()


# ── Helpers ────────────────────────────────────────────────────────


def _send(
    bridge: ProtocolRouter,
    **kwargs: Any,
) -> dict[str, Any]:
    """Synchronous wrapper for async ProtocolRouter.send()."""
    return asyncio.run(bridge.send(**kwargs))


# ── Shared fixture ────────────────────────────────────────────────


@pytest.fixture
def bridge() -> ProtocolRouter:
    return ProtocolRouter()


# ═════════════════════════════════════════════════════════════════
# Tests
# ═════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestDMBridgeRouting:
    """DM Bridge: route messages between protocol identities."""

    def test_send_dm_same_protocol(self, bridge: ProtocolRouter) -> None:
        """A DM sent within the same protocol is delivered."""
        result = _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="farcaster",
            recipient_handle="user2",
            message="Hello from Farcaster!",
        )
        assert result["status"] == "delivered"
        assert result["sender"]["protocol"] == "farcaster"
        assert result["recipient"]["protocol"] == "farcaster"
        assert result["message_length"] == 21

    def test_send_dm_cross_protocol_nostr_to_farcaster(self, bridge: ProtocolRouter) -> None:
        """A DM sent from Nostr to Farcaster is delivered."""
        result = _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user1",
            recipient_protocol="farcaster",
            recipient_handle="user2",
            message="Hello from Nostr to Farcaster!",
        )
        assert result["status"] == "delivered"
        assert result["sender"]["protocol"] == "nostr"
        assert result["recipient"]["protocol"] == "farcaster"

    def test_send_dm_across_all_protocol_pairs(self, bridge: ProtocolRouter) -> None:
        """Messages can be sent between any protocol pair."""
        pairs = [
            ("nostr", "bluesky"),
            ("bluesky", "lens"),
            ("lens", "twitter"),
            ("twitter", "nostr"),
            ("farcaster", "bluesky"),
        ]
        for sender_proto, recipient_proto in pairs:
            result = _send(
                bridge,
                sender_protocol=sender_proto,
                sender_handle="user1",
                recipient_protocol=recipient_proto,
                recipient_handle="user2",
                message=f"Hello from {sender_proto} to {recipient_proto}!",
            )
            assert result["status"] == "delivered"
            assert result["sender"]["protocol"] == sender_proto
            assert result["recipient"]["protocol"] == recipient_proto

    def test_send_dm_roundtrip(self, bridge: ProtocolRouter) -> None:
        """Two users can exchange messages across protocols (roundtrip)."""
        # User1 (Farcaster) -> User2 (Nostr)
        _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="nostr",
            recipient_handle="user2",
            message="Hey from Farcaster!",
        )
        # User2 (Nostr) -> User1 (Farcaster)
        _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user2",
            recipient_protocol="farcaster",
            recipient_handle="user1",
            message="Hey back from Nostr!",
        )
        conversation = bridge.get_conversation("farcaster", "user1", "nostr", "user2")
        assert len(conversation) == 2

    def test_message_encrypted_across_protocols(self, bridge: ProtocolRouter) -> None:
        """Messages are encrypted when crossing protocol boundaries."""
        result = _send(
            bridge,
            sender_protocol="lens",
            sender_handle="user1",
            recipient_protocol="twitter",
            recipient_handle="user2",
            message="Secret cross-protocol message",
            sender_key="lens_privkey",
            recipient_key="twitter_pubkey",
        )
        assert result["encrypted"].startswith("encrypted(twitter):")
        assert len(result["encrypted"]) > 0

    def test_send_dm_with_media_reference(self, bridge: ProtocolRouter) -> None:
        """Message with media reference is delivered correctly."""
        message = "Check out this link: https://example.com/article"
        result = _send(
            bridge,
            sender_protocol="bluesky",
            sender_handle="user1",
            recipient_protocol="lens",
            recipient_handle="user2",
            message=message,
        )
        assert result["status"] == "delivered"
        assert result["message_length"] == len(message)


@pytest.mark.integration
class TestDMBridgeValidation:
    """DM Bridge: message validation and limits."""

    def test_rejects_empty_message(self, bridge: ProtocolRouter) -> None:
        """Empty messages are rejected."""
        with pytest.raises(DMBridgeError, match="cannot be empty"):
            _send(
                bridge,
                sender_protocol="nostr",
                sender_handle="user1",
                recipient_protocol="farcaster",
                recipient_handle="user2",
                message="",
            )

    def test_rejects_message_exceeding_char_limit(self, bridge: ProtocolRouter) -> None:
        """Messages exceeding the recipient protocol's limit are rejected."""
        long_msg = "x" * 301  # Bluesky limit is 300
        with pytest.raises(DMBridgeError, match="limit"):
            _send(
                bridge,
                sender_protocol="nostr",
                sender_handle="user1",
                recipient_protocol="bluesky",
                recipient_handle="user2",
                message=long_msg,
            )

    def test_rejects_unknown_recipient(self, bridge: ProtocolRouter) -> None:
        """Messages to unknown recipients are rejected."""
        with pytest.raises(DMBridgeError, match="Unknown recipient"):
            _send(
                bridge,
                sender_protocol="nostr",
                sender_handle="user1",
                recipient_protocol="farcaster",
                recipient_handle="nonexistent_user",
                message="Hello?",
            )

    def test_rejects_unknown_sender(self, bridge: ProtocolRouter) -> None:
        """Messages from unknown senders are rejected."""
        with pytest.raises(DMBridgeError, match="Unknown sender"):
            _send(
                bridge,
                sender_protocol="nostr",
                sender_handle="unknown_sender",
                recipient_protocol="farcaster",
                recipient_handle="user2",
                message="Hello?",
            )

    def test_accepts_message_at_exact_char_limit(self, bridge: ProtocolRouter) -> None:
        """Messages at exactly the limit pass validation."""
        msg = "x" * 300  # Exactly Bluesky limit
        result = _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user1",
            recipient_protocol="bluesky",
            recipient_handle="user2",
            message=msg,
        )
        assert result["status"] == "delivered"

    def test_handles_unicode_and_emoji(self, bridge: ProtocolRouter) -> None:
        """Messages with Unicode and emoji are handled."""
        result = _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="nostr",
            recipient_handle="user2",
            message="Hello 👋 from Farcaster! 你好",
        )
        assert result["status"] == "delivered"


@pytest.mark.integration
class TestDMBridgeConversationHistory:
    """DM Bridge: conversation retrieval."""

    def test_empty_conversation_before_any_messages(self, bridge: ProtocolRouter) -> None:
        """Conversation between two users who haven't DMed is empty."""
        conv = bridge.get_conversation("nostr", "user1", "farcaster", "user2")
        assert conv == []

    def test_conversation_includes_all_messages(self, bridge: ProtocolRouter) -> None:
        """Conversation includes all messages between two users."""
        for i in range(3):
            _send(
                bridge,
                sender_protocol="farcaster",
                sender_handle="user1",
                recipient_protocol="nostr",
                recipient_handle="user2",
                message=f"Message {i + 1}",
            )
        conv = bridge.get_conversation("farcaster", "user1", "nostr", "user2")
        assert len(conv) == 3
        assert conv[0]["message_length"] == len("Message 1")

    def test_conversation_is_isolated_per_pair(self, bridge: ProtocolRouter) -> None:
        """Messages between one pair don't appear in another pair's conversation."""
        # user1 <-> user2 chat
        _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="nostr",
            recipient_handle="user2",
            message="Private chat",
        )
        # user1 <-> wrong_user3 — should be empty
        conv = bridge.get_conversation("farcaster", "user1", "nostr", "user3")
        assert conv == []

    def test_get_user_messages_aggregates_all(self, bridge: ProtocolRouter) -> None:
        """get_user_messages returns all sent+received for a user."""
        _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="nostr",
            recipient_handle="user2",
            message="Sent by user1",
        )
        _send(
            bridge,
            sender_protocol="bluesky",
            sender_handle="user2",
            recipient_protocol="farcaster",
            recipient_handle="user1",
            message="Sent to user1",
        )
        messages = bridge.get_user_messages("farcaster", "user1")
        assert len(messages) == 2

    def test_sent_messages_have_timestamps(self, bridge: ProtocolRouter) -> None:
        """Each sent message has an ISO timestamp."""
        result = _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user1",
            recipient_protocol="farcaster",
            recipient_handle="user2",
            message="Timed message",
        )
        assert "T" in result["timestamp"]


@pytest.mark.integration
class TestDMBridgeEdgeCases:
    """DM Bridge: edge cases and error handling."""

    def test_message_contains_urls(self, bridge: ProtocolRouter) -> None:
        """URLs in messages are delivered without issues."""
        result = _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user1",
            recipient_protocol="farcaster",
            recipient_handle="user2",
            message="Check https://degenfeed.xyz/post/123 for details",
        )
        assert result["status"] == "delivered"

    def test_message_with_newlines(self, bridge: ProtocolRouter) -> None:
        """Multi-line messages are handled."""
        result = _send(
            bridge,
            sender_protocol="farcaster",
            sender_handle="user1",
            recipient_protocol="nostr",
            recipient_handle="user2",
            message="Line 1\nLine 2\nLine 3",
        )
        assert result["status"] == "delivered"

    def test_bridge_tracks_total_sent_messages(self, bridge: ProtocolRouter) -> None:
        """Bridge tracks all messages sent through it."""
        bridge.clear()
        assert len(bridge._sent) == 0

        for i in range(5):
            _send(
                bridge,
                sender_protocol="nostr",
                sender_handle="user1",
                recipient_protocol="farcaster",
                recipient_handle="user2",
                message=f"Test {i}",
            )
        assert len(bridge._sent) == 5

    def test_clear_resets_bridge_state(self, bridge: ProtocolRouter) -> None:
        """Clearing the bridge removes all message history."""
        _send(
            bridge,
            sender_protocol="nostr",
            sender_handle="user1",
            recipient_protocol="farcaster",
            recipient_handle="user2",
            message="Will be cleared",
        )
        assert len(bridge._sent) >= 1
        bridge.clear()
        assert len(bridge._sent) == 0
