"""Standardized Private Chat — Matrix fabric + Signal Protocol encryption.

Architecture:
  Matrix (Conduit) = universal chat fabric
  Signal Protocol = Double Ratchet + Kyber-1024 post-quantum encryption
  Bridge Layer = bidirectional protocol adapters to Matrix rooms

Every integration must have: error handling, rate limit tracking,
response validation, fallback. No silent failures.
"""

from app.services.chat.bridge_manager import BridgeManager
from app.services.chat.key_store import KeyStore
from app.services.chat.matrix_client import MatrixClient
from app.services.chat.models import (
    BridgeStatus,
    ChatMessage,
    ChatRoom,
    ChatUser,
    EncryptionType,
    KeyBundle,
    MessageStatus,
    Protocol,
    RoomType,
    SafetyNumber,
)
from app.services.chat.protocol_selector import ProtocolSelector
from app.services.chat.signal_protocol import SignalProtocol

__all__ = [
    "BridgeManager",
    "BridgeStatus",
    "ChatMessage",
    "ChatRoom",
    "ChatUser",
    "EncryptionType",
    "KeyBundle",
    "KeyStore",
    "MatrixClient",
    "MessageStatus",
    "Protocol",
    "ProtocolSelector",
    "RoomType",
    "SafetyNumber",
    "SignalProtocol",
]
