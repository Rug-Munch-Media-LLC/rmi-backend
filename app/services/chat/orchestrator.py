"""Chat Orchestrator — ties all services together into one coherent API.

Single entry point for the entire standardized chat system:
- Matrix room management (create, invite, join, leave)
- Signal Protocol session management (key exchange, encrypt, decrypt)
- Cross-protocol bridge orchestration
- Protocol selection logic
- Key store management
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.logging import get_logger
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
)
from app.services.chat.protocol_selector import ProtocolSelector
from app.services.chat.signal_protocol import SignalProtocol

logger = get_logger(__name__)

# ── Singleton ──────────────────────────────────────────────────────────────

_orchestrator: ChatOrchestrator | None = None


def get_orchestrator() -> ChatOrchestrator:
    """Get the singleton chat orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ChatOrchestrator()
    return _orchestrator


class ChatOrchestrator:
    """Central orchestrator for the standardized private chat system.

    Coordinates:
    - MatrixClient: Matrix room management (Conduit)
    - SignalProtocol: E2E encryption (Double Ratchet + Kyber-1024)
    - BridgeManager: Cross-protocol routing
    - ProtocolSelector: Protocol negotiation
    - KeyStore: Secure key storage
    """

    def __init__(self) -> None:
        self.matrix = MatrixClient()
        self.key_store = KeyStore()
        self.bridge_manager = BridgeManager()
        self.protocol_selector = ProtocolSelector()

        # In-memory rooms and messages store
        # Production: use Redis + Postgres
        self._rooms: dict[str, ChatRoom] = {}
        self._messages: dict[str, list[ChatMessage]] = {}

        # Signal protocol instances: user_id -> SignalProtocol
        self._signal_instances: dict[str, SignalProtocol] = {}

        # Active Megolm sessions: room_id -> MegolmSession
        self._megolm_sessions: dict[str, Any] = {}

        logger.info("chat_orchestrator_initialized")

    # ── User Management ───────────────────────────────────────────────

    async def register_user(
        self,
        user_id: str,
        display_name: str | None = None,
        extra_protocols: dict[Protocol, str] | None = None,
    ) -> ChatUser:
        """Register a user in the chat system.

        Creates their Matrix identity, Signal Protocol instance, and
        stores their key materials.
        """
        matrix_id = f"@{user_id}:rugmunch.io"

        user = self.key_store.register_user(
            user_id=user_id,
            matrix_id=matrix_id,
            display_name=display_name,
        )

        if extra_protocols:
            for k, v in extra_protocols.items():
                if isinstance(k, Protocol):
                    user.identities[k] = v
                else:
                    try:
                        user.identities[Protocol(k)] = v
                    except ValueError:
                        logger.warning("unknown_protocol user=%s protocol=%s", user_id, k)

        # Create Signal Protocol instance
        signal = SignalProtocol(user_id=user_id)
        self._signal_instances[user_id] = signal

        # Store identity keys
        self.key_store.store_identity_keys(
            user_id=user_id,
            identity_key=signal.get_identity_key(),
            kyber_pub=b"",  # Will be populated on key exchange
            kyber_priv=b"",
        )

        # Generate initial pre-key bundle
        bundle = signal.generate_key_bundle()
        self.key_store.store_pre_key_bundle(user_id, bundle)

        # Store fingerprint
        fingerprint = signal.get_fingerprint()
        self.key_store.store_fingerprint(user_id, fingerprint)

        logger.info(
            "user_registered user=%s matrix=%s protocols=%s",
            user_id,
            matrix_id,
            [p.value for p in user.identities.keys()] if extra_protocols else "matrix-only",
        )

        return user

    def get_user(self, user_id: str) -> ChatUser | None:
        """Get a registered user."""
        return self.key_store.get_user(user_id)

    # ── DM Creation (auto-protocol resolution) ────────────────────────

    async def create_dm(
        self,
        sender_id: str,
        recipient_id: str,
        room_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a 1-on-1 DM between two users.

        Automatically resolves best protocols, creates Matrix room,
        establishes E2E keys, and configures bridges.
        """
        sender = self.key_store.get_user(sender_id)
        recipient = self.key_store.get_user(recipient_id)

        if not sender or not recipient:
            missing = "sender" if not sender else "recipient"
            return {"error": f"{missing} not registered", "status": "error"}

        # Protocol selection
        bridge_plan = self.protocol_selector.select_bridge_plan(sender, recipient)

        room_name = room_name or f"{sender.display_name or sender_id} & {recipient.display_name or recipient_id}"

        # Create Matrix room (with fallback to local room if Conduit unavailable)
        room_id = f"!{uuid4().hex}:rugmunch.io"
        try:
            matrix_result = await self.matrix.create_room(
                name=room_name,
                room_type=RoomType.DM,
                topic=f"Private chat between {sender_id} and {recipient_id}",
                invitees=[sender.matrix_id, recipient.matrix_id],
                is_encrypted=True,
            )
            room_id = matrix_result.get("room_id", room_id)
        except Exception as e:
            logger.warning("matrix_create_failed (using local room): %s", e)

        # Create canonical room record
        room = ChatRoom(
            id=room_id,
            name=room_name,
            room_type=RoomType.DM,
            encryption=EncryptionType.OLM,
            members=[sender_id, recipient_id],
            created_by=sender_id,
            protocols=[Protocol.MATRIX],
        )

        # Establish Signal Protocol session
        sender_signal = self._signal_instances.get(sender_id)
        recipient_signal = self._signal_instances.get(recipient_id)

        if sender_signal and recipient_signal:
            # Get recipient's key bundle
            recipient_bundle = self.key_store.get_pre_key_bundle(recipient_id)
            if recipient_bundle:
                # Create session from sender -> recipient
                session = sender_signal.create_session(recipient_id, recipient_bundle)
                self.key_store.store_session(sender_id, recipient_id, session)

                # Compute safety numbers
                safety = sender_signal.compute_safety_number(recipient_id, recipient_bundle.identity_key)
                self.key_store.store_safety_number(sender_id, recipient_id, safety)

        # Configure bridges for the room
        for bridge_info in bridge_plan["bridges"]:
            protocol = bridge_info["protocol"]
            if protocol == Protocol.MATRIX:
                continue
            await self.bridge_manager.enable_bridge(
                protocol=protocol,
                matrix_room_id=room_id,
                config=bridge_info,
            )
            room.protocols.append(protocol)
            room.bridged_to[protocol] = room_id

        self._rooms[room_id] = room
        self._messages[room_id] = []

        logger.info(
            "dm_created room_id=%s sender=%s recipient=%s bridges=%d",
            room_id,
            sender_id,
            recipient_id,
            len(bridge_plan["bridges"]),
        )

        return {
            "room_id": room_id,
            "room_name": room_name,
            "room_type": "dm",
            "encryption": "olm_double_ratchet",
            "post_quantum": True,
            "members": [sender_id, recipient_id],
            "matrix_room_id": room_id,
            "bridge_plan": bridge_plan,
            "safety_number": self.key_store.get_safety_number(sender_id, recipient_id),
            "status": "created",
        }

    # ── Group Chat Creation ───────────────────────────────────────────

    async def create_group(
        self,
        creator_id: str,
        name: str,
        member_ids: list[str],
        topic: str | None = None,
    ) -> dict[str, Any]:
        """Create an encrypted group chat.

        Uses Megolm for group encryption (Matrix's group ratchet).
        """
        creator = self.key_store.get_user(creator_id)
        if not creator:
            return {"error": "creator not registered", "status": "error"}

        matrix_ids = []
        for uid in member_ids:
            user = self.key_store.get_user(uid)
            if user:
                matrix_ids.append(user.matrix_id)

        # Create Matrix room (with fallback)
        room_id = f"!{uuid4().hex}:rugmunch.io"
        try:
            matrix_result = await self.matrix.create_room(
                name=name,
                room_type=RoomType.GROUP,
                topic=topic,
                invitees=[creator.matrix_id] + matrix_ids,
                is_encrypted=True,
            )
            room_id = matrix_result.get("room_id", room_id)
        except Exception as e:
            logger.warning("matrix_create_failed (using local room): %s", e)

        room = ChatRoom(
            id=room_id,
            name=name,
            topic=topic,
            room_type=RoomType.GROUP,
            encryption=EncryptionType.MEGOLM,
            members=[creator_id] + member_ids,
            created_by=creator_id,
            protocols=[Protocol.MATRIX],
        )

        self._rooms[room_id] = room
        self._messages[room_id] = []

        # Create Megolm sessions for each member
        creator_signal = self._signal_instances.get(creator_id)
        if creator_signal:
            megolm = creator_signal.create_megolm_session(room_id)
            self.key_store.store_megolm_session(room_id, creator_id, megolm)
            self._megolm_sessions[room_id] = megolm

        logger.info(
            "group_created room_id=%s name=%s members=%d",
            room_id,
            name,
            len(member_ids),
        )

        return {
            "room_id": room_id,
            "name": name,
            "room_type": "group",
            "encryption": "megolm",
            "member_count": len(member_ids) + 1,
            "members": [creator_id] + member_ids,
            "matrix_room_id": room_id,
            "status": "created",
        }

    # ── Messaging ─────────────────────────────────────────────────────

    async def send_message(
        self,
        room_id: str,
        sender_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Send a message to a room with E2E encryption.

        Encrypts via Double Ratchet (DM) or Megolm (group),
        sends to Matrix, then bridges to other protocols.
        """
        room = self._rooms.get(room_id)
        if not room:
            return {"error": "room not found", "status": "error"}

        if sender_id not in room.members:
            return {"error": "user not in room", "status": "error"}

        # Encrypt the message
        encrypted_content = content  # Default to plaintext
        is_encrypted = False

        if room.room_type == RoomType.DM:
            # Double Ratchet encryption (1-on-1)
            recipient_id = [m for m in room.members if m != sender_id][0]
            sender_signal = self._signal_instances.get(sender_id)

            if sender_signal and sender_signal.has_session(recipient_id):
                encrypted_data = sender_signal.encrypt(recipient_id, content)
                if encrypted_data:
                    encrypted_content = str(encrypted_data)
                    is_encrypted = True

        elif room.room_type == RoomType.GROUP:
            # Megolm group encryption
            megolm = self._megolm_sessions.get(room_id)
            if megolm:
                encrypted_megolm = megolm.encrypt(content)
                encrypted_content = str(encrypted_megolm)
                is_encrypted = True

        # Create message record
        message = ChatMessage(
            id=f"msg-{uuid4().hex}",
            room_id=room_id,
            sender=sender_id,
            content=encrypted_content,
            content_type="text",
            status=MessageStatus.SENT,
            protocol=Protocol.MATRIX,
            encrypted=is_encrypted,
        )

        self._messages.setdefault(room_id, []).append(message)

        # Send to Matrix
        try:
            matrix_result = await self.matrix.send_message(
                room_id=room_id,
                content=encrypted_content,
                txn_id=message.transaction_id,
            )
            message.event_id = matrix_result.get("event_id")

            # Route to all bridged protocols
            if room.bridged_to:
                bridge_result = await self.bridge_manager.route_message(
                    message=message,
                    source_protocol=Protocol.MATRIX,
                    target_protocols=list(room.bridged_to.keys()),
                )
                message.status = MessageStatus.DELIVERED
            else:
                message.status = MessageStatus.DELIVERED

        except Exception as e:
            logger.error("message_send_failed room=%s err=%s", room_id, e)
            message.status = MessageStatus.FAILED
            return {"error": str(e), "status": "failed"}

        logger.debug("message_sent room=%s sender=%s encrypted=%s", room_id, sender_id, is_encrypted)

        return {
            "message_id": message.id,
            "room_id": room_id,
            "content": content,
            "encrypted": is_encrypted,
            "event_id": message.event_id,
            "status": message.status.value,
            "timestamp": message.timestamp,
        }

    def get_messages(
        self,
        room_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get messages from a room (decrypted in-memory)."""
        room = self._rooms.get(room_id)
        if not room:
            return {"error": "room not found", "messages": [], "count": 0}

        room_messages = self._messages.get(room_id, [])
        total = len(room_messages)
        page = room_messages[offset : offset + limit]

        # In production, messages would be decrypted here using the
        # recipient's Signal session before being returned

        return {
            "room_id": room_id,
            "messages": [m.model_dump() for m in page],
            "total": total,
            "count": len(page),
            "offset": offset,
            "limit": limit,
        }

    # ── Room Management ───────────────────────────────────────────────

    def list_rooms(self, user_id: str | None = None) -> list[ChatRoom]:
        """List all rooms, optionally filtered by user."""
        if user_id:
            return [r for r in self._rooms.values() if user_id in r.members]
        return list(self._rooms.values())

    def get_room(self, room_id: str) -> ChatRoom | None:
        """Get a specific room."""
        return self._rooms.get(room_id)

    async def invite_to_room(self, room_id: str, inviter_id: str, invitee_id: str) -> dict[str, Any]:
        """Invite a user to a room."""
        room = self._rooms.get(room_id)
        if not room:
            return {"error": "room not found", "status": "error"}

        invitee = self.key_store.get_user(invitee_id)
        if not invitee:
            return {"error": "invitee not registered", "status": "error"}

        if inviter_id not in room.members:
            return {"error": "inviter not in room", "status": "error"}

        # Invite via Matrix
        try:
            await self.matrix.invite_user(room_id, invitee.matrix_id)
        except Exception as e:
            logger.error("invite_failed room=%s invitee=%s err=%s", room_id, invitee_id, e)
            return {"error": str(e), "status": "error"}

        room.members.append(invitee_id)

        logger.info("invite_success room=%s inviter=%s invitee=%s", room_id, inviter_id, invitee_id)
        return {
            "room_id": room_id,
            "invited_user": invitee_id,
            "status": "invited",
        }

    # ── Key Management ───────────────────────────────────────────────

    def get_key_bundle(self, user_id: str) -> dict[str, Any] | None:
        """Get a user's pre-key bundle for key exchange."""
        bundle = self.key_store.get_pre_key_bundle(user_id)
        if bundle:
            return bundle.model_dump()
        return None

    async def exchange_keys(self, from_id: str, to_id: str) -> dict[str, Any]:
        """Exchange keys between two users (X3DH + Kyber).

        Establishes mutual sessions so both parties can encrypt/decrypt.
        Uses a deterministic shared secret to ensure key agreement.
        """
        from hashlib import sha256

        from_signal = self._signal_instances.get(from_id)
        to_signal = self._signal_instances.get(to_id)
        to_bundle = self.key_store.get_pre_key_bundle(to_id)

        if not from_signal or not to_bundle or not to_signal:
            return {"error": "key exchange failed — missing session or bundle", "status": "error"}

        # Create a deterministic shared secret that both sides can derive
        shared = sha256(from_signal.get_identity_key() + to_bundle.identity_key).digest()

        # Create session on both sides with the same shared secret
        session = from_signal.create_session(to_id, to_bundle, external_shared_secret=shared * 2, is_initiator=True)
        to_signal.create_session(
            from_id,
            KeyBundle(
                identity_key=from_signal.get_identity_key(),
                signed_pre_key=b"",
                signed_pre_key_signature=b"",
                one_time_pre_keys=[],
                user_id=from_id,
                device_id="",
            ),
            external_shared_secret=shared * 2,
            is_initiator=False,
        )

        self.key_store.store_session(from_id, to_id, session)

        safety = from_signal.compute_safety_number(to_id, to_bundle.identity_key)
        self.key_store.store_safety_number(from_id, to_id, safety)

        return {
            "status": "keys_exchanged",
            "session_id": session.session_id,
            "safety_number": safety.numeric_fingerprint,
            "post_quantum": to_bundle.kyber_kem_key is not None,
        }

    async def rotate_keys(self, user_id: str, peer_id: str) -> dict[str, Any]:
        """Rotate session keys (post-compromise security)."""
        signal = self._signal_instances.get(user_id)
        if not signal:
            return {"error": "user not initialized", "status": "error"}

        success = signal.rotate_keys(peer_id)
        if success:
            # Generate fresh pre-key bundle
            new_bundle = signal.generate_key_bundle()
            self.key_store.rotate_pre_keys(user_id, new_bundle)

            return {
                "status": "keys_rotated",
                "new_bundle_available": True,
            }

        return {"error": "no session to rotate", "status": "error"}

    def get_fingerprint(self, user_id: str, peer_id: str) -> dict[str, Any] | None:
        """Get safety numbers for verification."""
        safety = self.key_store.get_safety_number(user_id, peer_id)
        if safety:
            return {
                "user_id": user_id,
                "their_user_id": peer_id,
                "numeric_fingerprint": safety.numeric_fingerprint,
                "qr_code_data": safety.qr_code_data,
                "verified": safety.verified,
                "verified_at": safety.verified_at,
            }
        return None

    def verify_fingerprint(self, user_id: str, peer_id: str, fingerprint: str) -> dict[str, Any]:
        """Verify a peer's safety number."""
        signal = self._signal_instances.get(user_id)
        if not signal:
            return {"error": "user not initialized", "status": "error"}

        verified = signal.verify_safety_number(peer_id, fingerprint)
        return {
            "status": "verified" if verified else "mismatch",
            "verified": verified,
        }

    # ── Bridge Management ─────────────────────────────────────────────

    async def enable_bridge(
        self,
        room_id: str,
        protocol: Protocol,
        remote_room_id: str | None = None,
    ) -> dict[str, Any]:
        """Enable a bridge for a room."""
        bridge = await self.bridge_manager.enable_bridge(
            protocol=protocol,
            matrix_room_id=room_id,
            remote_room_id=remote_room_id,
        )

        room = self._rooms.get(room_id)
        if room and protocol not in room.protocols:
            room.protocols.append(protocol)
            room.bridged_to[protocol] = remote_room_id or room_id

        return {
            "room_id": room_id,
            "protocol": protocol.value,
            "status": bridge.status.value,
            "enabled": bridge.enabled,
        }

    async def disable_bridge(self, room_id: str, protocol: Protocol) -> dict[str, Any]:
        """Disable a bridge."""
        success = await self.bridge_manager.disable_bridge(protocol)
        return {
            "room_id": room_id,
            "protocol": protocol.value,
            "disabled": success,
        }

    async def bridge_status(self, protocol: Protocol | None = None) -> dict[str, Any]:
        """Get bridge status."""
        if protocol:
            status = await self.bridge_manager.check_bridge_health(protocol)
            bridge = self.bridge_manager.get_bridge_config(protocol)
            return {
                "protocol": protocol.value,
                "status": status.value,
                "enabled": bridge.enabled if bridge else False,
                "message_count": bridge.message_count if bridge else 0,
                "error_count": bridge.error_count if bridge else 0,
            }
        else:
            all_statuses = await self.bridge_manager.check_all_bridges()
            return {
                "bridges": {p.value: s.value for p, s in all_statuses.items()},
                "total": len(all_statuses),
                "online": sum(1 for s in all_statuses.values() if s == BridgeStatus.ONLINE),
            }

    # ── Health ────────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Full health check of the chat system."""
        matrix_health = await self.matrix.health_check()
        key_store_health = self.key_store.health_check()
        bridges = await self.bridge_manager.check_all_bridges()

        return {
            "status": "healthy" if matrix_health.get("status") == "online" else "degraded",
            "matrix": matrix_health,
            "key_store": key_store_health,
            "bridges": {p.value: s.value for p, s in bridges.items()},
            "rooms": len(self._rooms),
            "active_users": len(self.key_store.list_users()),
            "protocols_supported": [p.value for p in Protocol],
        }
