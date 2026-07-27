"""Chat data models — rooms, messages, keys, bridges, protocols.

Single source of truth for all chat data types. Every model uses
Pydantic v2 with strict validation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────────────────


class Protocol(StrEnum):
    """Supported chat protocols. Matrix is the canonical fabric."""

    MATRIX = "matrix"
    NOSTR = "nostr"
    FARCASTER = "farcaster"
    BLUESKY = "bluesky"
    ACTIVITYPUB = "activitypub"  # Mastodon/Pleroma
    LENS = "lens"
    XMTP = "xmtp"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    IRC = "irc"
    XMPP = "xmpp"
    SIGNAL = "signal"  # Native Signal bridge (separate from protocol layer)
    WEB3 = "web3"  # Wallet-to-wallet chat


class RoomType(StrEnum):
    """Matrix room types."""

    DM = "dm"  # 1-on-1 direct message
    GROUP = "group"  # Multi-user group chat
    SPACE = "space"  # Room collection / space
    PUBLIC = "public"  # Open public room
    BRIDGED = "bridged"  # Cross-protocol bridged room


class EncryptionType(StrEnum):
    """Encryption schemes used across the chat system."""

    OLM = "olm"  # 1-on-1 Double Ratchet (Signal Protocol)
    MEGOLM = "megolm"  # Group ratchet
    NONE = "none"  # Unencrypted (public rooms)
    WEB3 = "web3"  # Wallet-derived keys


class MessageStatus(StrEnum):
    """Delivery status for messages."""

    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    PENDING = "pending"


class BridgeStatus(StrEnum):
    """Bridge health status."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    ERROR = "error"
    CONNECTING = "connecting"


# ── Chat Room ─────────────────────────────────────────────────────────────


class ChatRoom(BaseModel):
    """A chat room — always a Matrix room at the canonical layer."""

    id: str = Field(default_factory=lambda: f"!{uuid4().hex}:rugmunch.io")
    name: str
    topic: str | None = None
    room_type: RoomType = RoomType.DM
    encryption: EncryptionType = EncryptionType.OLM
    canonical_alias: str | None = None  # #alias:rugmunch.io
    members: list[str] = Field(default_factory=list)
    protocols: list[Protocol] = Field(default=[Protocol.MATRIX])
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_by: str | None = None
    bridged_to: dict[Protocol, str] = Field(default_factory=dict)  # protocol -> room_id
    avatar_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False, "extra": "ignore"}


# ── Chat Message ──────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A message in a chat room with full E2E metadata."""

    id: str = Field(default_factory=lambda: f"$event-{uuid4().hex}")
    room_id: str
    sender: str
    content: str
    content_type: str = "text"  # text, image, file, encrypted, reaction, etc.
    protocol: Protocol = Protocol.MATRIX
    status: MessageStatus = MessageStatus.SENDING
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    edited_at: str | None = None
    deleted_at: str | None = None
    reply_to: str | None = None  # message ID this is a reply to
    event_id: str | None = None  # Matrix event ID
    transaction_id: str | None = None  # Idempotency key for send
    metadata: dict[str, Any] = Field(default_factory=dict)
    encrypted: bool = True

    model_config = {"frozen": False, "extra": "ignore"}


# ── Chat User ──────────────────────────────────────────────────────────────


class ChatUser(BaseModel):
    """A chat user with cross-protocol identity mapping."""

    id: str  # Local user ID
    matrix_id: str  # @user:rugmunch.io
    display_name: str | None = None
    avatar_url: str | None = None

    # Cross-protocol identity mappings
    identities: dict[Protocol, str] = Field(default_factory=dict)

    # Key verification
    device_id: str | None = None
    online: bool = False
    last_seen: str | None = None

    model_config = {"frozen": False, "extra": "ignore"}


# ── Key Management ────────────────────────────────────────────────────────


class KeyBundle(BaseModel):
    """Signal Protocol key bundle for initial key exchange (X3DH + Kyber-1024)."""

    identity_key: bytes  # Public identity key (Ed25519)
    signed_pre_key: bytes  # Signed pre-key
    signed_pre_key_signature: bytes  # Signature of pre-key by identity key
    one_time_pre_keys: list[bytes]  # Batch of one-time pre-keys
    kyber_kem_key: bytes | None = None  # Kyber-1024 public key for PQ
    protocol_version: int = 1
    device_id: str
    user_id: str

    model_config = {"frozen": False, "extra": "ignore"}


class SafetyNumber(BaseModel):
    """Safety numbers for key verification (QR-compatible)."""

    user_id: str
    their_user_id: str
    numeric_fingerprint: str  # 12-digit numeric comparison
    qr_code_data: str  # Base64 encoded QR payload
    verified: bool = False
    verified_at: str | None = None

    model_config = {"frozen": False, "extra": "ignore"}


# ── Bridge Models ─────────────────────────────────────────────────────────


class BridgeConfig(BaseModel):
    """Configuration for a protocol bridge."""

    protocol: Protocol
    enabled: bool = False
    status: BridgeStatus = BridgeStatus.OFFLINE
    matrix_room_id: str | None = None  # The Matrix room bridged to
    remote_room_id: str | None = None  # The remote protocol's room/channel
    last_sync: str | None = None
    error_count: int = 0
    message_count: int = 0
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": False, "extra": "ignore"}
