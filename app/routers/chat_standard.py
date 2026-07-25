"""Standardized Private Chat API — Matrix fabric + Signal Protocol encryption.

This is THE canonical chat API. Every private chat, group chat, and
cross-protocol conversation routes through here.

Endpoints:
  POST   /api/v1/chat/dm/create         — Create 1-on-1 DM (auto protocol selection)
  POST   /api/v1/chat/group/create      — Create encrypted group chat
  GET    /api/v1/chat/rooms              — List all rooms (with optional user filter)
  GET    /api/v1/chat/room/{id}          — Get room details
  GET    /api/v1/chat/room/{id}/messages — Get messages
  POST   /api/v1/chat/room/{id}/send    — Send message to room
  POST   /api/v1/chat/room/{id}/invite  — Invite user to room
  POST   /api/v1/chat/keys/exchange     — X3DH + Kyber key exchange
  POST   /api/v1/chat/keys/rotate       — Rotate session keys
  GET    /api/v1/chat/keys/fingerprint  — Safety numbers for verification
  POST   /api/v1/chat/keys/verify       — Verify safety number
  POST   /api/v1/chat/bridge/enable     — Enable bridge for a protocol
  POST   /api/v1/chat/bridge/disable    — Disable bridge
  GET    /api/v1/chat/bridge/status     — Get bridge health
  POST   /api/v1/chat/user/register     — Register user in chat system
  GET    /api/v1/chat/user/{id}         — Get user info
  GET    /api/v1/chat/health            — Full system health check

All responses follow the standardized envelope format.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user, require_auth
from app.core.logging import get_logger
from app.services.chat import (
    BridgeStatus,
    ChatRoom,
    EncryptionType,
    Protocol,
    RoomType,
)
from app.services.chat.orchestrator import get_orchestrator

logger = get_logger("chat_standard_router")

router = APIRouter(prefix="/api/v1/chat", tags=["chat-standard"])


# ── Request Models ──────────────────────────────────────────────────────────


class CreateDMRequest(BaseModel):
    recipient_id: str = Field(..., description="User ID of the recipient")
    room_name: str | None = Field(default=None, max_length=200, description="Optional room name")


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Group chat name")
    member_ids: list[str] = Field(..., min_length=1, max_length=100, description="Initial members")
    topic: str | None = Field(default=None, max_length=500, description="Group topic")


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=65536, description="Message content")


class InviteRequest(BaseModel):
    user_id: str = Field(..., description="User ID to invite")


class KeyExchangeRequest(BaseModel):
    peer_id: str = Field(..., description="Peer user ID for key exchange")


class KeyRotateRequest(BaseModel):
    peer_id: str = Field(..., description="Peer user ID for key rotation")


class KeyVerifyRequest(BaseModel):
    peer_id: str = Field(..., description="Peer user ID")
    fingerprint: str = Field(..., description="Safety number fingerprint from peer")


class BridgeRequest(BaseModel):
    room_id: str = Field(..., description="Matrix room ID")
    protocol: Protocol = Field(..., description="Protocol to bridge")


class RegisterUserRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=100, description="User's unique ID")
    display_name: str | None = Field(default=None, max_length=100)
    protocols: dict[str, str] | None = Field(
        default=None,
        description='Extra protocol identities: {"nostr": "npub1...", "farcaster": "fid:..."}',
    )


# ── Response Helpers ────────────────────────────────────────────────────────


def _ok(data: Any = None, meta: dict | None = None) -> dict:
    """Standard success response."""
    return {"data": data, "error": None, "meta": meta or {}}


def _err(code: str, message: str, status: int = 400) -> HTTPException:
    """Standard error response."""
    raise HTTPException(
        status_code=status,
        detail={"code": code, "message": message, "request_id": None},
    )


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/dm/create", summary="Create 1-on-1 DM with auto protocol resolution")
async def create_dm(
    req: CreateDMRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Create a direct message between two users.

    Automatically:
    1. Checks which protocols both users have
    2. Creates a Matrix E2E room as the canonical chat space
    3. Establishes Signal Protocol session (Double Ratchet + Kyber-1024)
    4. Bridges to each user's preferred protocol
    5. Returns safety numbers for verification
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = await orchestrator.create_dm(
        sender_id=user_id,
        recipient_id=req.recipient_id,
        room_name=req.room_name,
    )

    if result.get("status") == "error":
        _err("DM_CREATION_FAILED", result.get("error", "Unknown error"), 400)

    logger.info(
        "dm_created user=%s recipient=%s room=%s",
        user_id,
        req.recipient_id,
        result.get("room_id"),
    )

    return _ok(
        data={
            "room_id": result["room_id"],
            "room_name": result["room_name"],
            "room_type": result["room_type"],
            "encryption": result["encryption"],
            "post_quantum": result.get("post_quantum", False),
            "members": result.get("members", []),
            "bridge_plan": result.get("bridge_plan", {}),
            "safety_number": result.get("safety_number"),
        }
    )


@router.post("/group/create", summary="Create encrypted group chat")
async def create_group(
    req: CreateGroupRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Create an encrypted group chat using Megolm ratchet."""
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    if user_id not in req.member_ids:
        req.member_ids = [user_id] + req.member_ids

    result = await orchestrator.create_group(
        creator_id=user_id,
        name=req.name,
        member_ids=req.member_ids,
        topic=req.topic,
    )

    if result.get("status") == "error":
        _err("GROUP_CREATION_FAILED", result.get("error", "Unknown error"), 400)

    logger.info(
        "group_created user=%s name=%s members=%d",
        user_id,
        req.name,
        len(req.member_ids),
    )

    return _ok(
        data={
            "room_id": result["room_id"],
            "name": result["name"],
            "room_type": result["room_type"],
            "encryption": result["encryption"],
            "member_count": result.get("member_count", 0),
            "members": result.get("members", []),
        }
    )


