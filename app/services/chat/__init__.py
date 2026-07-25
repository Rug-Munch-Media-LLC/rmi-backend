"""Standardized Private Chat — Matrix fabric + Signal Protocol encryption.

Architecture:
  Matrix (Conduit) = universal chat fabric
  Signal Protocol = Double Ratchet + Kyber-1024 post-quantum encryption
  Bridge Layer = bidirectional protocol adapters to Matrix rooms

Every integration must have: error handling, rate limit tracking,
response validation, fallback. No silent failures.
"""

from app.services.chat.models import (
    ChatRoom,
    ChatMessage,
    ChatUser,
    Protocol,
    RoomType,
    EncryptionType,
    MessageStatus,
    BridgeStatus,
    KeyBundle,
    SafetyNumber,
)
from app.services.chat.signal_protocol import SignalProtocol
from app.services.chat.matrix_client import MatrixClient
from app.services.chat.bridge_manager import BridgeManager
from app.services.chat.protocol_selector import ProtocolSelector
from app.services.chat.key_store import KeyStore

__all__ = [
    "ChatRoom",
    "ChatMessage",
    "ChatUser",
    "Protocol",
    "RoomType",
    "EncryptionType",
    "MessageStatus",
    "BridgeStatus",
    "KeyBundle",
    "SafetyNumber",
    "SignalProtocol",
    "MatrixClient",
    "BridgeManager",
    "ProtocolSelector",
    "KeyStore",
]