@router.get("/rooms", summary="List all rooms")
async def list_rooms(
    user: dict = Depends(require_auth),
) -> dict:
    """List all rooms the user is a member of."""
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    rooms = orchestrator.list_rooms(user_id=user_id)

    return _ok(
        data={
            "rooms": [
                {
                    "id": r.id,
                    "name": r.name,
                    "topic": r.topic,
                    "room_type": r.room_type.value,
                    "encryption": r.encryption.value,
                    "member_count": len(r.members),
                    "members": r.members,
                    "protocols": [p.value for p in r.protocols],
                    "bridged_to": {p.value: rid for p, rid in r.bridged_to.items()},
                    "created_at": r.created_at,
                    "created_by": r.created_by,
                }
                for r in rooms
            ],
            "total": len(rooms),
        }
    )


@router.get("/room/{room_id}", summary="Get room details")
async def get_room(
    room_id: str = Path(..., description="Room ID"),
    user: dict = Depends(require_auth),
) -> dict:
    """Get details for a specific room."""
    orchestrator = get_orchestrator()
    room = orchestrator.get_room(room_id)

    if not room:
        _err("ROOM_NOT_FOUND", f"Room {room_id} not found", 404)

    return _ok(
        data={
            "id": room.id,
            "name": room.name,
            "topic": room.topic,
            "room_type": room.room_type.value,
            "encryption": room.encryption.value,
            "member_count": len(room.members),
            "members": room.members,
            "protocols": [p.value for p in room.protocols],
            "bridged_to": {p.value: rid for p, rid in room.bridged_to.items()},
            "created_at": room.created_at,
            "created_by": room.created_by,
        }
    )


@router.get("/room/{room_id}/messages", summary="Get room messages")
async def get_messages(
    room_id: str = Path(..., description="Room ID"),
    limit: int = Query(default=50, ge=1, le=200, description="Max messages"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    user: dict = Depends(require_auth),
) -> dict:
    """Get messages from a room with proper decryption handling."""
    orchestrator = get_orchestrator()
    result = orchestrator.get_messages(room_id, limit=limit, offset=offset)

    if "error" in result:
        _err("ROOM_NOT_FOUND", result["error"], 404)

    return _ok(
        data={
            "room_id": room_id,
            "messages": result["messages"],
            "total": result["total"],
            "count": result["count"],
        }
    )


@router.post("/room/{room_id}/send", summary="Send message to room")
async def send_message(
    req: SendMessageRequest,
    room_id: str = Path(..., description="Room ID"),
    user: dict = Depends(require_auth),
) -> dict:
    """Send an encrypted message to a room.

    Encryption is automatic:
    - 1-on-1 DMs use Double Ratchet (Signal Protocol)
    - Group chats use Megolm (Matrix group ratchet)
    - Messages are bridged to all connected protocols automatically
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = await orchestrator.send_message(
        room_id=room_id,
        sender_id=user_id,
        content=req.content,
    )

    if result.get("status") == "failed":
        _err("MESSAGE_SEND_FAILED", result.get("error", "Unknown error"), 400)

    return _ok(
        data={
            "message_id": result["message_id"],
            "room_id": result["room_id"],
            "encrypted": result["encrypted"],
            "event_id": result.get("event_id"),
            "status": result["status"],
            "timestamp": result.get("timestamp"),
        }
    )


@router.post("/room/{room_id}/invite", summary="Invite user to room")
async def invite_user(
    req: InviteRequest,
    room_id: str = Path(..., description="Room ID"),
    user: dict = Depends(require_auth),
) -> dict:
    """Invite a user to a room."""
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = await orchestrator.invite_to_room(
        room_id=room_id,
        inviter_id=user_id,
        invitee_id=req.user_id,
    )

    if result.get("status") == "error":
        _err("INVITE_FAILED", result.get("error", "Unknown error"), 400)

    return _ok(
        data={
            "room_id": room_id,
            "invited_user": req.user_id,
            "status": result["status"],
        }
    )


@router.post("/keys/exchange", summary="Exchange encryption keys with peer")
async def exchange_keys(
    req: KeyExchangeRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Perform X3DH + Kyber-1024 key exchange with a peer.

    Establishes a Double Ratchet session for 1-on-1 E2E encryption.
    Returns safety numbers for out-of-band verification.
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = await orchestrator.exchange_keys(from_id=user_id, to_id=req.peer_id)

    if result.get("status") == "error":
        _err("KEY_EXCHANGE_FAILED", result.get("error", "Unknown error"), 400)

    return _ok(
        data={
            "session_id": result.get("session_id"),
            "safety_number": result.get("safety_number"),
            "post_quantum": result.get("post_quantum", False),
        }
    )


@router.post("/keys/rotate", summary="Rotate encryption keys")
async def rotate_keys(
    req: KeyRotateRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Rotate session keys for post-compromise security."""
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = await orchestrator.rotate_keys(user_id=user_id, peer_id=req.peer_id)

    if result.get("status") == "error":
        _err("KEY_ROTATION_FAILED", result.get("error", "Unknown error"), 400)

    return _ok(
        data={
            "status": result["status"],
            "new_bundle_available": result.get("new_bundle_available", False),
        }
    )


@router.get("/keys/fingerprint", summary="Get safety numbers")
async def get_fingerprint(
    peer_id: str = Query(..., description="Peer user ID"),
    user: dict = Depends(require_auth),
) -> dict:
    """Get safety numbers for verification.

    Returns both numeric fingerprint and QR code data for
    out-of-band verification (scan QR, compare digits).
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = orchestrator.get_fingerprint(user_id, peer_id)

    if not result:
        _err("NO_KEY_EXCHANGE", "No key exchange found with this peer. Exchange keys first.", 404)

    return _ok(data=result)


@router.post("/keys/verify", summary="Verify peer's safety number")
async def verify_fingerprint(
    req: KeyVerifyRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Verify a peer's safety number.

    Compare the numeric fingerprint out-of-band (in person, via
    another secure channel, or by scanning QR code).
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    result = orchestrator.verify_fingerprint(user_id, req.peer_id, req.fingerprint)

    return _ok(data=result)


@router.post("/bridge/enable", summary="Enable protocol bridge for a room")
async def enable_bridge(
    req: BridgeRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Enable a bridge for a protocol on a room.

    Supported protocols: nostr, farcaster, bluesky, activitypub,
    lens, xmtp, telegram, discord, irc, xmpp, signal, web3

    User sends message on Nostr → appears in Matrix room →
    bridges to all other connected protocols.
    """
    orchestrator = get_orchestrator()

    result = await orchestrator.enable_bridge(
        room_id=req.room_id,
        protocol=req.protocol,
    )

    return _ok(
        data={
            "room_id": req.room_id,
            "protocol": req.protocol.value,
            "status": result["status"],
            "enabled": result["enabled"],
        }
    )


@router.post("/bridge/disable", summary="Disable protocol bridge")
async def disable_bridge(
    req: BridgeRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Disable a bridge for a protocol."""
    orchestrator = get_orchestrator()

    result = await orchestrator.disable_bridge(
        room_id=req.room_id,
        protocol=req.protocol,
    )

    return _ok(
        data={
            "room_id": req.room_id,
            "protocol": req.protocol.value,
            "disabled": result["disabled"],
        }
    )


@router.get("/bridge/status", summary="Get bridge health status")
async def bridge_status(
    protocol: str | None = Query(default=None, description="Filter by protocol"),
    user: dict = Depends(require_auth),
) -> dict:
    """Get health status of all bridges or a specific bridge."""
    orchestrator = get_orchestrator()

    if protocol:
        proto = Protocol(protocol)
        result = await orchestrator.bridge_status(proto)
    else:
        result = await orchestrator.bridge_status()

    return _ok(data=result)


@router.post("/user/register", summary="Register user in chat system")
async def register_user(
    req: RegisterUserRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """Register a user in the standardized chat system.

    Creates their Matrix identity, Signal Protocol key bundle,
    and configures cross-protocol identities.
    """
    orchestrator = get_orchestrator()

    extra_protocols = None
    if req.protocols:
        extra_protocols = {Protocol(k): v for k, v in req.protocols.items()}

    chat_user = await orchestrator.register_user(
        user_id=req.user_id,
        display_name=req.display_name,
        extra_protocols=extra_protocols,
    )

    return _ok(
        data={
            "user_id": chat_user.id,
            "matrix_id": chat_user.matrix_id,
            "display_name": chat_user.display_name,
            "identities": {p.value: uid for p, uid in chat_user.identities.items()},
            "fingerprint": orchestrator.key_store.get_fingerprint(req.user_id),
        }
    )


@router.get("/user/{user_id}", summary="Get user info")
async def get_user(
    user_id: str = Path(..., description="User ID"),
    current_user: dict = Depends(require_auth),
) -> dict:
    """Get a user's chat profile."""
    orchestrator = get_orchestrator()
    chat_user = orchestrator.get_user(user_id)

    if not chat_user:
        _err("USER_NOT_FOUND", f"User {user_id} not found", 404)

    return _ok(
        data={
            "user_id": chat_user.id,
            "matrix_id": chat_user.matrix_id,
            "display_name": chat_user.display_name,
            "avatar_url": chat_user.avatar_url,
            "identities": {p.value: uid for p, uid in chat_user.identities.items()},
            "online": chat_user.online,
            "last_seen": chat_user.last_seen,
        }
    )


@router.get("/health", summary="Full system health check")
async def chat_health(
    user: dict | None = Depends(get_current_user),
) -> dict:
    """Full health check of the standardized chat system.

    Checks:
    - Matrix (Conduit) homeserver connectivity
    - Key store status
    - All bridge health
    - Protocol support
    """
    orchestrator = get_orchestrator()
    health = await orchestrator.health_check()

    http_status = 200 if health.get("status") == "healthy" else 503

    return _ok(
        data=health,
        meta={"http_status": http_status},
    )


@router.post("/check-protocols", summary="Check common protocols with another user")
async def check_common_protocols(
    peer_id: str = Query(..., description="Peer user ID to check against"),
    user: dict = Depends(require_auth),
) -> dict:
    """Check which protocols you have in common with another user.

    Useful before creating a DM to see what bridge plan would be used.
    """
    orchestrator = get_orchestrator()
    user_id = user.get("id", "unknown")

    current_user = orchestrator.get_user(user_id)
    peer = orchestrator.get_user(peer_id)

    if not current_user:
        _err("USER_NOT_REGISTERED", "You are not registered in the chat system", 400)
    if not peer:
        _err("PEER_NOT_FOUND", f"User {peer_id} not registered", 404)

    bridge_plan = orchestrator.protocol_selector.select_bridge_plan(current_user, peer)

    return _ok(
        data={
            "user_protocols": [p.value for p in orchestrator.protocol_selector.get_supported_protocols(current_user)],
            "peer_protocols": [p.value for p in orchestrator.protocol_selector.get_supported_protocols(peer)],
            "common_protocols": bridge_plan["common_protocols"],
            "bridge_plan": bridge_plan,
        }
    )
